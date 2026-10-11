#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditoría de wallets de Solana — busca las que son realmente buenas (no las que lo parecen).

Método (todo desde datos públicos, sin API keys):
  1. Arma el universo de "tokens que funcionaron": memecoins con market cap alto y edad reciente.
  2. Saca los holders de cada uno (RugCheck, descontando curva/pool/creador) y cuenta en cuántos
     ganadores aparece cada wallet.
  3. Para cada candidata, reconstruye su historial on-chain: compras, ventas y transferencias por
     token, y calcula PnL realizado, win rate, ROI mediano, tiempo de tenencia y consistencia.
  4. Descarta infraestructura, wallets de un solo golpe y wallets que reciben tokens regalados
     (patrón de bundle/insider) en vez de comprarlos.

Uso:
  python3 auditor_wallets.py --candidatos 40 --auditar 12 --tx 80
  python3 auditor_wallets.py --wallet <direccion> --tx 200     # auditar una sola
  python3 auditor_wallets.py --json
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pump_analizador as PA          # noqa: E402
import wallet_tracker as WT           # noqa: E402

SALIDA = os.path.join(AQUI, "auditoria_wallets.json")
PAUSA = 0.12                          # respiro entre llamadas al RPC público
INFRA_TIPOS = ("AMM", "LOCKER", "CREATOR", "POOL", "BURN", "FEE")
# nodos que NO son wallets de personas: no sirven para agrupar (CEX, programas, pools)
AU_IGNORAR = {
    "11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb", "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "So11111111111111111111111111111111111111112",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
}


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


# ------------------------------------------------------------ 1. universo

def tokens_ganadores(max_edad_dias=14.0, mc_min=150_000, tope=40):
    """Memecoins recientes que ya se movieron fuerte (las que un buen wallet habría tocado)."""
    vistos = {}
    fuentes = [
        f"{PA.PUMP}/coins?limit=200&sort=ath_market_cap&order=DESC&includeNsfw=true",
        f"{PA.PUMP}/coins?limit=200&sort=market_cap&order=DESC&includeNsfw=true",
        f"{PA.PUMP}/coins?limit=200&sort=last_trade_timestamp&order=DESC&includeNsfw=true",
    ]
    for url in fuentes:
        for c in (PA.http_json(url) or []):
            if c.get("mint"):
                vistos.setdefault(c["mint"], c)
    ganadores = []
    for c in vistos.values():
        edad = PA.edad_min(c.get("created_timestamp"))
        mc = c.get("usd_market_cap") or 0
        ath = c.get("ath_market_cap") or 0
        if edad is None or edad > max_edad_dias * 1440 or mc < mc_min:
            continue
        ganadores.append(c)
    ganadores.sort(key=lambda c: -(c.get("usd_market_cap") or 0))
    return ganadores[:tope]


# --------------------------------------------------- 2. candidatas (holders)

def candidatas_por_holders(ganadores):
    """Cuenta en cuántos tokens ganadores aparece cada wallet como holder (sin infraestructura)."""
    cuenta = {}
    for i, c in enumerate(ganadores, 1):
        rc = PA.rugcheck(c["mint"])
        if not rc:
            continue
        conocidas = rc.get("knownAccounts") or {}
        for h in (rc.get("topHolders") or [])[:20]:
            owner = h.get("owner")
            if not owner or (h.get("pct") or 0) <= 0.02:
                continue
            info = conocidas.get(owner) or {}
            if (info.get("type") or "").upper() in INFRA_TIPOS:
                continue
            if h.get("insider"):
                continue
            d = cuenta.setdefault(owner, {"veces": 0, "tokens": [], "pct_max": 0.0})
            d["veces"] += 1
            d["tokens"].append(c.get("symbol") or "?")
            d["pct_max"] = max(d["pct_max"], h.get("pct") or 0)
        if i % 10 == 0:
            log(f"  holders revisados: {i}/{len(ganadores)}")
        time.sleep(PAUSA)
    return cuenta


