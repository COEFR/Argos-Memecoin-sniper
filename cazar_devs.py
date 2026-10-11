#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Los DEVS/creadores de los tokens que despegaron — info privilegiada por definición.

Por qué: el cruce "recibió gratis -> vendió" dio vacío porque los que reciben son cáscaras. Pero hay
alguien que SABE ANTES QUE NADIE y siempre está: **el creador del token**. Tiene:
  · el dev buy del arranque (por definición, la entrada más temprana posible),
  · las creator fees (le entran SOL mientras el token opera),
  · y si es serial, lanza otro token y vuelve a hacer lo mismo.

Se audita cada creador con el motor propio (PnL, cerradas, hold), se mira su resultado EN SU token,
si todavía tiene la posición, y su clúster (quién lo fondeó y a quién le manda).

Uso:
  python3 cazar_devs.py --tokens 8 --tx 400
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


def main():
    ap = argparse.ArgumentParser(description="Creadores (insiders) de los tokens que despegaron")
    ap.add_argument("--tokens", type=int, default=8)
    ap.add_argument("--tx", type=int, default=400)
    a = ap.parse_args()

    log("1/4 tokens del radar que despegaron…")
    tokens, vistos = [], set()
    for c in B10.universo_radar(40):
        d = PA.dexscreener(c["mint"]) or {}
        mc = d.get("marketCap") or d.get("fdv") or 0
        if mc < 250_000:
            continue
        tokens.append({"mint": c["mint"], "symbol": c["symbol"], "mcap": mc,
                       "vol24": (d.get("volume") or {}).get("h24") or 0,
                       "creado": (d.get("pairCreatedAt") or 0) / 1000 or None})
        if len(tokens) >= a.tokens:
            break
    for t in tokens:
        log(f"    {t['symbol']:<12} mcap {t['mcap']:>12.0f} · vol24 {t['vol24']:>10.0f}")

    log("2/4 sacando el CREADOR de cada token (RugCheck)…")
    for t in tokens:
        rc = PA.rugcheck(t["mint"]) or {}
        t["creador"] = rc.get("creator")
        t["balance_creador"] = rc.get("creatorBalance")
        t["tokens_del_creador"] = len(rc.get("creatorTokens") or [])
        t["holders"] = rc.get("totalHolders")
        log(f"    {t['symbol']:<12} creador {str(t['creador'])[:14]} · tokens previos {t['tokens_del_creador']} "
            f"· holders {t['holders']}")

    log(f"3/4 auditando a los creadores ({a.tx} tx cada uno)…")
    fichas = []
    for t in tokens:
        w = t.get("creador")
        if not w or w in vistos:
            continue
        vistos.add(w)
        try:
            m = AU.auditar_wallet(w, min(a.tx, 1000))
        except Exception as e:
            log(f"    ! {str(w)[:10]}: {e}")
            m = None
        det = None
        for f in ((m or {}).get("detalle_cerradas") or []):
            if f.get("mint") == t["mint"]:
                det = f
                break
        try:
            ten = WT.rpc("getTokenAccountsByOwner", [w, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                                                     {"encoding": "jsonParsed"}])
            tiene = 0.0
            for c in ((ten or {}).get("value") or []):
                try:
                    i = c["account"]["data"]["parsed"]["info"]
                    if i["mint"] == t["mint"]:
                        tiene = float(i["tokenAmount"]["uiAmount"] or 0)
                except Exception:
                    continue
        except Exception:
            tiene = None
        saldo = ((WT.rpc("getBalance", [w]) or {}).get("value") or 0) / 1e9
        ficha = {**t, "etiqueta": CW.etiquetar(m), "cerradas": (m or {}).get("cerradas"),
                 "pnl_total": (m or {}).get("pnl_neto_sol"), "win_rate": (m or {}).get("win_rate"),
                 "hold": (m or {}).get("min_tenencia_mediano"),
                 "su_token_pnl": (det or {}).get("pnl"), "su_token_in": (det or {}).get("sol_in"),
                 "su_token_out": (det or {}).get("sol_out"),
                 "tiene_su_token": tiene, "saldo_sol": round(saldo, 3),
                 "fondeo": [(x.get("funder") or "") for x in IT.funder_de(w, 2)][:3]}
        fichas.append(ficha)
        log(f"    {t['symbol']:<10} creador {w[:12]}… {ficha['etiqueta']['emoji']} {ficha['etiqueta']['tipo']:<9} "
            f"cerradas {ficha['cerradas']} · pnl total {ficha['pnl_total']} · wr {ficha['win_rate']}% · "
            f"en su token {ficha['su_token_pnl']} · tiene {ficha['tiene_su_token']} · saldo {ficha['saldo_sol']}")

    log("4/4 buscando el clúster de cada creador (otras wallets que fondeó)…")
    for f in fichas[:6]:
        try:
            sigs = WT.rpc("getSignaturesForAddress", [f["creador"], {"limit": 40}]) or []
            hijas = set()
            for s in sigs[:20]:
                tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                                "encoding": "jsonParsed"}])
                time.sleep(PAUSA)
                if not tx or not tx.get("meta"):
                    continue
                meta = tx["meta"]
                keys = WT.cuentas_tx(tx)
                pre_b, post_b = meta.get("preBalances") or [], meta.get("postBalances") or []
                idx = keys.index(f["creador"]) if f["creador"] in keys else None
                if idx is None or idx >= len(pre_b):
                    continue
                if (post_b[idx] - pre_b[idx]) / 1e9 > -0.5:
                    continue
                for j, k in enumerate(keys):
                    if k == f["creador"] or k in AU.AU_IGNORAR or j >= len(pre_b):
                        continue
                    if (post_b[j] - pre_b[j]) / 1e9 >= 0.5:
                        hijas.add(k)
            f["cluster"] = sorted(hijas)[:6]
            log(f"    {f['symbol']:<10} creador mandó SOL a {len(hijas)} wallets (mostrando {len(f['cluster'])})")
        except Exception as e:
            f["cluster"] = []
            log(f"    ! clúster {str(f.get('creador'))[:10]}: {e}")

    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "creadores": fichas},
              open(os.path.join(AQUI, "devs_insiders.json"), "w"), ensure_ascii=False, indent=1, default=str)

    print("\n=== CREADORES (info privilegiada por definición) ===")
    for f in sorted(fichas, key=lambda x: -(x.get("pnl_total") or 0)):
        print(f"\n  👤 {f['symbol']:<10} {f['creador']}")
        print(f"      mcap {f['mcap']:.0f} · holders {f.get('holders')} · tokens previos del creador: {f.get('tokens_del_creador')}")
        print(f"      su wallet: {f['cerradas']} cerradas · PnL total {f['pnl_total']} SOL · wr {f['win_rate']}% · "
              f"hold {f['hold']} min · {f['etiqueta']['emoji']} {f['etiqueta']['tipo']}")
        print(f"      en SU token: metió {f['su_token_in']} y sacó {f['su_token_out']} = {f['su_token_pnl']} SOL · "
              f"tiene ahora {f['tiene_su_token']} · saldo {f['saldo_sol']} SOL")
        if f.get("cluster"):
            print(f"      🔗 mandó SOL a: {', '.join(f['cluster'][:4])}")
        if f.get("fondeo"):
            print(f"      🔗 la fondearon: {', '.join([x for x in f['fondeo'] if x])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
