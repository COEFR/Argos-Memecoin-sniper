#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifica el clúster fondeado por los devs: ¿son wallets que operan con info privilegiada?"""
import json
import os
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT        # noqa: E402
import auditor_wallets as AU       # noqa: E402
import clasificar_wallets as CW    # noqa: E402
import insider_token as IT         # noqa: E402

WALLETS = [l.strip() for l in open('/tmp/cluster_devs.txt') if l.strip()]
TX = int(sys.argv[1]) if len(sys.argv) > 1 else 300
SISTEMA = "11111111111111111111111111111111"


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def es_wallet(dir_):
    """OJO: sin este chequeo se auditan cuentas de programa (ej.: cuentas del programa de fees de
    pump.fun) y el parser devuelve movimientos falsos. Pasó: se reportó un 'insider +81,75 SOL' que
    en realidad era una cuenta del programa de fees con 99 de 100 tx fallidas.
    Sólo cuentan como wallet las cuentas del System Program con lamports.
    """
    v = (WT.rpc("getAccountInfo", [dir_, {"encoding": "jsonParsed"}]) or {}).get("value")
    if not v:
        return False, "no existe"
    if (v.get("owner") or "") != SISTEMA:
        return False, f"cuenta de programa ({(v.get('owner') or '')[:20]})"
    return True, ""


filas = []
for i, w in enumerate(WALLETS, 1):
    ok, motivo = es_wallet(w)
    if not ok:
        log(f"  {i}/{len(WALLETS)} {w[:14]}… DESCARTADA: {motivo}")
        continue
    try:
        m = AU.auditar_wallet(w, TX)
    except Exception as e:
        log(f"  ! {w[:10]}: {e}")
        m = None
    saldo = ((WT.rpc("getBalance", [w]) or {}).get("value") or 0) / 1e9
    sigs = WT.rpc("getSignaturesForAddress", [w, {"limit": 10}]) or []
    ult = sigs[0].get("blockTime") if sigs else None
    ten = (WT.rpc("getTokenAccountsByOwner", [w, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                                              {"encoding": "jsonParsed"}]) or {}).get("value") or []
    tok = []
    for c in ten:
        try:
            i2 = c["account"]["data"]["parsed"]["info"]
            a = float(i2["tokenAmount"]["uiAmount"] or 0)
            if a > 0:
                tok.append((i2["mint"][:8], round(a, 2)))
        except Exception:
            continue
    f = {"wallet": w, "etiqueta": CW.etiquetar(m), "cerradas": (m or {}).get("cerradas"),
         "pnl": (m or {}).get("pnl_neto_sol"), "win_rate": (m or {}).get("win_rate"),
         "roi": (m or {}).get("roi_total"), "hold": (m or {}).get("min_tenencia_mediano"),
         "sin_compra": (m or {}).get("pct_sin_compra"), "t": (m or {}).get("t_stat"),
         "detalle": (m or {}).get("detalle_cerradas") or [],
         "saldo_sol": round(saldo, 3), "ultimo_mov": ult, "tokens": tok[:6],
         "fondeo": [(x.get("funder") or "") for x in IT.funder_de(w, 2)][:3]}
    filas.append(f)
    log(f"  {i}/{len(WALLETS)} {w[:14]}… {f['etiqueta']['emoji']} {f['etiqueta']['tipo']:<9} "
        f"cerradas {f['cerradas']} · pnl {f['pnl']} · wr {f['win_rate']}% · hold {f['hold']} · "
        f"saldo {f['saldo_sol']} · tokens {len(tok)}")

json.dump(filas, open(os.path.join(AQUI, "cluster_devs_verificado.json"), "w"), ensure_ascii=False,
          indent=1, default=str)
print("\n=== CLÚSTER FONDEADO POR LOS DEVS ===")
for f in sorted(filas, key=lambda x: -(x["pnl"] or -999)):
    print(f"\n  {f['etiqueta']['emoji']} {f['etiqueta']['tipo']:<9} {f['wallet']}")
    print(f"      cerradas {f['cerradas']} · PnL {f['pnl']} SOL · wr {f['win_rate']}% · ROI {f['roi']}% · "
          f"hold {f['hold']} min · sin compra {f['sin_compra']}% · t {f['t']}")
    print(f"      saldo {f['saldo_sol']} SOL · tokens que tiene: {f['tokens']}")
    for d in (f['detalle'] or [])[:4]:
        print(f"        · {d.get('mint','')[:10]} pnl {d.get('pnl')} roi {d.get('roi')} "
              f"in {d.get('sol_in')} out {d.get('sol_out')}")
