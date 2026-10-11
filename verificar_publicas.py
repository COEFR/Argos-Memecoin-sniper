#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verificar wallets de LISTAS PÚBLICAS (GMGN/KolScan vía kol-quest) contra la realidad de hoy.

El dataset público (nirholas/kol-quest → `site/data/solwallets.json`) trae 574 wallets etiquetadas
(smart money, KOL, sniper, top dev…) con métricas de GMGN/KolScan, pero **de marzo 2026**: la
etiqueta puede estar muerta y el edge también. Esto las pasa por el mismo motor propio:

  1. Filtro del dataset: ganancia 30 d > 0, winrate ≥ 50 %, hold promedio ≥ 120 s, ≥ 30 operaciones.
  2. Chequeo de VIDA: ¿movió plata en los últimos 14 días? ¿tiene saldo o tokens?
  3. Auditoría con el motor propio (mismo puntaje 0-10, con el filtro anti-suerte de los 3 mejores).

Uso:
  python3 verificar_publicas.py --vida              # sólo fase 1-2 (rápido, barato)
  python3 verificar_publicas.py --auditar 20 --tx 400
  python3 verificar_publicas.py --auditar 8 --tx 1200   # verificación profunda de los mejores
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
import wallet_forense as WF          # noqa: E402
import forense_profunda as FP        # noqa: E402
import red_wallets as RW             # noqa: E402

DATASET = os.path.join(AQUI, "candidatas_publicas.json")
SALIDA = os.path.join(AQUI, "publicas_verificadas.json")


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def cargar_filtradas(min_hold_seg=120, min_tx=30, min_wr=0.5):
    """Filtra el dataset público (hold en SEGUNDOS: GMGN devuelve segundos)."""
    if not os.path.exists(DATASET):
        return []
    crudas = json.load(open(DATASET))
    def num(x, k):
        try:
            return float(x.get(k) or 0)
        except Exception:
            return 0.0
    out = [w for w in crudas
           if num(w, "realized_profit_30d") > 0
           and num(w, "winrate_30d") >= min_wr
           and num(w, "avg_holding_period_30d") >= min_hold_seg
           and num(w, "txs_30d") >= min_tx]
    return out


