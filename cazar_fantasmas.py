#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cazar más "FANTASMAS DEL DEV": cruzando POOL (quién vende) x MINT (quién recibió gratis).

Por qué así:
  · Las tx del MINT se llenan de creaciones de cuentas de token (spam/airdrops), así que leerlas
    buscando ventas da casi nada.
  · Las tx del POOL son TRADES: ahí están las ventas con su monto en SOL.
  · El MINT (su historial más viejo) dice quién RECIBIÓ tokens sin pagar.

  INSIDER = recibió sin pagar  ∩  vendió en el pool. Es la firma del dev pasando tokens a una
  wallet secundaria para que los venda (caso verificado: +81,75 SOL con 0 compras).

Uso:
  python3 cazar_fantasmas.py --tokens 8 --pool-tx 300 --mint-viejas 150 --min-sol 5
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
import auditor_wallets as AU         # noqa: E402
import pump_analizador as PA         # noqa: E402
import clasificar_wallets as CW      # noqa: E402
import buscar_10 as B10              # noqa: E402
import insider_token as IT           # noqa: E402

PAUSA = 0.03


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def por_wallet(mint, sigs):
    """De una lista de firmas: {wallet: {recibio, pago, vendio_sol, vendio_tok}}."""
    d = {}
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
            if not d_tok:
                continue
            i = keys.index(owner)
            d_sol = (post_b[i] - pre_b[i]) / 1e9 if (i < len(pre_b) and i < len(post_b)) else 0.0
            e = d.setdefault(owner, {"recibio": 0.0, "pago": 0.0, "vendio_sol": 0.0, "vendio_tok": 0.0})
            if d_tok > 0 and d_sol <= -0.2:
                e["pago"] += -d_sol
            elif d_tok > 0 and abs(d_sol) < 0.01:
                e["recibio"] += d_tok
            elif d_tok < 0 and d_sol >= 0.2:
                e["vendio_sol"] += d_sol
                e["vendio_tok"] += -d_tok
    return d


def main():
    ap = argparse.ArgumentParser(description="Más fantasmas del dev (cruce pool x mint)")
    ap.add_argument("--tokens", type=int, default=8)
    ap.add_argument("--pool-tx", type=int, default=300)
    ap.add_argument("--mint-viejas", type=int, default=150)
    ap.add_argument("--min-sol", type=float, default=5.0)
    ap.add_argument("--tx", type=int, default=400, help="tx para auditar a los que dan el cruce")
    a = ap.parse_args()

    log("1/3 tokens del radar que despegaron (con pool para leer ventas)…")
    tokens = []
    for c in B10.universo_radar(40):
        d = PA.dexscreener(c["mint"]) or {}
        mc = d.get("marketCap") or d.get("fdv") or 0
        pool = d.get("pairAddress")
        if mc < 200_000 or not pool:
            continue
        tokens.append({"mint": c["mint"], "symbol": c["symbol"], "mcap": mc, "pool": pool})
        if len(tokens) >= a.tokens:
            break
    for t in tokens:
        log(f"    {t['symbol']:<12} mcap {t['mcap']:>12.0f}")

    log("2/3 cruce: quién vendió en el pool ∩ quién recibió gratis en el mint…")
    hits, ctx = [], {}
    for t in tokens:
        mint, pool = t["mint"], t["pool"]
        viejas, _p, _tot = IT.sigs_mas_viejas(mint, a.mint_viejas, 60)
        nuevas = WT.rpc("getSignaturesForAddress", [pool, {"limit": a.pool_tx}]) or []
        A = por_wallet(mint, viejas)
        B = por_wallet(mint, nuevas)
        libres = {w: e["recibio"] for w, e in A.items() if e["recibio"] > 0 and e["pago"] < 0.2}
        vendedores = {w: e["vendio_sol"] for w, e in B.items() if e["vendio_sol"] > 0}
        cruce = [(w, cobro) for w, cobro in vendedores.items() if w in libres and cobro >= a.min_sol]
        ctx[t["symbol"]] = {"recibieron_gratis": len(libres), "vendieron_en_pool": len(vendedores),
                            "cruces": len(cruce)}
        log(f"    {t['symbol']:<12} recibieron gratis {len(libres):>4} · vendieron {len(vendedores):>4} "
            f"· CRUCES {len(cruce)}")
        for w, cobro in cruce:
            hits.append({"wallet": w, "symbol": t["symbol"], "mint": mint, "recibio": round(libres[w], 2),
                         "cobro_sol": round(cobro, 3)})

    hits.sort(key=lambda h: -h["cobro_sol"])
    log(f"    {len(hits)} wallets recibieron sin pagar Y vendieron en el pool")

    log(f"3/3 auditando los mejores ({a.tx} tx) para confirmar que nunca compraron…")
    for h in hits[:8]:
        try:
            m = AU.auditar_wallet(h["wallet"], min(a.tx, 1000))
            det = None
            for f in ((m or {}).get("detalle_cerradas") or []):
                if f.get("mint") == h["mint"]:
                    det = f
                    break
            h["etiqueta"] = CW.etiquetar(m)
            h["compras"] = (det or {}).get("compras")
            h["sol_in"] = (det or {}).get("sol_in")
            h["sol_out"] = (det or {}).get("sol_out")
            h["saldo"] = round(((WT.rpc("getBalance", [h["wallet"]]) or {}).get("value") or 0) / 1e9, 3)
            h["fondeo"] = [x.get("funder") for x in IT.funder_de(h["wallet"], 2)][:2]
            log(f"    {h['wallet'][:14]}… {h['symbol']:<10} cobró {h['cobro_sol']:>8.2f} SOL · "
                f"compras {h['compras']} · in {h['sol_in']} out {h['sol_out']} · saldo {h['saldo']} · "
                f"{h['etiqueta']['emoji']} {h['etiqueta']['tipo']}")
        except Exception as e:
            log(f"    ! {h['wallet'][:10]}: {e}")

    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "contexto": ctx,
               "hits": hits}, open(os.path.join(AQUI, "fantasmas.json"), "w"), ensure_ascii=False,
              indent=1, default=str)

    print("\n=== FANTASMAS (recibió sin pagar + vendió en el pool) ===")
    for h in hits:
        print(f"\n  💎 {h['symbol']:<10} {h['wallet']}")
        print(f"      recibió {h['recibio']:.0f} tokens sin pagar · cobró {h['cobro_sol']:.2f} SOL en el pool")
        if h.get("compras") is not None:
            print(f"      auditoría: compras {h['compras']} · in {h['sol_in']} out {h['sol_out']} · "
                  f"saldo {h['saldo']} SOL · {h['etiqueta']['emoji']} {h['etiqueta']['tipo']}")
            if h.get("fondeo"):
                print(f"      🔗 la fondearon: {', '.join([x for x in h['fondeo'] if x])}")
    if not hits:
        print("  (sin cruces por encima del mínimo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
