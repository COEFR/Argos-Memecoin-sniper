#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forense de una wallet de Solana — le pasás una dirección y averigua:
  · de qué wallets recibió SOL y a qué wallets mandó (siguiendo el rastro del dinero)
  · qué tokens compró, cuánto puso, cuánto sacó y con qué resultado
  · si es un trader, un insider con tokens regalados, una pasarela o infraestructura
  · quién la fondeó y qué wallets hermanas existen (posibles wallets del mismo dueño)
  · cuál de todo el grupo es la que REALMENTE sirve (la que gana)

Solo datos públicos del RPC de Solana + RugCheck/DexScreener. Sin API keys.

Uso:
  python3 wallet_forense.py <direccion>                 # forense completa
  python3 wallet_forense.py <direccion> --tx 120        # más historial
  python3 wallet_forense.py <direccion> --sin-cluster   # sin buscar hermanas
  python3 wallet_forense.py <direccion> --discord       # manda el resumen al canal
  python3 wallet_forense.py <direccion> --json
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
import auditor_wallets as AU          # noqa: E402
import clasificar_wallets as CW       # noqa: E402

PAUSA = 0.10
MIN_SOL = 0.01                        # movimientos de SOL por debajo de esto se ignoran
TOPE_HERMANAS = 4                     # cuántas hermanas auditar (cada una cuesta ~1 min de RPC)

# programas y cuentas de infraestructura: no son "wallets" que mandan o reciben plata
IGNORAR = {
    "11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb", "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "So11111111111111111111111111111111111111112",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s", "SysvarRent111111111111111111111111111111111",
    "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM",
}


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def _pub(k):
    return k.get("pubkey") if isinstance(k, dict) else k


# --------------------------------------------------------------- 1. flujos

def flujos_sol(direccion, max_tx=80):
    """Quién le mandó SOL y a quién le mandó, con montos y fechas (últimas max_tx transacciones)."""
    sigs = WT.rpc("getSignaturesForAddress", [direccion, {"limit": max_tx}]) or []
    recibido, enviado = {}, {}
    for s in sigs:
        if s.get("err"):
            continue
        tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                       "encoding": "jsonParsed"}])
        time.sleep(PAUSA)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        keys = WT.cuentas_tx(tx)
        for i, k in enumerate(keys):
            if k == direccion or k in IGNORAR or i >= len(pre) or i >= len(post):
                continue
            d = (post[i] - pre[i]) / 1e9
            if abs(d) < MIN_SOL:
                continue
            reg = recibido if d > 0 else enviado
            e = reg.setdefault(k, {"sol": 0.0, "tx": 0, "ultimo": None})
            e["sol"] += abs(d)
            e["tx"] += 1
            ts = s.get("blockTime") or 0
            e["ultimo"] = max(e["ultimo"] or 0, ts)
    return sigs, recibido, enviado


# --------------------------------------------------------------- 2. tokens

def tokens_operados(direccion, sigs):
    """Por token: compras, ventas, SOL puesto y sacado, resultado y si sigue abierto."""
    por_token = {}
    for s in sigs:
        if s.get("err"):
            continue
        tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                       "encoding": "jsonParsed"}])
        time.sleep(PAUSA)
        ev = WT.parsear_tx(tx, direccion) if tx else None
        if not ev or not ev.get("mint"):
            continue
        d = por_token.setdefault(ev["mint"], {"compras": 0, "ventas": 0, "sol_in": 0.0, "sol_out": 0.0,
                                             "tokens": 0.0, "recibidos": 0, "ts": [], "slots": []})
        d["ts"].append(s.get("blockTime") or 0)
        d["slots"].append(tx.get("slot"))
        if ev["accion"] == "compra":
            d["compras"] += 1
            d["sol_in"] += abs(ev["sol"])
            d["tokens"] += ev["cantidad"] or 0
        elif ev["accion"] == "venta":
            d["ventas"] += 1
            d["sol_out"] += abs(ev["sol"])
        elif ev["accion"] == "recibe":
            d["recibidos"] += 1
    tenencias = tenencias_actuales(direccion)
    filas = []
    for mint, d in por_token.items():
        if not (d["compras"] or d["ventas"]):
            continue
        pnl = d["sol_out"] - d["sol_in"]
        filas.append({
            "mint": mint, "compras": d["compras"], "ventas": d["ventas"],
            "sol_in": round(d["sol_in"], 4), "sol_out": round(d["sol_out"], 4),
            "pnl": round(pnl, 4),
            "roi": round(pnl / d["sol_in"] * 100, 1) if d["sol_in"] > 0 else None,
            "minutos": round((max(d["ts"]) - min(d["ts"])) / 60, 1) if d["ts"] else None,
            "abierto": tenencias.get(mint, 0) > 0 or d["ventas"] == 0,
            "solo_vendio": d["compras"] == 0 and d["ventas"] > 0,
            "slots": d["slots"][:6],
        })
    filas.sort(key=lambda f: -f["pnl"])
    return filas