# ------------------------------------------------ 3. auditoría de una wallet

def auditar_wallet(direccion, max_tx=80, verbose=False):
    """Reconstruye el historial reciente de la wallet y saca sus métricas de trading."""
    sigs = WT.rpc("getSignaturesForAddress", [direccion, {"limit": max_tx}]) or []
    if not sigs:
        return None
    eventos = []
    fondeos = {}                      # quién le mandó SOL (para saber de dónde salió la plata)
    for s in sigs:
        if s.get("err"):
            continue
        tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                       "encoding": "jsonParsed"}])
        ev = WT.parsear_tx(tx, direccion) if tx else None
        if ev:
            ev["ts"] = s.get("blockTime") or 0
            ev["sig"] = s["signature"]
            eventos.append(ev)
        if tx and tx.get("meta"):
            meta = tx["meta"]
            pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
            keys = WT.cuentas_tx(tx)
            for i, k in enumerate(keys):
                if k == direccion or k in AU_IGNORAR or i >= len(pre) or i >= len(post):
                    continue
                d = (post[i] - pre[i]) / 1e9
                if d >= 0.05:
                    e = fondeos.setdefault(k, {"sol": 0.0, "ts": 0})
                    e["sol"] += d
                    e["ts"] = min(e["ts"] or 9e9, s.get("blockTime") or 0)
        time.sleep(PAUSA)
    if not eventos:
        return None

    # tenencias actuales (para saber si una posición sigue abierta)
    tenencias = {}
    cuentas = WT.rpc("getTokenAccountsByOwner",
                     [direccion, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                      {"encoding": "jsonParsed"}]) or {}
    for acc in (cuentas.get("value") or []):
        try:
            info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            if float(info.get("uiAmount") or 0) > 0:
                tenencias[acc["account"]["data"]["parsed"]["info"]["mint"]] = float(info["uiAmount"])
        except Exception:
            continue

    # flujo por token
    por_token = {}
    for ev in eventos:
        if not ev.get("mint"):
            continue
        d = por_token.setdefault(ev["mint"], {"compras": 0, "ventas": 0, "sol_comprado": 0.0,
                                              "sol_vendido": 0.0, "tokens_comprados": 0.0,
                                              "tokens_vendidos": 0.0, "ts": [], "regalados": 0})
        d["ts"].append(ev["ts"])
        if ev["accion"] == "compra":
            d["compras"] += 1
            d["sol_comprado"] += abs(ev["sol"])
            d["tokens_comprados"] += ev["cantidad"] or 0
        elif ev["accion"] == "venta":
            d["ventas"] += 1
            d["sol_vendido"] += abs(ev["sol"])
            d["tokens_vendidos"] += ev["cantidad"] or 0
        elif ev["accion"] == "recibe":
            d["regalados"] += 1

    cerradas, abiertas = [], []
    for mint, d in por_token.items():
        if d["compras"] == 0 and d["ventas"] == 0:
            continue
        pnl = d["sol_vendido"] - d["sol_comprado"]          # realizado (aprox)
        roi = (pnl / d["sol_comprado"] * 100) if d["sol_comprado"] > 0 else None
        dur = (max(d["ts"]) - min(d["ts"])) / 60.0 if d["ts"] else None
        reg = {"mint": mint, "pnl": pnl, "roi": roi, "compras": d["compras"], "ventas": d["ventas"],
               "sol_in": d["sol_comprado"], "sol_out": d["sol_vendido"], "min": dur,
               "regalados": d["regalados"],
               "horas": sorted({datetime.fromtimestamp(t, timezone.utc).hour for t in d["ts"] if t})}
        if tenencias.get(mint, 0) > 0 or d["ventas"] == 0:
            abiertas.append(reg)
        else:
            cerradas.append(reg)

    if not cerradas:
        return {"wallet": direccion, "cerradas": 0, "nota": "sin posiciones cerradas en la ventana"}
    sin_compra = [c for c in cerradas if c["compras"] == 0 and c["ventas"] > 0]
    ganancias = [c["pnl"] for c in cerradas if c["pnl"] > 0]
    perdidas = [c["pnl"] for c in cerradas if c["pnl"] <= 0]
    # --- perfil de la wallet según los umbrales que usa la comunidad (investigación 2026-10) ---
    pos = sum(ganancias)
    concentracion = round(max(ganancias) / pos * 100, 1) if pos > 0 and ganancias else None
    perd_abs = [abs(c["pnl"]) for c in cerradas if c["pnl"] < 0]
    payoff = round(statistics.mean(ganancias) / statistics.mean(perd_abs), 2) if ganancias and perd_abs else None
    rets = [c["roi"] for c in cerradas if c["roi"] is not None]
    t_stat = None
    if len(rets) >= 4:
        desv = statistics.pstdev(rets)
        if desv > 0:
            t_stat = round(statistics.mean(rets) / (desv / (len(rets) ** 0.5)), 2)
    horas_dia = len({h for c in cerradas for h in c.get("horas", [])})
    hold = statistics.median([c["min"] for c in cerradas if c["min"]]) if any(c["min"] for c in cerradas) else None
    if hold is None:
        copiable = "?"
    elif hold < 2:
        copiable = "NO (hold <2 min: scalper/sniper, cuando reacciones ya vendió)"
    elif hold < 20:
        copiable = "límite (2-20 min)"
    else:
        copiable = "sí (hold humano)"
    # fondeo: de dónde salió la plata y si el historial es posterior al fondeo
    top_fondeo = sorted(fondeos.items(), key=lambda kv: -kv[1]["sol"])[:3]
    primera = min([e["ts"] for e in eventos if e.get("ts")] or [0])
    fondeo_ts = min([f["ts"] for _, f in top_fondeo if f.get("ts")] or [0])
    edad_hist = (time.time() - primera) / 3600 if primera else None
    fondeo_fresco = bool(primera and fondeo_ts and abs(fondeo_ts - primera) < 3600 and (edad_hist or 0) < 168)
    pnl_neto = sum(c["pnl"] for c in cerradas)
    invertido = sum(c["sol_in"] for c in cerradas)
    rois = [c["roi"] for c in cerradas if c["roi"] is not None]
    regalados_tot = sum(c["regalados"] for c in cerradas)
    compras_tot = sum(c["compras"] for c in cerradas)
    return {
        "wallet": direccion,
        "tx_vistas": len(eventos),
        "tokens_operados": len(por_token),
        "cerradas": len(cerradas),
        "abiertas": len(abiertas),
        "pnl_neto_sol": round(pnl_neto, 4),
        "invertido_sol": round(invertido, 4),
        "roi_total": round(pnl_neto / invertido * 100, 1) if invertido > 0 else None,
        "win_rate": round(len(ganancias) / len(cerradas) * 100, 1),
        "mejor": round(max(c["pnl"] for c in cerradas), 4),
        "peor": round(min(c["pnl"] for c in cerradas), 4),
        "roi_mediano": round(statistics.median(rois), 1) if rois else None,
        "min_tenencia_mediano": round(statistics.median([c["min"] for c in cerradas if c["min"]]), 1)
        if any(c["min"] for c in cerradas) else None,
        "pct_regalados": round(regalados_tot / (regalados_tot + compras_tot) * 100, 0)
        if (regalados_tot + compras_tot) else 0,
        "posiciones_sin_compra": len(sin_compra),
        "pct_sin_compra": round(len(sin_compra) / len(cerradas) * 100, 0),
        "alerta": ("recibió tokens en vez de comprarlos (patrón bundle/insider)"
                   if len(sin_compra) > len(cerradas) * 0.3 else ""),
        "concentracion_pnl": concentracion,
        "payoff": payoff,
        "t_stat": t_stat,
        "horas_del_dia": horas_dia,
        "copiable": copiable,
        "fondeo_principal": [{"wallet": w, "sol": round(f["sol"], 3)} for w, f in top_fondeo],
        "fondeo_fresco": fondeo_fresco,
        "detalle_cerradas": sorted(cerradas, key=lambda c: -c["pnl"])[:8],
    }