def estado_actual(d):
    """¿La wallet sigue viva? Devuelve actividad, saldo y cuentas de token con saldo."""
    try:
        saldo = ((WT.rpc("getBalance", [d]) or {}).get("value") or 0) / 1e9
    except Exception:
        saldo = 0.0
    try:
        sigs = WT.rpc("getSignaturesForAddress", [d, {"limit": 10}]) or []
    except Exception:
        sigs = []
    ult = sigs[0].get("blockTime") if sigs else None
    try:
        cuentas = (WT.rpc("getTokenAccountsByOwner",
                          [d, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                           {"encoding": "jsonParsed"}]) or {}).get("value") or []
    except Exception:
        cuentas = []
    con = 0
    for c in cuentas:
        try:
            if float(c["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0) > 0:
                con += 1
        except Exception:
            continue
    return {"saldo_sol": round(saldo, 3), "ultimo_mov": ult, "cuentas_token": len(cuentas),
            "tokens_con_saldo": con}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vida", action="store_true", help="sólo chequeo de vida (rápido)")
    ap.add_argument("--auditar", type=int, default=0, help="cuántas vivas auditar a fondo")
    ap.add_argument("--tx", type=int, default=400)
    ap.add_argument("--dias-viva", type=int, default=14)
    a = ap.parse_args()

    cands = cargar_filtradas()
    log(f"candidatas del dataset público (filtro ganancia+wr+hold≥2min+30 ops): {len(cands)}")
    if not cands:
        return 1
    limite = time.time() - a.dias_viva * 86400
    log("1/2 chequeando si siguen VIVAS (saldo + movimiento reciente)…")
    vivas = []
    for i, c in enumerate(cands, 1):
        d = c.get("wallet_address") or c.get("address")
        e = estado_actual(d)
        c.update(e)
        c["viva"] = bool(e["ultimo_mov"] and e["ultimo_mov"] >= limite)
        if c["viva"]:
            vivas.append(c)
        if i % 25 == 0:
            log(f"   {i}/{len(cands)} · vivas hasta ahora {len(vivas)}")
        time.sleep(0.02)
    log(f"    {len(vivas)} de {len(cands)} siguen activas en los últimos {a.dias_viva} días")
    vivas.sort(key=lambda w: -float(w.get("realized_profit_30d") or 0))
    json.dump(vivas, open(os.path.join(AQUI, "publicas_vivas.json"), "w"), ensure_ascii=False, indent=1)

    if a.vida or not a.auditar:
        print(f"\n=== VIVAS ({len(vivas)}) ordenadas por ganancia histórica ===")
        for w in vivas[:40]:
            f = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%d/%m %H:%M") if t else "?"
            print("%-45s %-11s prof30d %9.0f · wr %3.0f%% · hold %7.0f s · saldo %8.2f SOL · últ %s · %s" % (
                w["wallet_address"], w.get("_fuente", "?"), float(w.get("realized_profit_30d") or 0),
                float(w.get("winrate_30d") or 0) * 100, float(w.get("avg_holding_period_30d") or 0),
                w["saldo_sol"], f(w["ultimo_mov"]), (w.get("twitter_username") or "")))
        return 0

    log(f"2/2 auditando las {a.auditar} mejores con {a.tx} tx…")
    filas = []
    for i, w in enumerate(vivas[:a.auditar], 1):
        d = w["wallet_address"]
        try:
            sigs = FP.todas_las_firmas(d, a.tx)
            cache = FP.descargar(d, sigs, os.path.join(AQUI, f"cache_tx_{d[:8]}.json"))
            an = FP.analizar(d, cache, sigs)
            ten = WF.tenencias_actuales(d)
            cer, ab = FP.posiciones(an["tokens"], ten)
            met = FP.metricas(cer, ab, w["saldo_sol"])
            nota, ok, no = RW.puntaje_10(met)
        except Exception as e:
            log(f"  ! error {d[:10]}: {e}")
            met, nota, ok, no = None, 0.0, [], ["error"]
        fila = {**w, "nota": nota, "aprobados": ok, "fallados": no, "tx_leidas": len(cache) if met else 0,
                "cerradas": (met or {}).get("cerradas"), "pnl": (met or {}).get("pnl_neto_sol"),
                "wr_real": (met or {}).get("win_rate"), "roi": (met or {}).get("roi_total"),
                "hold_real": (met or {}).get("min_tenencia_mediano"),
                "t_real": (met or {}).get("t_stat"), "sin_top3": (met or {}).get("pnl_sin_top3_sol"),
                "sin_compra": (met or {}).get("pct_sin_compra")}
        filas.append(fila)
        log(f"  {i}/{a.auditar} {d[:12]}… nota {nota}/10 · {fila['tx_leidas']} tx · "
            f"{fila['cerradas']} cerradas · pnl {fila['pnl']} SOL · wr {fila['wr_real']}% · "
            f"t {fila['t_real']} · sin top3 {fila['sin_top3']}")
    filas.sort(key=lambda f: -f["nota"])
    json.dump(filas, open(SALIDA, "w"), ensure_ascii=False, indent=1, default=str)
    log(f"guardado en {SALIDA}")
    print("\n=== LISTAS PÚBLICAS, VERIFICADAS HOY (nota propia 0-10) ===")
    for f in filas:
        print(f"\n{f['nota']:>4}/10  {f['wallet_address']}  ({f.get('_fuente')})")
        print(f"      dataset: prof30d {float(f.get('realized_profit_30d') or 0):.0f} · "
              f"wr {float(f.get('winrate_30d') or 0)*100:.0f}% · hold {float(f.get('avg_holding_period_30d') or 0):.0f} s · "
              f"{f.get('twitter_username') or '-'}")
        print(f"      hoy: {f['tx_leidas']} tx · {f['cerradas']} cerradas · PnL {f['pnl']} SOL · "
              f"wr {f['wr_real']}% · ROI {f['roi']}% · hold {f['hold_real']} min · t {f['t_real']} · "
              f"sin 3 mejores {f['sin_top3']} SOL · saldo {f['saldo_sol']}")
        if f["fallados"]:
            print(f"      ❌ {' · '.join(f['fallados'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
