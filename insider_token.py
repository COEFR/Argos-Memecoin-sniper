#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Detector de INSIDERS por token: ¿el lanzamiento fue un bundle?

Implementa las técnicas del dossier `TECNICAS_INSIDERS.md`:

  1. **Compras en el slot de creación** (la señal más limpia): si hay ≥3 compras distintas en el
     mismo slot en que nació el token, es un bundle — nadie de afuera puede transaccionar ahí.
  2. **% del supply acaparado en el bloque cero** (con los umbrales: <5 normal · 5-15 bloque insider ·
     15-25 un actor mueve el precio · >25 los insiders son dueños).
  3. **Clúster de fondeo**: quién le mandó SOL a cada comprador del bloque cero → funders repetidos
     = el mismo operador.
  4. **Precio de break-even del bundle**: con la reserva de la curva se calcula cuánto SOL orgánico
     necesita para que el bundler pueda salir en cero (a partir de ahí es puro riesgo para el resto).

Uso:
  python3 insider_token.py <mint>                     # un token
  python3 insider_token.py --radar 5                  # los últimos 5 aprobados por el radar
  python3 insider_token.py <mint> --tx 400            # más profundidad para el arranque
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT          # noqa: E402
import pump_analizador as PA         # noqa: E402
import buscar_10 as B10              # noqa: E402
import auditor_wallets as AU         # noqa: E402

PAUSA = 0.03
VIRTUAL_SOL = 30.0            # reservas virtuales al lanzar (docs de pump.fun)
VIRTUAL_TOKENS = 1_073_000_000.0


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def sigs_mas_viejas(mint, cuantas, max_paginas=200):
    """Las tx más viejas del token, paginando HASTA EL FINAL del historial.

    Trampa que había en la primera versión: paginar sólo 3 páginas devuelve las 3.000 tx más
    nuevas y `[:cuantas]` toma la más vieja *de ese tramo*, no el lanzamiento. En un token con
    100.000 tx eso lee un tramo del medio y el "slot de creación" queda mal.
    """
    out, antes, pag = [], None, 0
    while True:
        p = {"limit": 1000}
        if antes:
            p["before"] = antes
        lote = WT.rpc("getSignaturesForAddress", [mint, p]) or []
        if not lote:
            break
        out = lote + out
        antes = lote[-1]["signature"]
        pag += 1
        if len(lote) < 1000 or pag >= max_paginas:
            break
        time.sleep(0.03)
    return out[:cuantas], pag, len(out)


def primeras_tx(mint, cuantas, max_paginas=200):
    """Las tx más viejas del mint (paginando hasta el arranque real)."""
    sigs, paginas, total = sigs_mas_viejas(mint, cuantas, max_paginas)
    log(f"    historial completo: {total} tx en {paginas} páginas · leyendo las {len(sigs)} más viejas")
    out = []
    for s in sigs:
        if s.get("err"):
            continue
        try:
            tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                             "encoding": "jsonParsed"}])
        except Exception:
            tx = None
        time.sleep(PAUSA)
        if tx and tx.get("meta") and not tx["meta"].get("err"):
            out.append((s, tx))
    return out


def compras_del_slot(tx_arranque, mint):
    """Compradores por slot: devuelve {slot: [{wallet, tokens, sol}]} y el slot de creación."""
    por_slot = {}
    for s, tx in tx_arranque:
        slot = tx.get("slot")
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
            if d_sol > -0.2:                    # no pagó: es reparto, no compra
                continue
            por_slot.setdefault(slot, []).append({"wallet": owner, "tokens": d_tok, "sol": -d_sol,
                                                  "tx": s["signature"]})
    return por_slot


def funder_de(wallet, limite=3):
    """Quién le mandó SOL: mira las tx más viejas del wallet y devuelve los remitentes."""
    try:
        sigs = B10._sigs_del_pool(wallet, limite, "viejas")
    except Exception:
        return []
    out = []
    for s in sigs:
        try:
            tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                            "encoding": "jsonParsed"}])
        except Exception:
            tx = None
        time.sleep(PAUSA)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        try:
            keys = WT.cuentas_tx(tx)
        except Exception:
            continue
        pre_b, post_b = meta.get("preBalances") or [], meta.get("postBalances") or []
        idx = keys.index(wallet) if wallet in keys else None
        if idx is None or idx >= len(pre_b) or idx >= len(post_b):
            continue
        d_mio = (post_b[idx] - pre_b[idx]) / 1e9
        if d_mio <= 0.01:
            continue
        for j, k in enumerate(keys):
            if k == wallet or k in AU.AU_IGNORAR or j >= len(pre_b) or j >= len(post_b):
                continue
            d = (post_b[j] - pre_b[j]) / 1e9
            if d <= -0.01:
                out.append({"funder": k, "sol": round(-d, 4), "fecha": s.get("blockTime")})
    return out