def puntaje(a):
    """Puntaje compuesto: lo que gana, con qué consistencia y con cuánta muestra."""
    if not a or not a.get("cerradas"):
        return -999
    p = 0.0
    p += min(a["pnl_neto_sol"], 200) * 1.0              # gana de verdad (tope para no aplastar)
    p += (a["win_rate"] - 40) * 1.2                     # aciertos sobre el 40% de equilibrio
    p += min(a["cerradas"], 25) * 1.5                   # muestra suficiente
    if a["roi_total"] is not None:
        p += min(max(a["roi_total"], -100), 300) * 0.15
    p -= a["pct_regalados"] * 0.35                      # recibir tokens regalados = bundle/insider
    p -= a.get("pct_sin_compra", 0) * 0.60              # vender sin haber comprado = bundle/insider
    if a.get("roi_total") is None:                      # nunca compró: no es un trader, es mano pasiva
        p -= 20
    # --- filtros que salieron de la investigación (umbrales de la comunidad) ---
    if (a.get("win_rate") or 0) > 85:
        p -= 25                     # 85%+ es señal de alarma: insider, sniper o farmer
    if (a.get("concentracion_pnl") or 0) > 50:
        p -= 20                     # >50% del resultado en UN solo trade = lotería
    if a.get("cerradas", 0) >= 4 and (a.get("t_stat") is None or a["t_stat"] < 1.645):
        p -= 20                     # sin significancia estadística no es edge
    if a.get("cerradas", 0) < 30:
        p -= 15                     # muestra chica: no se puede concluir
    if str(a.get("copiable", "")).startswith("NO"):
        p -= 30                     # hold <2 min: no es copiable, es bot
    if a.get("fondeo_fresco"):
        p -= 15                     # historia que empieza con el fondeo = sin track record
    return round(p, 1)


