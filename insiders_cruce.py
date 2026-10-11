#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Insiders por CRUCE (método propio y barato): recibió gratis en el lanzamiento + vendió en el pico.

Por qué así: auditar wallets enteras es carísimo y muchas veces el trade del token queda fuera de
la ventana. En cambio, dentro del historial del PROPIO token se ve todo:

  · Tx MÁS VIEJAS del mint  -> set A: wallets que recibieron tokens en el lanzamiento (gratis o
                               entrando baratísimo).
  · Tx MÁS NUEVAS del mint  -> set B: wallets que VENDIERON durante el despegue (Δtoken < 0 y
                               ΔSOL > 0), con cuánto SOL cobraron.

  INSIDER = A ∩ B: recibió en el lanzamiento y cobró en el pico. El cruce es la prueba: no compró
  (o compró migajas) y salió con SOL. Se completa con si sigue dentro y quién la fondeó.

Uso:
  python3 insiders_cruce.py --tokens 6 --viejas 250 --nuevas 900 --min-sol 3
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
import buscar_10 as B10              # noqa: E402
import insider_token as IT           # noqa: E402

PAUSA = 0.03


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def analizar_txs(mint, sigs):
    """De una lista de firmas devuelve: {wallet: {libre, vendio_sol, vendio_tok, compro_sol, compro_tok}}."""
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
            e = d.setdefault(owner, {"libre": 0.0, "vendio_sol": 0.0, "vendio_tok": 0.0,
                                     "compro_sol": 0.0, "primera": s.get("blockTime")})
            if d_tok > 0 and d_sol <= -0.2:
                e["compro_sol"] += -d_sol
            elif d_tok > 0 and abs(d_sol) < 0.01:
                e["libre"] += d_tok
            elif d_tok < 0 and d_sol >= 0.2:
                e["vendio_sol"] += d_sol
                e["vendio_tok"] += -d_tok
    return d


def main():
    ap = argparse.ArgumentParser(description="Insiders por cruce (lanzamiento + pico) — método propio")
    ap.add_argument("--tokens", type=int, default=6)
    ap.add_argument("--viejas", type=int, default=250, help="tx del lanzamiento a leer")
    ap.add_argument("--nuevas", type=int, default=900, help="tx del despegue a leer")
    ap.add_argument("--min-sol", type=float, default=3.0, help="SOL cobrados para considerarlo insider")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    log("1/3 tokens del radar que despegaron…")
    tokens = []
    for c in B10.universo_radar(30):
        d = PA.dexscreener(c["mint"]) or {}
        mc = d.get("marketCap") or d.get("fdv") or 0
        if mc < 300_000:
            continue
        tokens.append({"mint": c["mint"], "symbol": c["symbol"], "mcap": mc,
                       "vol24": (d.get("volume") or {}).get("h24") or 0})
        if len(tokens) >= a.tokens:
            break
    for t in tokens:
        log(f"    {t['symbol']:<12} mcap {t['mcap']:>12.0f} · vol24 {t['vol24']:>10.0f}")

    log("2/3 leyendo lanzamiento (viejas) y despegue (nuevas) de cada token…")
    filas, ctx = [], {}
    for t in tokens:
        mint = t["mint"]
        viejas, _p1, _t1 = IT.sigs_mas_viejas(mint, a.viejas, 60)
        nuevas = WT.rpc("getSignaturesForAddress", [mint, {"limit": a.nuevas}]) or []
        A = analizar_txs(mint, viejas)
        B = analizar_txs(mint, nuevas)
        libres = {w: e for w, e in A.items() if e["libre"] > 0}
        ctx[mint] = {"symbol": t["symbol"], "mcap": t["mcap"], "recibieron_libre": len(libres),
                     "vendieron_total": len([1 for e in B.values() if e["vendio_sol"] > 0])}
        log(f"    {t['symbol']:<12} recibieron gratis: {len(libres):>4} · vendieron en el despegue: "
            f"{len([1 for e in B.values() if e['vendio_sol'] > 0]):>4}")
        for w, e in libres.items():
            v = B.get(w)
            cobro = (v or {}).get("vendio_sol", 0.0)
            if cobro < a.min_sol:
                continue
            filas.append({"wallet": w, "symbol": t["symbol"], "mint": mint,
                          "recibio_libre": round(e["libre"], 2), "pago_por_ellos": round(e["compro_sol"], 2),
                          "cobro_sol": round(cobro, 3), "vendio_tokens": round((v or {}).get("vendio_tok", 0), 2),
                          "primera_vez": e.get("primera")})
    filas.sort(key=lambda f: -f["cobro_sol"])
    log(f"    cruce: {len(filas)} wallets recibieron en el lanzamiento Y cobraron ≥{a.min_sol} SOL")

    log("3/3 chequeando si siguen dentro y quién las fondeó…")
    for f in filas[:14]:
        try:
            ten = WT.rpc("getTokenAccountsByOwner", [f["wallet"],
                                                     {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                                                     {"encoding": "jsonParsed"}])
            tiene = 0.0
            for c in ((ten or {}).get("value") or []):
                try:
                    i = c["account"]["data"]["parsed"]["info"]
                    if i["mint"] == f["mint"]:
                        tiene = float(i["tokenAmount"]["uiAmount"] or 0)
                except Exception:
                    continue
            saldo = ((WT.rpc("getBalance", [f["wallet"]]) or {}).get("value") or 0) / 1e9
            f["sigue_teniendo"] = round(tiene, 2)
            f["saldo_sol"] = round(saldo, 3)
            f["fondeo"] = [x.get("funder") for x in IT.funder_de(f["wallet"], 2)]
        except Exception:
            pass

    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tokens": ctx,
               "insiders": filas}, open(os.path.join(AQUI, "insiders_cruce.json"), "w"),
              ensure_ascii=False, indent=1, default=str)

    print("\n=== INSIDERS POR CRUCE (recibió en el lanzamiento + cobró en el pico) ===")
    for f in filas[:14]:
        print(f"\n  💎 {f['symbol']:<10} {f['wallet']}")
        print(f"      recibió {f['recibio_libre']:.0f} tokens sin pagar · puso {f['pago_por_ellos']:.2f} SOL")
        print(f"      COBRÓ {f['cobro_sol']:.2f} SOL ({f['vendio_tokens']:.0f} tokens) · "
              f"sigue teniendo {f.get('sigue_teniendo', '?')} · saldo {f.get('saldo_sol', '?')} SOL")
        if f.get("fondeo"):
            print(f"      🔗 la fondearon: {', '.join([x for x in f['fondeo'] if x])}")
    if not filas:
        print("  (sin cruces: nadie que haya recibido en el lanzamiento vendió ≥ min-sol en la ventana)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
