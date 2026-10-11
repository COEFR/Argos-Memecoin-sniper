#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Los DISTRIBUIDORES: quién reparte los tokens en el lanzamiento (el operador con info privilegiada).

Hallazgo previo: los que RECIBEN los tokens son cáscaras vacías y los CREADORES son wallets frescas
desechables. Pero alguien tiene que **enviar** esos tokens a miles de wallets: esa wallet es el
operador del reparto — el que sabe antes que nadie — y esa sí es rastreable.

Este script lee los envíos del lanzamiento (token sale de una wallet sin que entre SOL) y agrupa por
REMITENTE: cuántos wallets fondeó, cuántos tokens repartió, y si el reparto lo hizo desde una sola
cuenta (hub) o desde varias (operación grande).

Uso:
  python3 cazar_distribuidores.py --tokens 6 --tx 400 --min-envios 5
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
import insider_token as IT           # noqa: E402

PAUSA = 0.03


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def envios_del_arranque(mint, cuantas=120, max_paginas=40):
    """Quién ENVÍA tokens del mint sin recibir SOL: el que reparte."""
    sigs, paginas, total = IT.sigs_mas_viejas(mint, cuantas, max_paginas)
    remitentes = {}
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
        suben, bajan = [], []
        for owner in set(pre_t) | set(post_t):
            if owner in AU.AU_IGNORAR or owner not in keys:
                continue
            d = post_t.get(owner, 0.0) - pre_t.get(owner, 0.0)
            if d > 0:
                i = keys.index(owner)
                ds = (post_b[i] - pre_b[i]) / 1e9 if (i < len(pre_b) and i < len(post_b)) else 0.0
                if abs(ds) < 0.01:
                    suben.append((owner, d))
            elif d < 0:
                bajan.append((owner, -d))
        for (rec, tok) in suben:
            for (env, cant) in bajan:
                if env == rec:
                    continue
                e = remitentes.setdefault(env, {"envios": 0, "tokens": 0.0, "hacia": set()})
                e["envios"] += 1
                e["tokens"] += min(cant, tok)
                e["hacia"].add(rec)
    return remitentes, {"tx_total": total, "paginas": paginas}


def main():
    ap = argparse.ArgumentParser(description="Distribuidores del lanzamiento (operador insider)")
    ap.add_argument("--tokens", type=int, default=6)
    ap.add_argument("--tx", type=int, default=400)
    ap.add_argument("--arranque", type=int, default=120)
    ap.add_argument("--min-envios", type=int, default=5)
    a = ap.parse_args()

    log("1/3 tokens del radar que despegaron…")
    tokens = []
    for c in B10.universo_radar(40):
        d = PA.dexscreener(c["mint"]) or {}
        mc = d.get("marketCap") or d.get("fdv") or 0
        if mc < 200_000:
            continue
        tokens.append({"mint": c["mint"], "symbol": c["symbol"], "mcap": mc})
        if len(tokens) >= a.tokens:
            break
    for t in tokens:
        log(f"    {t['symbol']:<12} mcap {t['mcap']:>12.0f}")

    log("2/3 leyendo el reparto del lanzamiento y agrupando por REMITENTE…")
    acum = defaultdict(lambda: {"envios": 0, "tokens": 0.0, "tokens_en": set(), "hacia": set()})
    for t in tokens:
        rem, meta = envios_del_arranque(t["mint"], a.arranque)
        log(f"    {t['symbol']:<12} remitentes en el arranque: {len(rem)} · "
            f"repartieron a {sum(len(v['hacia']) for v in rem.values())} wallets")
        for w, v in rem.items():
            e = acum[w]
            e["envios"] += v["envios"]
            e["tokens"] += v["tokens"]
            e["tokens_en"].add(t["symbol"])
            e["hacia"] |= v["hacia"]

    orden = sorted(acum.items(), key=lambda kv: (-len(kv[1]["tokens_en"]), -kv[1]["envios"]))
    log(f"    {len(orden)} remitentes en total · con ≥{a.min_envios} envíos: "
        f"{len([1 for _, v in orden if v['envios'] >= a.min_envios])}")

    log(f"3/3 auditando a los repartidores más grandes ({a.tx} tx)…")
    fichas = []
    for w, v in orden[:10]:
        if v["envios"] < a.min_envios:
            continue
        try:
            m = AU.auditar_wallet(w, min(a.tx, 1000))
        except Exception as e:
            log(f"    ! {w[:10]}: {e}")
            m = None
        saldo = ((WT.rpc("getBalance", [w]) or {}).get("value") or 0) / 1e9
        sigs = WT.rpc("getSignaturesForAddress", [w, {"limit": 5}]) or []
        ult = sigs[0].get("blockTime") if sigs else None
        ficha = {"wallet": w, "envios": v["envios"], "tokens_repartidos": round(v["tokens"], 2),
                 "tokens_donde_repartio": sorted(v["tokens_en"]), "wallets_destino": len(v["hacia"]),
                 "saldo_sol": round(saldo, 3), "ultimo_mov": ult,
                 "etiqueta": CW.etiquetar(m), "cerradas": (m or {}).get("cerradas"),
                 "pnl": (m or {}).get("pnl_neto_sol"), "win_rate": (m or {}).get("win_rate"),
                 "sin_compra": (m or {}).get("pct_sin_compra"),
                 "hold": (m or {}).get("min_tenencia_mediano"),
                 "fondeo": [(x.get("funder") or "") for x in IT.funder_de(w, 2)][:3]}
        fichas.append(ficha)
        log(f"    {w[:14]}… envíos {v['envios']:>4} · repartió a {len(v['hacia']):>5} wallets en "
            f"{len(v['tokens_en'])} tokens · {ficha['etiqueta']['emoji']} {ficha['etiqueta']['tipo']:<9} · "
            f"cerradas {ficha['cerradas']} · pnl {ficha['pnl']} · saldo {ficha['saldo_sol']}")

    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "distribuidores": fichas},
              open(os.path.join(AQUI, "distribuidores.json"), "w"), ensure_ascii=False, indent=1, default=str)

    print("\n=== DISTRIBUIDORES (el que reparte los tokens del lanzamiento) ===")
    for f in fichas:
        print(f"\n  🏭 {f['wallet']}")
        print(f"      repartió en {len(f['tokens_donde_repartio'])} tokens ({', '.join(f['tokens_donde_repartio'])}): "
              f"{f['envios']} envíos a {f['wallets_destino']} wallets distintas · {f['tokens_repartidos']:.0f} tokens")
        print(f"      su wallet: saldo {f['saldo_sol']} SOL · {f['cerradas']} cerradas · PnL {f['pnl']} · "
              f"wr {f['win_rate']}% · sin compra {f['sin_compra']}% · hold {f['hold']} · "
              f"{f['etiqueta']['emoji']} {f['etiqueta']['tipo']}")
        if f.get("fondeo"):
            print(f"      🔗 la fondearon: {', '.join([x for x in f['fondeo'] if x])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
