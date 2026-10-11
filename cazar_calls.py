#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Detectar CALLS de grupos de alpha (Pastel, Heaven or Hell, etc.) = RÁFAGAS de compras.

La idea: una call de un grupo no se ve en una wallet suelta, se ve en una **ráfaga**: varios
wallets distintos comprando el MISMO token dentro de la misma ventana corta, con tamaño real.
Después, los wallets que aparecen en **varias** ráfagas son el **núcleo del grupo** (los que
tienen la información antes que el público).

Pasos:
  1. Universo: tokens que corrieron (mcap y volumen reales hoy, del radar propio + movers).
  2. En cada token se leen las tx del POOL (son trades, no spam) y se sacan las COMPRAS con wallet,
     monto y hora.
  3. Ráfaga = ventana de `--ventana` segundos con ≥ `--min-wallets` compradores distintos y
     ≥ `--min-sol` SOL cada uno.
  4. Núcleo = wallets presentes en ≥ `--min-calls` ráfagas (union-find sobre co-ocurrencia).
  5. Se auditan con el motor propio (con CHECK de que la cuenta sea una wallet real).

Uso:
  python3 cazar_calls.py --tokens 30 --pool-tx 400 --min-wallets 3 --min-sol 0.5 --min-calls 2
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT          # noqa: E402
import auditor_wallets as AU         # noqa: E402
import pump_analizador as PA         # noqa: E402
import clasificar_wallets as CW      # noqa: E402
import buscar_10 as B10              # noqa: E402
import red_wallets as RW             # noqa: E402

PAUSA = 0.03
SISTEMA = "11111111111111111111111111111111"


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def es_wallet(dir_):
    """Toda dirección se valida antes de tratarla como wallet (lección del error del 07/10)."""
    v = (WT.rpc("getAccountInfo", [dir_, {"encoding": "jsonParsed"}]) or {}).get("value")
    if not v:
        return False
    return (v.get("owner") or "") == SISTEMA


def compras_del_pool(pool, mint, limite):
    """Compras en el pool: wallet, SOL puestos y hora. (Δtoken > 0 y ΔSOL < 0.)"""
    sigs = WT.rpc("getSignaturesForAddress", [pool, {"limit": limite}]) or []
    compras = []
    for s in sigs:
        if s.get("err"):
            continue
        try:
            tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                             "encoding": "jsonParsed"}])
        except Exception:
            tx = None
        time.sleep(PAUSA)
        if not tx or not tx.get("meta") or tx["meta"].get("err"):
            continue
        meta = tx["meta"]
        try:
            keys = WT.cuentas_tx(tx)
        except Exception:
            continue
        pre_b, post_b = meta.get("preBalances") or [], meta.get("postBalances") or []
        pre_t, post_t = {}, {}
        for b in meta.get("preTokenBalances") or []:
            if b.get("mint") == mint and b.get("owner"):
                pre_t[b["owner"]] = pre_t.get(b["owner"], 0.0) + float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        for b in meta.get("postTokenBalances") or []:
            if b.get("mint") == mint and b.get("owner"):
                post_t[b["owner"]] = post_t.get(b["owner"], 0.0) + float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        for owner in set(pre_t) | set(post_t):
            if owner in AU.AU_IGNORAR or owner not in keys:
                continue
            d_tok = post_t.get(owner, 0.0) - pre_t.get(owner, 0.0)
            if d_tok <= 0:
                continue
            i = keys.index(owner)
            d_sol = (post_b[i] - pre_b[i]) / 1e9 if (i < len(pre_b) and i < len(post_b)) else 0.0
            if d_sol <= -0.2:
                compras.append({"wallet": owner, "sol": -d_sol, "ts": s.get("blockTime") or 0,
                                "tx": s["signature"]})
    return compras


def rafagas(compras, ventana, min_wallets, min_sol):
    """Ventanas con ≥ min_wallets compradores distintos poniendo ≥ min_sol cada uno."""
    fuertes = [c for c in compras if c["sol"] >= min_sol and c["ts"]]
    fuertes.sort(key=lambda c: c["ts"])
    out, i = [], 0
    while i < len(fuertes):
        j = i
        grupo = []
        while j < len(fuertes) and fuertes[j]["ts"] - fuertes[i]["ts"] <= ventana:
            grupo.append(fuertes[j])
            j += 1
        wallets = {g["wallet"] for g in grupo}
        if len(wallets) >= min_wallets:
            out.append({"desde": fuertes[i]["ts"], "hasta": fuertes[j - 1]["ts"],
                        "wallets": sorted(wallets), "sol_total": round(sum(g["sol"] for g in grupo), 2)})
            i = j
        else:
            i += 1
    return out