def tenencias_actuales(direccion):
    cuentas = WT.rpc("getTokenAccountsByOwner",
                     [direccion, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                      {"encoding": "jsonParsed"}]) or {}
    out = {}
    for acc in (cuentas.get("value") or []):
        try:
            info = acc["account"]["data"]["parsed"]["info"]
            amt = float((info["tokenAmount"] or {}).get("uiAmount") or 0)
            if amt > 0:
                out[info["mint"]] = amt
        except Exception:
            continue
    return out


# ------------------------------------------------------------ 3. hermanas

def hermanas(direccion, recibido):
    """La wallet que la fondeó y las otras wallets que esa misma wallet fondeó (hermanas)."""
    if not recibido:
        return None, []
    padre = max(recibido.items(), key=lambda kv: kv[1]["sol"])[0]
    sigs_padre = WT.rpc("getSignaturesForAddress", [padre, {"limit": 60}]) or []
    hijas = {}
    for s in sigs_padre:
        if s.get("err"):
            continue
        tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                       "encoding": "jsonParsed"}])
        time.sleep(PAUSA)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        keys = WT.cuentas_tx(tx)
        for i, k in enumerate(keys):
            if k in IGNORAR or k == padre or i >= len(pre) or i >= len(post):
                continue
            d = (post[i] - pre[i]) / 1e9
            if d >= 0.05:                       # el padre le mandó plata a esta wallet
                e = hijas.setdefault(k, {"sol": 0.0, "tx": 0, "ultimo": 0})
                e["sol"] += d
                e["tx"] += 1
                e["ultimo"] = max(e["ultimo"], s.get("blockTime") or 0)
    orden = sorted(hijas.items(), key=lambda kv: -kv[1]["sol"])
    return padre, orden


# ------------------------------------------------------------ 4. veredicto

def es_hub(direccion, umbral=1000):
    """¿Es una CEX/hub (miles de firmas)? Si lo es, NO sirve para agrupar wallets (stop-node)."""
    try:
        sigs = WT.rpc("getSignaturesForAddress", [direccion, {"limit": umbral}])
        return bool(sigs) and len(sigs) >= umbral
    except Exception:
        return False


def red_de_la_wallet(direccion, recibido, enviado, max_red=6):
    """Wallets de la red: las que la fondearon y las que recibieron su plata (sin hubs ni infra)."""
    cands = {}
    for w, d in sorted(recibido.items(), key=lambda kv: -kv[1]["sol"])[:4]:
        cands[w] = {"origen": f"la fondeó con {d['sol']:.2f} SOL", "peso": d["sol"]}
    for w, d in sorted(enviado.items(), key=lambda kv: -kv[1]["sol"])[:4]:
        cands.setdefault(w, {"origen": f"recibió {d['sol']:.2f} SOL de ella", "peso": d["sol"]})
    # descartar hubs (un exchange no es una wallet útil para seguir)
    limpio = {}
    for w, info in cands.items():
        if es_hub(w):
            info["origen"] += " · es un HUB/CEX (se descarta como wallet a seguir)"
            continue
        limpio[w] = info
    return dict(sorted(limpio.items(), key=lambda kv: -kv[1]["peso"])[:max_red])