def analizar(mint, tx_arranque=200, max_paginas=200):
    rc = PA.rugcheck(mint) or {}
    tok = rc.get("token") or {}
    try:
        supply = float(tok.get("supply") or 0)
        dec = int(tok.get("decimals") or 6)
        if supply > 1e12:                    # RugCheck devuelve el supply crudo (con decimales)
            supply = supply / (10 ** dec)
    except Exception:
        supply = 0.0
    ds = PA.dexscreener(mint) or {}
    sym = ((ds.get("baseToken") or {}).get("symbol")) or mint[:6]
    precio = float(ds.get("priceUsd") or 0)

    tx = primeras_tx(mint, tx_arranque, max_paginas)
    if not tx:
        return {"mint": mint, "symbol": sym, "error": "sin tx legibles"}
    por_slot = compras_del_slot(tx, mint)
    if not por_slot:
        return {"mint": mint, "symbol": sym, "error": "no se detectaron compras"}
    slots = sorted(por_slot)
    slot_creacion = slots[0]
    compras = por_slot[slot_creacion]
    wallets = {c["wallet"] for c in compras}
    tok_slot = sum(c["tokens"] for c in compras)
    sol_slot = sum(c["sol"] for c in compras)
    pct = (tok_slot / supply * 100) if supply else 0.0

    # umbrales del dossier
    if pct > 25:
        lectura = "🚨 los insiders son dueños: vos sos la salida"
    elif pct >= 15:
        lectura = "🚨 un solo actor mueve el precio"
    elif pct >= 5:
        lectura = "⚠️ bloque insider: entrar chico y con invalidación"
    elif pct:
        lectura = "✅ posición inicial normal"
    else:
        lectura = "?"

    # clúster de fondeo de los compradores del bloque cero
    funders = {}
    for w in wallets:
        for f in funder_de(w):
            funders.setdefault(f["funder"], []).append(w)
    cluster = {k: v for k, v in funders.items() if len(v) >= 2}

    # break-even del bundle: cuánto SOL orgánico tiene que entrar para que pueda salir en cero
    break_even_sol = None
    if sol_slot > 0:
        try:
            k = VIRTUAL_SOL * VIRTUAL_TOKENS
            sol1 = VIRTUAL_SOL + sol_slot
            tok1 = k / sol1
            t_b = VIRTUAL_TOKENS - tok1          # tokens que se llevó el bundle

            def sale_con(x):
                sol2 = sol1 + x
                tok2 = k / sol2
                return (sol2 - k / (tok2 + t_b)) - sol_slot   # lo que cobra al vender, menos lo puesto

            lo, hi = 0.0, 5000.0
            if sale_con(hi) > 0:
                for _ in range(60):
                    mid = (lo + hi) / 2
                    if sale_con(mid) > 0:
                        hi = mid
                    else:
                        lo = mid
                break_even_sol = round(hi, 2)
        except Exception:
            break_even_sol = None

    return {"mint": mint, "symbol": sym, "precio_usd": precio,
            "slot_creacion": slot_creacion, "tx_en_slot_creacion": len(compras),
            "wallets": sorted(wallets), "sol_invertido": round(sol_slot, 3),
            "pct_supply": round(pct, 2), "lectura": lectura,
            "clusters_fondeo": {k: sorted(set(v)) for k, v in cluster.items()},
            "sol_para_break_even": break_even_sol,
            "bundle": len(compras) >= 3}


def main():
    ap = argparse.ArgumentParser(description="Detector de insiders/bundle por token")
    ap.add_argument("mint", nargs="?")
    ap.add_argument("--radar", type=int, default=0, help="analizar los últimos N tokens aprobados por el radar")
    ap.add_argument("--tx", type=int, default=200, help="tx del arranque a leer")
    ap.add_argument("--paginas", type=int, default=200, help="páginas de 1.000 firmas a paginar para llegar al arranque")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    mints = []
    if a.radar:
        for c in B10.universo_radar(a.radar):
            mints.append((c["mint"], c["symbol"]))
    elif a.mint:
        mints = [(a.mint, None)]
    else:
        ap.print_help()
        return 1

    res = []
    for mint, sym in mints:
        log(f"analizando {sym or mint[:8]}…")
        r = analizar(mint, a.tx, a.paginas)
        res.append(r)
        if "error" in r:
            log(f"  {r['error']}")
            continue
        log(f"  {r['symbol']}: {len(r['wallets'])} wallets en el slot de creación "
            f"({r['tx_en_slot_creacion']} compras) · {r['sol_invertido']} SOL · {r['pct_supply']}% del supply "
            f"· bundle={'SÍ' if r['bundle'] else 'no'} · clusters={len(r['clusters_fondeo'])}")
    json.dump(res, open(os.path.join(AQUI, "insiders_token.json"), "w"), ensure_ascii=False, indent=1, default=str)

    for r in res:
        print(f"\n=== {r.get('symbol')} · {r['mint']}")
        if "error" in r:
            print("   ", r["error"]); continue
        print(f"   slot de creación {r['slot_creacion']} · COMPRAS en ese slot: {r['tx_en_slot_creacion']} "
              f"({len(r['wallets'])} wallets) {'→ BUNDLE 🚨' if r['bundle'] else '→ sin bundle'}")
        print(f"   pusieron {r['sol_invertido']} SOL = {r['pct_supply']}% del supply · {r['lectura']}")
        if r.get("sol_para_break_even") is not None:
            print(f"   break-even: con ~{r['sol_para_break_even']} SOL de compra orgánica el bundle ya sale en cero")
        for f, ws in (r.get("clusters_fondeo") or {}).items():
            print(f"   🔗 clúster: {f[:20]}… fondeó a {len(ws)} de los compradores")
        for w in r["wallets"][:8]:
            print(f"      · {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
