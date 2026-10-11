#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forense PROFUNDA de una wallet — pasada única sobre todo el historial.

Por qué existe (medido): `wallet_forense.py` baja cada transacción DOS veces (una para los
flujos de SOL y otra para los tokens) y por eso, con wallets de 1.000+ tx, la corrida se
pasa de 7 minutos y muere por timeout sin dejar resultado. Acá cada tx se baja UNA vez, se
guarda lo derivado en un caché en disco y de ahí salen todas las métricas: se puede volver a
analizar (o cambiar los umbrales) sin tocar el RPC de nuevo.

Qué saca:
  · rastro del dinero: quién la fondeó, a quién le mandó, hermanas de fondeo
  · operaciones: compras/ventas por token, PnL realizado, hold mediano, win rate, ROI
  · ritmo: tx por hora, ventana de actividad, horas del día, quién paga los fees (clúster)
  · entrada: segundos entre la creación del par y su primera compra (sniper vs. discrecional)
  · vínculo con el dev: si el creador del token la fondeó o le mandó SOL (insider)
  · qué tiene abierto AHORA con su valor en USD
  · etiqueta final con el clasificador del proyecto (clasificar_wallets.etiquetar)

Uso:
  python3 forense_profunda.py <wallet>                  # pasada completa (usa caché si está)
  python3 forense_profunda.py <wallet> --max-tx 300     # limita el historial
  python3 forense_profunda.py <wallet> --refrescar      # ignora el caché y vuelve a bajar
  python3 forense_profunda.py <wallet> --json           # solo el JSON
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pump_analizador as PA          # noqa: E402
import wallet_tracker as WT           # noqa: E402
import wallet_forense as WF           # noqa: E402
import clasificar_wallets as CW       # noqa: E402