def veredicto(direccion, recibido, enviado, tokens):
    """Clasifica la wallet con las banderas a la vista."""
    banderas, tipo = [], "?"
    cerrados = [t for t in tokens if not t["abierto"]]
    solo_vendio = [t for t in tokens if t["solo_vendio"]]
    pnl = sum(t["pnl"] for t in tokens)
    invertido = sum(t["sol_in"] for t in tokens)
    outs = len(enviado)
    ins = len(recibido)
    regalados_pct = (len(solo_vendio) / len(cerrados) * 100) if cerrados else 0

    if len(recibido) + len(enviado) > 40 and not tokens:
        tipo = "INFRAESTRUCTURA / pasarela de fondos"
        banderas.append(f"se mueve con {ins + outs} contrapartes distintas y casi no opera tokens")
    elif regalados_pct > 30:
        tipo = "INSIDER / bundle"
        banderas.append(f"{regalados_pct:.0f}% de sus posiciones cerradas las cobró sin haber comprado "
                        f"(recibió los tokens)")
    elif len(enviado) >= 5 and len(recibido) >= 5 and invertido < 1:
        tipo = "REPARTIDORA (relay)"
        banderas.append("recibe de muchos y reparte a muchos, pero casi no compra tokens: "
                        "la wallet que opera está en otro lado del grupo")
    elif tokens:
        tipo = "TRADER"
        banderas.append(f"{len(cerrados)} posiciones cerradas · {len(recibido)} wallets la fondearon")
    if solo_vendio:
        banderas.append(f"{len(solo_vendio)} token(s) vendidos que nunca compró")
    return {"tipo": tipo, "banderas": banderas, "pnl_total": round(pnl, 4),
            "invertido": round(invertido, 4), "cerradas": len(cerrados),
            "win_rate": round(len([t for t in cerrados if t["pnl"] > 0]) / len(cerrados) * 100, 1)
            if cerrados else None}


# ---------------------------------------------------------------- informe