def main():
    ap = argparse.ArgumentParser(description="Auditoría de wallets: cuáles son realmente buenas")
    ap.add_argument("--candidatos", type=int, default=40, help="cuántos tokens ganadores revisar")
    ap.add_argument("--auditar", type=int, default=12, help="cuántas wallets auditar a fondo")
    ap.add_argument("--tx", type=int, default=80, help="transacciones a revisar por wallet")
    ap.add_argument("--wallet", help="auditar una sola dirección")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--agregar-top", type=int, default=0,
                    help="agrega las N mejores al tracker de wallets (etiqueta 'auditada')")
    a = ap.parse_args()

    if a.wallet:
        r = auditar_wallet(a.wallet, a.tx, True)
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else _linea(r, 1))
        return 0

    log("1/3 armando universo de tokens que funcionaron…")
    ganadores = tokens_ganadores(tope=a.candidatos)
    log(f"    {len(ganadores)} tokens recientes con mcap ≥150k")
    if not ganadores:
        log("no encontré ganadores; probá subir --candidatos")
        return 1

    log("2/3 buscando candidatas en los holders…")
    cuenta = candidatas_por_holders(ganadores)
    orden = sorted(cuenta.items(), key=lambda kv: (-kv[1]["veces"], -kv[1]["pct_max"]))
    log(f"    {len(orden)} wallets candidatas (aparecen en el top 20 de algún ganador)")
    a_auditar = [w for w, d in orden if d["veces"] >= 2][:a.auditar]
    if len(a_auditar) < a.auditar:                       # completar con las de 1 sola aparición
        a_auditar += [w for w, d in orden if d["veces"] == 1][:a.auditar - len(a_auditar)]
    if not a_auditar:
        log("sin candidatas con esos filtros")
        return 1
    log(f"    auditando {len(a_auditar)} wallets · {a.tx} tx cada una (puede tardar)")

    log("3/3 auditando historial on-chain…")
    resultados = []
    for i, w in enumerate(a_auditar, 1):
        try:
            r = auditar_wallet(w, a.tx)
        except Exception as e:
            log(f"  ! error con {w[:8]}: {e}")
            r = None
        if r:
            r["aparece_en"] = cuenta.get(w, {}).get("veces", 0)
            r["tokens_vistos"] = cuenta.get(w, {}).get("tokens", [])[:6]
            r["puntaje"] = puntaje(r)
            resultados.append(r)
        if r:
            log(f"  {i}/{len(a_auditar)} {w[:10]}… cerradas={r.get('cerradas')} "
                f"pnl={r.get('pnl_neto_sol')} wr={r.get('win_rate')}")
        else:
            log(f"  {i}/{len(a_auditar)} {w[:10]}… sin datos")
    resultados.sort(key=lambda r: -r["puntaje"])

    with open(SALIDA, "w") as fh:
        json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "tokens_revisados": [c.get("symbol") for c in ganadores], "wallets": resultados},
                  fh, ensure_ascii=False, indent=2)
    log(f"guardado en {SALIDA}")

    if a.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== {len(resultados)} wallets auditadas · ordenadas por lo que GANAN de verdad ===")
        for i, r in enumerate(resultados, 1):
            print(_linea(r, i))
        print("\nFiltros aplicados (umbrales de la investigación 2026-10): ≥30 posiciones cerradas · "
              "win rate 45-80% (≥85% es bandera roja) · <50% del PnL en un solo trade · "
              "t-stat ≥1.645 · ≤20% de posiciones sin compra · hold ≥2 min · fondeo no fresco")
        buenas = [r for r in resultados
                  if r.get("cerradas", 0) >= 30
                  and r["pnl_neto_sol"] > 0
                  and 45 <= (r.get("win_rate") or 0) <= 80
                  and (r.get("concentracion_pnl") or 100) < 50
                  and (r.get("t_stat") or 0) >= 1.645
                  and r.get("pct_sin_compra", 100) <= 20
                  and r.get("roi_total") is not None
                  and not r.get("fondeo_fresco")
                  and not str(r.get("copiable", "")).startswith("NO")]
        print(f"{len(buenas)} pasan TODOS los filtros")
        if buenas:
            w = buenas[0]
            print(f"\nLa mejor: {w['wallet']}")
            print(json.dumps(w, ensure_ascii=False, indent=2)[:1800])

    if a.agregar_top:
        for r in resultados[:a.agregar_top]:
            os.system(f'cd {AQUI} && python3 wallet_tracker.py --agregar {r["wallet"]} '
                      f'"auditada pnl {r["pnl_neto_sol"]}SOL wr {r["win_rate"]}%"')
    return 0