PAUSA = 0.06
INFRA = WF.IGNORAR
MS = 1_000_000_000


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def f_fecha(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m %H:%M") if ts else "?"


def f_num(x, d=3):
    return "?" if x is None else f"{x:.{d}f}"


# ------------------------------------------------------------------ firmas

def todas_las_firmas(wallet, max_tx=None):
    """Paginado completo de getSignaturesForAddress (de la más nueva a la más vieja)."""
    out, before, pag = [], None, 0
    while True:
        params = [wallet, {"limit": 1000}]
        if before:
            params[1]["before"] = before
        lote = WT.rpc("getSignaturesForAddress", params) or []
        if not lote:
            break
        out.extend(lote)
        pag += 1
        log(f"  firmas: página {pag} · acumulado {len(out)}")
        before = lote[-1]["signature"]
        if len(lote) < 1000 or (max_tx and len(out) >= max_tx):
            break
        if pag > 30:
            log("  corto el paginado en 30 páginas (seguridad)")
            break
        time.sleep(0.15)
    return out[:max_tx] if max_tx else out


# --------------------------------------------------------- descarga + caché

def _registro(tx, sig):
    """Lo mínimo que hace falta de una tx, en un formato chico para guardar en disco."""
    meta = tx.get("meta") or {}
    msg = tx["transaction"]["message"]
    keys = [(k.get("pubkey") if isinstance(k, dict) else k) for k in msg.get("accountKeys") or []]
    keys.extend(((meta.get("loadedAddresses") or {}).get("writable") or []))
    keys.extend(((meta.get("loadedAddresses") or {}).get("readonly") or []))
    pt = [[b.get("mint"), ((b.get("uiTokenAmount") or {}).get("uiAmount") or 0), b.get("owner")]
          for b in (meta.get("preTokenBalances") or [])]
    qt = [[b.get("mint"), ((b.get("uiTokenAmount") or {}).get("uiAmount") or 0), b.get("owner")]
          for b in (meta.get("postTokenBalances") or [])]
    return {"sig": sig, "slot": tx.get("slot"), "ts": tx.get("blockTime") or 0,
            "fee": meta.get("fee") or 0, "err": bool(tx.get("meta", {}).get("err")),
            "keys": keys, "pre": meta.get("preBalances") or [], "post": meta.get("postBalances") or [],
            "pt": pt, "qt": qt}


def _guardar_cache(cache_path, wallet, cache):
    tmp = cache_path + ".tmp"
    json.dump({"wallet": wallet,
               "guardado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tx": cache}, open(tmp, "w"))
    os.replace(tmp, cache_path)


def _tx(sig, intentos=1, pausa=0.0):
    """Baja una tx probando host por host hasta que alguno la devuelva.

    `WT.rpc` se queda con el primer `result`, aunque venga **null** (nodo que no tiene esa tx),
    así que acá se insiste contra los demás hosts antes de dar la tx por perdida. La primera
    pasada va con `intentos=1` (rápida); las que quedan dudosas se reintentan en un segundo lote.
    """
    params = [sig, {"maxSupportedTransactionVersion": WT.MAX_VERSION, "encoding": "jsonParsed"}]
    for i in range(intentos):
        for url in WT.RPCS_TX:
            try:
                cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                     "params": params}).encode()
                req = urllib.request.Request(url, data=cuerpo,
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "hermes-wallet-tracker/1.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    d = json.loads(r.read().decode())
                if d.get("result"):
                    return d["result"]
            except Exception:
                continue
        if i < intentos - 1:
            time.sleep(pausa or 1.0)
    return None


def descargar(wallet, sigs, cache_path, refrescar=False):
    """Baja cada tx una sola vez y guarda el registro derivado en el caché.

    Dos pasadas: la primera rápida (1 vuelta por host) y una segunda sólo para las que quedaron
    sin datos — el RPC público deja caer tx por rate limit y sin el reintento el análisis se sesga
    (medido: una corrida de 1.200 tx dejó 326). Guarda cada 200 tx: una corrida cortada no pierde
    lo hecho y `--desde-cache` permite analizar resultados parciales sin tocar el RPC.
    """
    cache = {}
    if os.path.exists(cache_path) and not refrescar:
        try:
            cache = json.load(open(cache_path)).get("tx", {})
        except Exception:
            cache = {}
    faltan = [s for s in sigs if s["signature"] not in cache]
    log(f"historial: {len(sigs)} firmas · en caché {len(sigs) - len(faltan)} · a bajar {len(faltan)}")
    t0 = time.time()
    dudosos = []
    for i, s in enumerate(faltan, 1):
        tx = _tx(s["signature"], intentos=1)
        if tx:
            cache[s["signature"]] = _registro(tx, s["signature"])
        else:
            dudosos.append(s)
        time.sleep(PAUSA)
        if i % 100 == 0:
            v = i / max(time.time() - t0, 0.1)
            log(f"  1a pasada {i}/{len(faltan)} tx · {v:.1f} tx/s · sin datos {len(dudosos)} · "
                f"quedan ~{(len(faltan) - i) / max(v, 0.1) / 60:.1f} min")
        if i % 200 == 0:
            _guardar_cache(cache_path, wallet, cache)
    if dudosos:
        log(f"  2a pasada: reintentando {len(dudosos)} tx sin datos…")
        fallos = 0
        for j, s in enumerate(dudosos, 1):
            tx = _tx(s["signature"], intentos=2, pausa=0.5)
            if tx:
                cache[s["signature"]] = _registro(tx, s["signature"])
            else:
                fallos += 1
            time.sleep(PAUSA)
            if j % 100 == 0:
                log(f"  2a pasada {j}/{len(dudosos)} · todavía sin datos {fallos}")
                _guardar_cache(cache_path, wallet, cache)
    else:
        fallos = 0
    if faltan:
        _guardar_cache(cache_path, wallet, cache)
    log(f"  descarga lista: {len(cache)} tx en caché · {fallos} sin respuesta del RPC")
    return cache


# ------------------------------------------------------------------ análisis

def parsear_reg(reg, wallet):
    """Misma convención que WT.parsear_tx, pero desde el registro cacheado."""
    if reg.get("err"):
        return None
    keys, pre, post = reg["keys"], reg["pre"], reg["post"]
    idx = None
    for i, k in enumerate(keys):
        if k == wallet:
            idx = i
    sol = 0.0
    if idx is not None and idx < len(pre) and idx < len(post):
        sol = (post[idx] - pre[idx]) / 1e9

    def saldos(lst):
        out = {}
        for mint, amt, owner in lst:
            if owner == wallet:
                out[mint] = out.get(mint, 0.0) + float(amt or 0)
        return out

    antes, despues = saldos(reg["pt"]), saldos(reg["qt"])
    deltas = {}
    for m in set(antes) | set(despues):
        d = despues.get(m, 0.0) - antes.get(m, 0.0)
        if abs(d) > WT.MIN_TOK_AIRE:
            deltas[m] = d
    if not deltas:
        if abs(sol) >= WT.MIN_SOL_MOV:
            return {"accion": "sol", "mint": None, "cantidad": None, "sol": sol}
        return None
    mint, delta = max(deltas.items(), key=lambda kv: abs(kv[1]))
    if abs(sol) < WT.MIN_SOL_MOV:
        return None                       # polvo / airdrop / movimiento interno
    if delta > 0 and sol < -WT.MIN_SOL_MOV:
        accion = "compra"
    elif delta < 0 and sol > WT.MIN_SOL_MOV:
        accion = "venta"
    elif delta > 0:
        accion = "recibe"
    else:
        accion = "envia"
    return {"accion": accion, "mint": mint, "cantidad": abs(delta), "sol": sol}


def analizar(wallet, cache, sigs):
    """Todo lo que se puede sacar de la pasada única."""
    recibido, enviado = {}, {}
    tokens = {}
    pagadores, cofirmantes = {}, {}
    eventos = []                      # (ts, accion, mint, sol)
    n_err = 0
    for s in sigs:
        reg = cache.get(s["signature"])
        if not reg:
            continue
        if reg.get("err"):
            n_err += 1
            continue
        ts = reg.get("ts") or s.get("blockTime") or 0
        # quién paga los fees y quién más firma sus tx (señal de clúster/bundle)
        pagador = reg["keys"][0] if reg["keys"] else None
        if pagador and pagador != wallet:
            pagadores[pagador] = pagadores.get(pagador, 0) + 1
        # --- flujos de SOL con las otras cuentas
        pre, post = reg["pre"], reg["post"]
        for i, k in enumerate(reg["keys"]):
            if k == wallet or k in INFRA or i >= len(pre) or i >= len(post):
                continue
            d = (post[i] - pre[i]) / 1e9
            if abs(d) < WF.MIN_SOL:
                continue
            reg_d = recibido if d > 0 else enviado
            e = reg_d.setdefault(k, {"sol": 0.0, "tx": 0, "primero": 0, "ultimo": 0})
            e["sol"] += abs(d)
            e["tx"] += 1
            e["primero"] = min(e["primero"] or 9e18, ts)
            e["ultimo"] = max(e["ultimo"], ts)
        # --- tokens
        ev = parsear_reg(reg, wallet)
        if not ev:
            continue
        eventos.append({"ts": ts, "accion": ev["accion"], "mint": ev["mint"], "sol": ev["sol"]})
        if not ev["mint"]:
            continue
        d = tokens.setdefault(ev["mint"], {"compras": 0, "ventas": 0, "sol_in": 0.0, "sol_out": 0.0,
                                           "tok_in": 0.0, "tok_out": 0.0, "recibidos": 0,
                                           "ts": [], "primera_compra": None, "ultima_venta": None,
                                           "slots": []})
        d["ts"].append(ts)
        d["slots"].append(reg.get("slot"))
        if ev["accion"] == "compra":
            d["compras"] += 1
            d["sol_in"] += abs(ev["sol"])
            d["tok_in"] += ev["cantidad"] or 0
            d["primera_compra"] = min(d["primera_compra"] or 9e18, ts)
        elif ev["accion"] == "venta":
            d["ventas"] += 1
            d["sol_out"] += abs(ev["sol"])
            d["tok_out"] += ev["cantidad"] or 0
            d["ultima_venta"] = max(d["ultima_venta"] or 0, ts)
        elif ev["accion"] == "recibe":
            d["recibidos"] += 1
    return {"recibido": recibido, "enviado": enviado, "tokens": tokens,
            "pagadores": pagadores, "eventos": eventos, "n_err": n_err}


def posiciones(tokens, tenencias):
    """Cierra cada token en cerrada/abierta con PnL realizado y hold."""
    cerradas, abiertas = [], []
    for mint, d in tokens.items():
        if not (d["compras"] or d["ventas"]):
            continue
        pnl = d["sol_out"] - d["sol_in"]
        hold = None
        if d["primera_compra"] and d["ultima_venta"]:
            hold = (d["ultima_venta"] - d["primera_compra"]) / 60.0
        elif d["primera_compra"] and d["compras"]:
            hold = (max(d["ts"]) - d["primera_compra"]) / 60.0 if d["ts"] else None
        fila = {"mint": mint, "pnl": pnl, "sol_in": d["sol_in"], "sol_out": d["sol_out"],
                "roi": (pnl / d["sol_in"] * 100) if d["sol_in"] > 0 else None,
                "compras": d["compras"], "ventas": d["ventas"], "recibidos": d["recibidos"],
                "hold_min": hold, "ts": d["ts"], "slots": d["slots"][:4],
                "primera_compra": d["primera_compra"], "ultima_venta": d["ultima_venta"]}
        if tenencias.get(mint, 0) > 0 or d["ventas"] == 0:
            abiertas.append(fila)
        else:
            cerradas.append(fila)
    cerradas.sort(key=lambda f: -f["pnl"])
    abiertas.sort(key=lambda f: -f["sol_in"])
    return cerradas, abiertas


def metricas(cerradas, abiertas, verts_sol):
    """Mismas cuentas que auditor_wallets.auditar_wallet, pero sobre TODO el historial."""
    if not cerradas:
        return {"cerradas": 0, "abiertas": len(abiertas), "pnl_neto_sol": 0.0, "win_rate": None,
                "roi_total": None, "pct_sin_compra": 0, "min_tenencia_mediano": None,
                "concentracion_pnl": None, "t_stat": None, "copiable": "?", "invertido_sol": 0.0,
                "mejor": None, "peor": None, "roi_mediano": None, "payoff": None,
                "posiciones_sin_compra": 0}
    sin_compra = [c for c in cerradas if c["compras"] == 0 and c["ventas"] > 0]
    ganancias = [c["pnl"] for c in cerradas if c["pnl"] > 0]
    perd_abs = [abs(c["pnl"]) for c in cerradas if c["pnl"] < 0]
    pnl_neto = sum(c["pnl"] for c in cerradas)
    # ¿el resultado sobrevive si se sacan los 3 mejores trades? (anti-suerte de cola)
    orden = sorted((c["pnl"] for c in cerradas), reverse=True)
    pnl_sin_top3 = sum(orden[3:]) if len(orden) > 3 else sum(orden)
    invertido = sum(c["sol_in"] for c in cerradas)
    rets = [c["roi"] for c in cerradas if c["roi"] is not None]
    t_stat = None
    if len(rets) >= 4:
        desv = statistics.pstdev(rets)
        if desv > 0:
            t_stat = round(statistics.mean(rets) / (desv / (len(rets) ** 0.5)), 2)
    holds = [c["hold_min"] for c in cerradas if c["hold_min"]]
    hold = statistics.median(holds) if holds else None
    if hold is None:
        copiable = "?"
    elif hold < 2:
        copiable = "NO (hold <2 min: scalper/sniper, cuando reacciones ya vendió)"
    elif hold < 20:
        copiable = "límite (2-20 min)"
    else:
        copiable = "sí (hold humano)"
    return {"cerradas": len(cerradas), "abiertas": len(abiertas),
            "pnl_neto_sol": round(pnl_neto, 4), "invertido_sol": round(invertido, 4),
            "pnl_sin_top3_sol": round(pnl_sin_top3, 4),
            "roi_total": round(pnl_neto / invertido * 100, 1) if invertido > 0 else None,
            "win_rate": round(len(ganancias) / len(cerradas) * 100, 1),
            "mejor": round(max(c["pnl"] for c in cerradas), 4),
            "peor": round(min(c["pnl"] for c in cerradas), 4),
            "roi_mediano": round(statistics.median(rets), 1) if rets else None,
            "min_tenencia_mediano": round(hold, 1) if hold is not None else None,
            "pct_sin_compra": round(len(sin_compra) / len(cerradas) * 100, 0),
            "posiciones_sin_compra": len(sin_compra),
            "concentracion_pnl": round(max(ganancias) / sum(ganancias) * 100, 1) if ganancias else None,
            "payoff": round(statistics.mean(ganancias) / statistics.mean(perd_abs), 2)
            if ganancias and perd_abs else None,
            "t_stat": t_stat, "copiable": copiable, "verts_finales_sol": verts_sol}


def mercado_de(mints, tope=12):
    """Símbolo, liquidez, mcap, estado y antigüedad del par para los tokens que operó."""
    out = {}
    for m in mints[:tope]:
        try:
            p = PA.dexscreener(m)
        except Exception:
            p = None
        if not p:
            out[m] = {"symbol": m[:6], "muerto": True}
            continue
        out[m] = {"symbol": (p.get("baseToken") or {}).get("symbol") or m[:6],
                  "mcap": p.get("marketCap") or p.get("fdv"),
                  "liq": (p.get("liquidity") or {}).get("usd"),
                  "vol24": (p.get("volume") or {}).get("h24"),
                  "cambio24": (p.get("priceChange") or {}).get("h24"),
                  "creado": (p.get("pairCreatedAt") or 0) / 1000 or None,
                  "muerto": ((p.get("liquidity") or {}).get("usd") or 0) < 500}
    return out


def vinculo_con_el_dev(mints, recibido, enviado):
    """¿El creador de alguno de los tokens que compró la fondeó o le mandó SOL?"""
    vinculos = []
    for m in mints[:6]:
        rc = PA.rugcheck(m)
        if not rc:
            continue
        creador = rc.get("creator") or (rc.get("token") or {}).get("creator")
        if not creador:
            continue
        if creador in recibido:
            vinculos.append({"mint": m, "creador": creador, "tipo": "LA FONDEÓ",
                             "sol": round(recibido[creador]["sol"], 3)})
        elif creador in enviado:
            vinculos.append({"mint": m, "creador": creador, "tipo": "le mandó SOL al creador",
                             "sol": round(enviado[creador]["sol"], 3)})
        time.sleep(0.2)
    return vinculos


def main():
    ap = argparse.ArgumentParser(description="Forense profunda de una wallet (historial completo)")
    ap.add_argument("wallet")
    ap.add_argument("--max-tx", type=int, default=1500)
    ap.add_argument("--refrescar", action="store_true")
    ap.add_argument("--desde-cache", action="store_true",
                    help="analiza SOLO el caché en disco: cero RPC (para mirar resultados parciales)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    w = a.wallet
    cache_path = os.path.join(AQUI, f"cache_tx_{w[:8]}.json")

    log(f"FORENSE PROFUNDA de {w}")
    sigs = todas_las_firmas(w, a.max_tx)
    if not sigs:
        log("la dirección no tiene firmas")
        return 1
    if a.desde_cache:
        cache = json.load(open(cache_path)).get("tx", {}) if os.path.exists(cache_path) else {}
        log(f"modo caché: {len(cache)} tx en disco (de {len(sigs)} firmas)")
        sigs = [{"signature": k, "blockTime": v.get("ts")} for k, v in cache.items()]
    else:
        cache = descargar(w, sigs, cache_path, a.refrescar)
    log("analizando…")
    d = analizar(w, cache, sigs)

    saldo = 0 if a.desde_cache else ((WT.rpc("getBalance", [w]) or {}).get("value") or 0)
    tenencias = {} if a.desde_cache else WF.tenencias_actuales(w)
    cerradas, abiertas = posiciones(d["tokens"], tenencias)
    met = metricas(cerradas, abiertas, saldo / 1e9)

    ts = [e["ts"] for e in d["eventos"] if e["ts"]] + [s.get("blockTime") or 0 for s in sigs]
    ts = [t for t in ts if t]
    primera, ultima = (min(ts), max(ts)) if ts else (0, 0)
    horas = (ultima - primera) / 3600 if primera else 0
    swaps = [e for e in d["eventos"] if e["accion"] in ("compra", "venta")]
    met["plataforma"] = {
        "tx_totales": len(sigs), "tx_con_error": d["n_err"],
        "swaps_detectados": len(swaps),
        "primera_tx": primera, "ultima_tx": ultima, "horas_de_vida": round(horas, 1),
        "tx_por_hora": round(len(sigs) / horas, 1) if horas > 0 else None,
        "mints_operados": len(d["tokens"]),
        "fees_pagados_sol": round(sum((cache.get(s["signature"]) or {}).get("fee", 0)
                                      for s in sigs) / 1e9, 4),
        "firmas_por_terceros": sum(d["pagadores"].values()),
        "pagadores_distintos": len(d["pagadores"]),
    }

    # --- símbolos y estado de mercado de los tokens operados
    mints_orden = [c["mint"] for c in cerradas] + [c["mint"] for c in abiertas]
    mercado = mercado_de(mints_orden, tope=14)
    for f in cerradas + abiertas:
        f["symbol"] = mercado.get(f["mint"], {}).get("symbol") or f["mint"][:6]
        f["mcap"] = mercado.get(f["mint"], {}).get("mcap")
        f["creado_par"] = mercado.get(f["mint"], {}).get("creado")
        if f.get("creado_par") and f.get("primera_compra"):
            f["entrada_seg"] = int(f["primera_compra"] - f["creado_par"])
        else:
            f["entrada_seg"] = None

    vinculos = [] if a.desde_cache else vinculo_con_el_dev(mints_orden, d["recibido"], d["enviado"])

    et = CW.etiquetar(met)
    res = {"wallet": w, "saldo_sol": round(saldo / 1e9, 4), "etiqueta": et, "metricas": met,
           "plataforma": met["plataforma"], "recibido": d["recibido"], "enviado": d["enviado"],
           "pagadores": d["pagadores"], "cerradas": cerradas, "abiertas": abiertas,
           "tenencias": tenencias, "mercado": mercado, "vinculos_dev": vinculos,
           "generado": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    json.dump(res, open(os.path.join(AQUI, f"forense_{w[:8]}.json"), "w"),
              ensure_ascii=False, indent=2, default=str)
    log(f"guardado en forense_{w[:8]}.json")
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        print(texto(res))
    return 0


def texto(r):
    p, m, pl = r["plataforma"], r["metricas"], r["plataforma"]
    et = r["etiqueta"]
    l = [f"FORENSE PROFUNDA · {r['wallet']}",
         f"  etiqueta: {et['emoji']} {et['tipo']} — {et['razon']}",
         f"  saldo {r['saldo_sol']} SOL · {pl['tx_totales']} tx ({pl['tx_con_error']} con error) "
         f"· {pl['swaps_detectados']} swaps · {pl['mints_operados']} tokens",
         f"  vida: {f_fecha(pl['primera_tx'])} → {f_fecha(pl['ultima_tx'])} "
         f"= {pl['horas_de_vida']} h · ritmo {pl['tx_por_hora']} tx/h",
         f"  fees pagados {pl['fees_pagados_sol']} SOL · "
         f"{pl['firmas_por_terceros']} tx con firmas de terceros ({pl['pagadores_distintos']} pagadores)",
         "",
         f"  cerradas {m['cerradas']} · abiertas {m['abiertas']} · PnL {m['pnl_neto_sol']:+.3f} SOL "
         f"sobre {m['invertido_sol']:.3f} invertidos · win rate {m['win_rate']}% · ROI total {m['roi_total']}%",
         f"  hold mediano {m['min_tenencia_mediano']} min · sin compra {m['pct_sin_compra']}% · "
         f"concentración {m['concentracion_pnl']}% · payoff {m['payoff']} · t {m['t_stat']}"]
    if r["cerradas"]:
        l += ["", "TOKENS CERRADOS (por resultado)"]
        for c in r["cerradas"][:12]:
            l.append(f"  {c['pnl']:>+9.4f} SOL · {(c['roi'] if c['roi'] is not None else 0):>7.1f}% · "
                     f"hold {f_num(c['hold_min'], 1)}min · entrada "
                     f"{c['entrada_seg'] if c['entrada_seg'] is not None else '?'}s · {c['symbol']}")
    if r["abiertas"]:
        l += ["", "ABIERTAS AHORA"]
        for c in r["abiertas"][:8]:
            l.append(f"  metió {c['sol_in']:.3f} SOL · {c['compras']}c/{c['ventas']}v · "
                     f"{c['symbol']} · mcap {c['mcap']}")
    if r["pagadores"]:
        l += ["", "PAGADORES DE FEES (clúster)"]
        for k, v in sorted(r["pagadores"].items(), key=lambda kv: -kv[1])[:5]:
            l.append(f"  {v} tx · {k}")
    if r["vinculos_dev"]:
        l += ["", "VINCULO CON EL DEV"]
        for v in r["vinculos_dev"]:
            l.append(f"  {v['tipo']} · {v['sol']} SOL · token {v['mint'][:10]}… · {v['creador']}")
    l += ["", "DE DÓNDE VINO LA PLATA"]
    for k, v in sorted(r["recibido"].items(), key=lambda kv: -kv[1]["sol"])[:6]:
        l.append(f"  {v['sol']:>8.3f} SOL · {v['tx']} tx · {f_fecha(v['primero'])} · {k}")
    l += ["", "A DÓNDE FUE"]
    for k, v in sorted(r["enviado"].items(), key=lambda kv: -kv[1]["sol"])[:6]:
        l.append(f"  {v['sol']:>8.3f} SOL · {v['tx']} tx · {f_fecha(v['primero'])} · {k}")
    l += ["", "nota: PnL reconstruido de la cadena; no incluye priority fees/tips."]
    return "\n".join(l)


if __name__ == "__main__":
    sys.exit(main())