def _fecha(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m %H:%M") if ts else "?"


def informe(direccion, max_tx=80, con_cluster=True, auditar_hermanas=True, max_red=6):
    log(f"forense de {direccion} · últimas {max_tx} transacciones")
    saldo = WT.rpc("getBalance", [direccion]) or {}
    sigs, recibido, enviado = flujos_sol(direccion, max_tx)
    log(f"  flujos: recibió de {len(recibido)} wallets · mandó a {len(enviado)} wallets")
    tokens = tokens_operados(direccion, sigs)
    log(f"  tokens operados: {len(tokens)}")
    v = veredicto(direccion, recibido, enviado, tokens)

    padre, hs = (None, [])
    if con_cluster:
        log("  buscando la wallet que la fondeó y sus hermanas…")
        padre, hs = hermanas(direccion, recibido)

    # --- la red: hermanas + quién la fondeó + a quién le mandó la plata, todo clasificado ---
    red = {}
    for w, d in hs[:min(TOPE_HERMANAS, max_red)]:
        red.setdefault(w, {"origen": "hermana (misma wallet la fondeó)", "peso": d["sol"]})
    for w, info in red_de_la_wallet(direccion, recibido, enviado, max_red=max_red).items():
        red.setdefault(w, info)
    red.pop(direccion, None)

    wallets_utiles = []
    if (auditar_hermanas or red):
        log(f"  clasificando la red: {len(red)} wallets + la tuya…")
        objetivo = [(direccion, {"origen": "la que te pasaron", "peso": 0})] + list(red.items())
        for w, info in objetivo:
            try:
                r = AU.auditar_wallet(w, min(max_tx, 90))
            except Exception as e:
                log(f"    ! error con {w[:8]}: {e}")
                r = None
            et = CW.etiquetar(r)
            fila = {"wallet": w, "origen": info["origen"], **et,
                    "pnl": (r or {}).get("pnl_neto_sol"), "win_rate": (r or {}).get("win_rate"),
                    "cerradas": (r or {}).get("cerradas"), "roi": (r or {}).get("roi_total"),
                    "sin_compra": (r or {}).get("pct_sin_compra"),
                    "hold": (r or {}).get("min_tenencia_mediano"),
                    "es_la_consultada": w == direccion}
            wallets_utiles.append(fila)
            log(f"    {et['emoji']} {et['tipo']:<10} {w[:12]}… {(r or {}).get('pnl_neto_sol','')} SOL")
        prio = {"INSIDER": 0, "CANDIDATA": 1, "BOT/MEV": 2, "PIERDE": 3, "PASARELA": 4, "SIN DATOS": 5}
        wallets_utiles.sort(key=lambda f: (prio.get(f["tipo"], 9), -(f["pnl"] or -999)))

    # recomendación en una línea
    cands = [f for f in wallets_utiles if f["tipo"] == "CANDIDATA"]
    insiders = [f for f in wallets_utiles if f["tipo"] == "INSIDER"]
    recomendacion = []
    if cands:
        mejor = max(cands, key=lambda f: f["pnl"] or -999)
        recomendacion.append(f"SEGUIR ⭐ {mejor['wallet']} (PnL {mejor['pnl']:+.2f} SOL, "
                             f"win rate {mejor['win_rate']:.0f}%, {mejor['cerradas']} cerradas)")
    for f in insiders[:2]:
        recomendacion.append(f"VIGILAR 🚨 {f['wallet']} (insider: saber cuándo suelta)")
    malas = [f for f in wallets_utiles if f["tipo"] in ("PIERDE", "BOT/MEV", "PASARELA")]
    for f in malas[:3]:
        recomendacion.append(f"EVITAR {f['emoji']} {f['wallet']} ({f['tipo']})")
    ranking = [f for f in wallets_utiles if f["tipo"] in ("CANDIDATA", "INSIDER")]

    return {"wallet": direccion, "saldo_sol": round((saldo.get("value") or 0) / 1e9, 4),
            "tx_revisadas": len(sigs), "recibido": recibido, "enviado": enviado,
            "tokens": tokens, "veredicto": v, "fondeo_de": padre,
            "hermanas": [{"wallet": w, **d} for w, d in hs[:10]],
            "wallets_utiles": wallets_utiles, "recomendacion": recomendacion,
            "ranking_grupo": ranking}


def texto_informe(r):
    v = r["veredicto"]
    l = [f"🔍 FORENSE DE WALLET  {r['wallet']}",
         f"   saldo {r['saldo_sol']} SOL · {r['tx_revisadas']} tx revisadas",
         f"   clasificación: **{v['tipo']}**"]
    for b in v["banderas"]:
        l.append(f"   · {b}")
    if v["cerradas"]:
        l.append(f"   · PnL realizado {v['pnl_total']:+.3f} SOL sobre {v['invertido']:.3f} invertidos "
                 f"· {v['cerradas']} cerradas · win rate {v['win_rate']}%")
    l.append("")
    top_in = sorted(r["recibido"].items(), key=lambda kv: -kv[1]["sol"])[:6]
    if top_in:
        l.append("💰 LE MANDARON SOL (de dónde vino la plata)")
        for w, d in top_in:
            l.append(f"   {d['sol']:>9.3f} SOL en {d['tx']} tx · últ. {_fecha(d['ultimo'])} · {w}")
    top_out = sorted(r["enviado"].items(), key=lambda kv: -kv[1]["sol"])[:6]
    if top_out:
        l.append("")
        l.append("📤 MANDÓ SOL A")
        for w, d in top_out:
            l.append(f"   {d['sol']:>9.3f} SOL en {d['tx']} tx · últ. {_fecha(d['ultimo'])} · {w}")
    if r["tokens"]:
        l.append("")
        l.append("🪙 TOKENS OPERADOS (por resultado)")
        for t in r["tokens"][:10]:
            estado = "abierto" if t["abierto"] else "cerrado"
            extra = " ⚠️ nunca compró" if t["solo_vendio"] else ""
            l.append(f"   {t['pnl']:>+9.4f} SOL · {(t['roi'] if t['roi'] is not None else 0):>7.1f}% · "
                     f"in {t['sol_in']:.3f}/out {t['sol_out']:.3f} · {estado}{extra} · {t['mint'][:10]}…")
    if r.get("fondeo_de"):
        l.append("")
        l.append(f"🏦 LA FONDEÓ: {r['fondeo_de']}")
    if r["hermanas"]:
        l.append("")
        l.append("👥 WALLETS HERMANAS (fondeadas por la misma wallet)")
        for h in r["hermanas"][:8]:
            marca = "  ← esta" if h["wallet"] == r["wallet"] else ""
            l.append(f"   {h['sol']:>8.3f} SOL · {h['tx']} tx · {h['wallet']}{marca}")
    if r.get("wallets_utiles"):
        l.append("")
        l.append("🏆 WALLETS ÚTILES DE ESTA RED (para el tracker)")
        for f in r["wallets_utiles"]:
            marca = "  ← la que te pasaron" if f.get("es_la_consultada") else ""
            nums = []
            if f.get("pnl") is not None:
                nums.append(f"PnL {f['pnl']:+.2f} SOL")
            if f.get("win_rate") is not None:
                nums.append(f"wr {f['win_rate']:.0f}%")
            if f.get("cerradas"):
                nums.append(f"{f['cerradas']} cerradas")
            if f.get("roi") is not None:
                nums.append(f"ROI {f['roi']}%")
            if f.get("sin_compra") is not None:
                nums.append(f"sin_compra {f['sin_compra']:.0f}%")
            if f.get("hold") is not None:
                nums.append(f"hold {f['hold']}min")
            l.append(f"   {f['emoji']} {f['tipo']:<10} {f['wallet']}{marca}")
            l.append(f"      {f['origen']}")
            l.append(f"      {' · '.join(nums) if nums else 'sin datos'}")
            l.append(f"      {f['razon']}")
    if r.get("recomendacion"):
        l.append("")
        l.append("✅ QUÉ HACER")
        for x in r["recomendacion"]:
            l.append(f"   {x}")
    l.append("")
    l.append("⚠️ Medición propia sobre datos públicos (últimas tx revisadas). No incluye priority "
             "fees/tips, así que el PnL real es algo peor. No es asesoría financiera.")
    return "\n".join(l)


def main():
    ap = argparse.ArgumentParser(description="Forense de una wallet de Solana")
    ap.add_argument("wallet", help="dirección a investigar")
    ap.add_argument("--tx", type=int, default=80, help="transacciones a revisar")
    ap.add_argument("--sin-cluster", action="store_true", help="no buscar hermanas")
    ap.add_argument("--sin-ranking", action="store_true", help="no auditar las hermanas")
    ap.add_argument("--max-red", type=int, default=6, help="tope de wallets de la red a clasificar")
    ap.add_argument("--discord", action="store_true", help="manda el resumen al canal")
    ap.add_argument("--cargar", type=int, default=0, metavar="N",
                    help="carga las N wallets más útiles de la red al tracker, ya etiquetadas")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    r = informe(a.wallet, a.tx, con_cluster=not a.sin_cluster, auditar_hermanas=not a.sin_ranking,
                max_red=a.max_red)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(texto_informe(r))
    if a.cargar:
        utiles = [f for f in r["wallets_utiles"] if f["tipo"] in ("CANDIDATA", "INSIDER")][:a.cargar]
        for f in utiles:
            etq = f"{f['emoji']}{f['tipo']}" + (f" pnl{f['pnl']:+.1f}SOL" if f.get("pnl") is not None else "")
            os.system(f'cd {AQUI} && python3 wallet_tracker.py --agregar {f["wallet"]} "{etq}"')
            print(f"cargada al tracker: {etq} · {f['wallet']}")
    if a.discord:
        v = r["veredicto"]
        em = {"title": f"🔍 Forense de wallet · {v['tipo']}",
              "description": texto_informe(r)[:3900],
              "color": 0xE67E22 if "INSIDER" in v["tipo"] else 0x3498DB,
              "footer": {"text": r["wallet"]}}
        print("discord:", PA.enviar_embed(em, contenido="🔍 **Informe de forense**"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