def main():
    ap = argparse.ArgumentParser(description="Detectar calls (ráfagas de compras coordinadas)")
    ap.add_argument("--tokens", type=int, default=30)
    ap.add_argument("--pool-tx", type=int, default=400)
    ap.add_argument("--ventana", type=int, default=90, help="segundos para considerar una ráfaga")
    ap.add_argument("--min-wallets", type=int, default=3)
    ap.add_argument("--min-sol", type=float, default=0.5)
    ap.add_argument("--min-calls", type=int, default=2, help="en cuántas ráfagas debe aparecer para ser núcleo")
    ap.add_argument("--auditar", type=int, default=12)
    ap.add_argument("--tx", type=int, default=400)
    a = ap.parse_args()

    log("1/4 universo: tokens que corrieron…")
    tokens, vistos = [], set()
    for f in (PA.pump_movidos(150), PA.pump_live(150)):
        for c in (f or []):
            m = c.get("mint")
            if not m or m in vistos:
                continue
            vistos.add(m)
            d = PA.dexscreener(m) or {}
            mc = d.get("marketCap") or d.get("fdv") or 0
            vol = (d.get("volume") or {}).get("h24") or 0
            pool = d.get("pairAddress")
            if mc < 150_000 or vol < 50_000 or not pool:
                continue
            tokens.append({"mint": m, "symbol": (d.get("baseToken") or {}).get("symbol") or c.get("symbol"),
                           "mcap": mc, "vol24": vol, "pool": pool})
            if len(tokens) >= a.tokens:
                break
        if len(tokens) >= a.tokens:
            break
    for t in tokens:
        log(f"    {t['symbol']:<12} mcap {t['mcap']:>12.0f} · vol24 {t['vol24']:>10.0f}")

    log(f"2/4 buscando ráfagas (≥{a.min_wallets} wallets en ≤{a.ventana}s con ≥{a.min_sol} SOL)…")
    calls, partic = [], defaultdict(set)
    for i, t in enumerate(tokens, 1):
        compras = compras_del_pool(t["pool"], t["mint"], a.pool_tx)
        rf = rafagas(compras, a.ventana, a.min_wallets, a.min_sol)
        for r in rf:
            calls.append({**r, "symbol": t["symbol"], "mint": t["mint"], "mcap": t["mcap"]})
            for w in r["wallets"]:
                partic[w].add(t["symbol"])
        log(f"    {i}/{len(tokens)} {t['symbol']:<12} compras {len(compras):>4} · RÁFAGAS {len(rf)}")

    log(f"3/4 núcleo: wallets en ≥{a.min_calls} ráfagas…")
    nucleo = {w: sorted(s) for w, s in partic.items() if len(s) >= a.min_calls}
    log(f"    {len(calls)} ráfagas detectadas · {len(partic)} wallets implicadas · "
        f"{len(nucleo)} en el NÚCLEO (≥{a.min_calls} calls)")

    log(f"4/4 auditando el núcleo ({a.tx} tx, con check de wallet real)…")
    fichas = []
    for w, syms in sorted(nucleo.items(), key=lambda kv: -len(kv[1]))[:a.auditar]:
        if not es_wallet(w):
            log(f"    {w[:14]}… DESCARTADA (no es wallet)")
            continue
        try:
            m = AU.auditar_wallet(w, min(a.tx, 1000))
        except Exception as e:
            log(f"    ! {w[:10]}: {e}")
            m = None
        nota, ok, no = RW.puntaje_10(m)
        saldo = ((WT.rpc("getBalance", [w]) or {}).get("value") or 0) / 1e9
        fichas.append({"wallet": w, "calls": len(syms), "tokens": syms, "nota": nota,
                       "etiqueta": CW.etiquetar(m), "cerradas": (m or {}).get("cerradas"),
                       "pnl": (m or {}).get("pnl_neto_sol"), "win_rate": (m or {}).get("win_rate"),
                       "hold": (m or {}).get("min_tenencia_mediano"),
                       "sin_compra": (m or {}).get("pct_sin_compra"), "roi": (m or {}).get("roi_total"),
                       "saldo_sol": round(saldo, 3), "fallados": no})
        log(f"    {w[:14]}… en {len(syms)} calls · nota {nota}/10 · {fichas[-1]['etiqueta']['emoji']} "
            f"{fichas[-1]['etiqueta']['tipo']:<9} · cerradas {fichas[-1]['cerradas']} · "
            f"pnl {fichas[-1]['pnl']} · wr {fichas[-1]['win_rate']}% · saldo {fichas[-1]['saldo_sol']}")

    fichas.sort(key=lambda f: (-f["calls"], -(f["pnl"] or -999)))
    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "calls": calls, "nucleo": fichas,
               "wallets_por_token": {w: s for w, s in partic.items()}},
              open(os.path.join(AQUI, "calls_detectadas.json"), "w"), ensure_ascii=False, indent=1, default=str)

    print(f"\n=== RÁFAGAS (calls) DETECTADAS: {len(calls)} ===")
    for c in calls[:12]:
        print(f"\n  ⚡ {c['symbol']:<10} {len(c['wallets'])} wallets en {c['hasta']-c['desde']}s · "
              f"{c['sol_total']} SOL")
        for w in c["wallets"][:6]:
            print(f"       · {w}")
    print(f"\n=== NÚCLEO (aparecen en ≥{a.min_calls} calls) ===")
    for f in fichas:
        print(f"\n  {f['nota']:>4}/10  {f['etiqueta']['emoji']} {f['etiqueta']['tipo']:<9} {f['wallet']}")
        print(f"       en {f['calls']} calls: {', '.join(f['tokens'][:8])}")
        print(f"       {f['cerradas']} cerradas · PnL {f['pnl']} SOL · wr {f['win_rate']}% · "
              f"ROI {f['roi']}% · hold {f['hold']} · sin compra {f['sin_compra']}% · saldo {f['saldo_sol']}")
        if f["fallados"]:
            print(f"       ❌ {' · '.join(f['fallados'][:3])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