def _linea(r, i):
    if not r or not r.get("cerradas"):
        return f"{i:>2}. {r['wallet'] if r else '?'}  (sin posiciones cerradas en la ventana)"
    return (f"{i:>2}. {r['wallet']}  puntaje {r.get('puntaje','-'):>6}  "
            f"pnl {r['pnl_neto_sol']:>9.3f} SOL  wr {r['win_rate']:>5}%  "
            f"concentracion {r.get('concentracion_pnl') if r.get('concentracion_pnl') is not None else '-'}%  "
            f"t {r.get('t_stat') if r.get('t_stat') is not None else '-'}  "
            f"payoff {r.get('payoff') if r.get('payoff') is not None else '-'}  "
            f"roi_tot {(r['roi_total'] if r.get('roi_total') is not None else -999):>7}%  cerradas {r['cerradas']:>3}  "
            f"abiertas {r['abiertas']:>2}  sin_compra {r.get('pct_sin_compra',0):>3.0f}%  "
            f"en {r.get('aparece_en','?')} ganadores"
            + (f"   hold {r.get('min_tenencia_mediano')}min ({r.get('copiable')})" if r.get('copiable') else "")
            + ("  fondeo fresco" if r.get("fondeo_fresco") else "")
            + ("   ⚠️ " + r["alerta"] if r.get("alerta") else ""))


if __name__ == "__main__":
    sys.exit(main())
