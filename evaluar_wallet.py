#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Evaluar una wallet con historial PROFUNDO (paginado) y darle la nota 0-10.

Usa la tubería de `forense_profunda.py` (paginado completo + caché en disco) porque
`auditor_wallets.auditar_wallet` pide `limit: max_tx` directo y el RPC **rechaza límites >1000**
(devuelve None y la wallet parece "sin posiciones en la ventana").

Uso:
  python3 evaluar_wallet.py <wallet> --tx 2000      # baja (o usa caché) y puntúa
  python3 evaluar_wallet.py <wallet> --desde-cache  # sólo relee el caché (cero RPC)
"""
import argparse
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT          # noqa: E402
import wallet_forense as WF          # noqa: E402
import forense_profunda as FP        # noqa: E402
import red_wallets as RW             # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("--tx", type=int, default=2000)
    ap.add_argument("--desde-cache", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    w = a.wallet
    cache_path = os.path.join(AQUI, f"cache_tx_{w[:8]}.json")

    if a.desde_cache:
        cache = json.load(open(cache_path)).get("tx", {}) if os.path.exists(cache_path) else {}
        sigs = [{"signature": k, "blockTime": v.get("ts")} for k, v in cache.items()]
        print(f"modo caché: {len(cache)} tx en disco")
        saldo = 0
        tenencias = {}
    else:
        sigs = FP.todas_las_firmas(w, a.tx)
        if not sigs:
            print(f"{w}: sin firmas")
            return 1
        cache = FP.descargar(w, sigs, cache_path)
        saldo = ((WT.rpc("getBalance", [w]) or {}).get("value") or 0) / 1e9
        tenencias = WF.tenencias_actuales(w)

    d = FP.analizar(w, cache, sigs)
    cerradas, abiertas = FP.posiciones(d["tokens"], tenencias)
    met = FP.metricas(cerradas, abiertas, saldo)
    nota, ok, no = RW.puntaje_10(met)

    if a.json:
        print(json.dumps({"wallet": w, "nota": nota, "aprobados": ok, "fallados": no,
                          "metricas": met, "tx_leidas": len(cache)}, ensure_ascii=False,
                         indent=2, default=str))
        return 0
    print(f"\n=== {w} · {len(cache)} tx leídas (pedidas {len(sigs)}) ===")
    print(f"NOTA: {nota}/10")
    print(f"  cerradas {met.get('cerradas')} · abiertas {met.get('abiertas')} · PnL {met.get('pnl_neto_sol')} SOL "
          f"sobre {met.get('invertido_sol')} invertidos · ROI {met.get('roi_total')}%")
    print(f"  win rate {met.get('win_rate')}% · hold mediano {met.get('min_tenencia_mediano')} min · "
          f"t {met.get('t_stat')} · concentración {met.get('concentracion_pnl')}% · payoff {met.get('payoff')}")
    print(f"  sin compra {met.get('pct_sin_compra')}% · copiable: {met.get('copiable')}")
    print(f"  ✅ {' · '.join(ok) if ok else '-'}")
    print(f"  ❌ {' · '.join(no) if no else '-'}")
    print(f"  mejores: {[(c.get('symbol') or c['mint'][:6], round(c['pnl'], 2)) for c in cerradas[:3]]}")
    print(f"  peores : {[(c.get('symbol') or c['mint'][:6], round(c['pnl'], 2)) for c in cerradas[-3:]]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
