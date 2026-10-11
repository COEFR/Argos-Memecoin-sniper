#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Red de una wallet — saca las wallets CONECTADAS y las puntúa 0-10.

Para qué: el usuario pasa una wallet y quiere saber si alguna de las wallets que se mueven con
ella sirve de verdad (puntaje 10/10 = pasa TODOS los filtros del proyecto).

Qué hace:
  1. Toma los flujos (quién le mandó SOL / a quién le mandó) del caché de `forense_profunda.py`
     si existe (gratis) o los calcula on-chain.
  2. Descarta lo que NO es una wallet de persona: cuentas que no son del System Program
     (vaults de pools en wSOL, PDAs del programa de fees de pump.fun, cuentas de token…),
     las que no existen y las que están vacías (<0,02 SOL).
  3. Audita cada candidata con `auditor_wallets.auditar_wallet` (compras/ventas, PnL, win rate,
     hold, concentración, t-stat, % sin compra, fondeo fresco).
  4. Puntúa 0-10 con los filtros del proyecto y ordena. Marca SEGUIR / VIGILAR / EVITAR.

Uso:
  python3 red_wallets.py <wallet>                 # top 10 candidatas, 150 tx cada una
  python3 red_wallets.py <wallet> --max 6 --tx 80 # más rápido
  python3 red_wallets.py <wallet> --cargar        # carga al tracker las que pasen los filtros
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT           # noqa: E402
import wallet_forense as WF           # noqa: E402
import auditor_wallets as AU          # noqa: E402

SISTEMA = "11111111111111111111111111111111"


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def es_wallet(dir_):
    """Devuelve {'sol': x} si es una wallet normal con plata; None si es programa/PDA/vacía."""
    try:
        v = (WT.rpc("getAccountInfo", [dir_, {"encoding": "jsonParsed"}]) or {}).get("value")
    except Exception:
        return None
    if not v:
        return None
    if (v.get("owner") or "") != SISTEMA:        # vault de pool, PDA, cuenta de token…
        return None
    sol = (v.get("lamports") or 0) / 1e9
    if sol < 0.02:
        return None
    return {"sol": round(sol, 3)}


def puntaje_10(a):
    """0-10 con los filtros del proyecto. Devuelve (nota, aprobados, fallados)."""
    if not a or not a.get("cerradas"):
        return 0.0, [], ["sin posiciones cerradas en la ventana"]
    ok, no = [], []
    p = 0.0
    def check(cond, peso, bien, mal):
        nonlocal p
        if cond:
            p += peso
            ok.append(bien)
        else:
            no.append(mal)
    check(a["pnl_neto_sol"] > 0, 2.0, "gana plata", f"pierde ({a['pnl_neto_sol']:+.1f} SOL)")
    check((a.get("roi_total") or 0) > 0, 1.5, "ROI positivo", f"ROI {a.get('roi_total')}%")
    check(a["cerradas"] >= 30, 1.5, f"{a['cerradas']} cerradas", f"muestra chica ({a['cerradas']})")
    check(45 <= (a.get("win_rate") or 0) <= 80, 1.0,
          f"win rate {a['win_rate']:.0f}%", f"win rate {a.get('win_rate')}% (fuera de 45-80)")
    check((a.get("concentracion_pnl") or 100) < 50 and (a.get("pnl_sin_top3_sol") is None
                                                       or a.get("pnl_sin_top3_sol") > 0), 1.0,
          "resultado repartido",
          ("el resultado depende de 2-3 trades: sin sus 3 mejores queda en "
           f"{a.get('pnl_sin_top3_sol')} SOL") if (a.get("pnl_sin_top3_sol") is not None
                                                   and a.get("pnl_sin_top3_sol") <= 0)
          else f"{a.get('concentracion_pnl')}% del PnL en un solo trade")
    check((a.get("t_stat") or 0) >= 1.645, 1.0, f"t={a['t_stat']} (significativo)",
          f"t={a.get('t_stat')} (sin significancia)")
    check(a.get("pct_sin_compra", 100) <= 20, 1.0, "compra con su plata",
          f"{a.get('pct_sin_compra')}% de posiciones sin compra (bundle/insider)")
    check(not str(a.get("copiable", "NO")).startswith("NO"), 1.0,
          f"hold {a.get('min_tenencia_mediano')} min (copiable)", f"hold {a.get('min_tenencia_mediano')} min: no copiable")
    check(not a.get("fondeo_fresco"), 0.0 if not a.get("fondeo_fresco") else -1.0,
          "historial con recorrido", "fondeo fresco (sin track record)")
    return round(max(0.0, p), 1), ok, no


def candidatas(wallet, max_tx):
    """Flujos de la wallet: caché de forense_profunda si está, si no on-chain."""
    cache_path = os.path.join(AQUI, f"cache_tx_{wallet[:8]}.json")
    recibido, enviado = {}, {}
    if os.path.exists(cache_path):
        tx = json.load(open(cache_path)).get("tx", {})
        log(f"usando el caché ({len(tx)} tx) para los flujos")
        for v in tx.values():
            pre, post = v.get("pre") or [], v.get("post") or []
            ts = v.get("ts") or 0
            for i, k in enumerate(v.get("keys") or []):
                if k == wallet or k in WF.IGNORAR or i >= len(pre) or i >= len(post):
                    continue
                d = (post[i] - pre[i]) / 1e9
                if abs(d) < WF.MIN_SOL:
                    continue
                reg = recibido if d > 0 else enviado
                e = reg.setdefault(k, {"sol": 0.0, "tx": 0})
                e["sol"] += abs(d)
                e["tx"] += 1
    else:
        log(f"sin caché: bajando flujos on-chain ({max_tx} tx)")
        _, recibido, enviado = WF.flujos_sol(wallet, max_tx)
    peso = {}
    for w, d in recibido.items():
        peso[w] = peso.get(w, 0) + d["sol"] * 1.0
    for w, d in enviado.items():
        peso[w] = peso.get(w, 0) + d["sol"] * 0.7
    return sorted(peso.items(), key=lambda kv: -kv[1]), recibido, enviado


def main():
    ap = argparse.ArgumentParser(description="Wallets conectadas, puntuadas 0-10")
    ap.add_argument("wallet")
    ap.add_argument("--max", type=int, default=10, help="cuántas candidatas auditar")
    ap.add_argument("--tx", type=int, default=150, help="transacciones por candidata")
    ap.add_argument("--desde", type=int, default=1, help="empezar en la candidata N (para seguir)")
    ap.add_argument("--escanear", type=int, default=150,
                    help="cuántas contrapartes chequear con getAccountInfo (las que no son wallets se descartan)")
    ap.add_argument("--cargar", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    w = a.wallet

    log(f"RED DE {w}")
    orden, recibido, enviado = candidatas(w, 300)
    log(f"contrapartes con SOL: {len(orden)} · revisando si son wallets de verdad…")
    reales = []
    for j, (d, p) in enumerate(orden[:a.escanear], 1):
        info = es_wallet(d)
        if info:
            reales.append({"wallet": d, "peso": round(p, 2), **info})
        if j % 50 == 0:
            log(f"    chequeadas {j}/{min(a.escanear, len(orden))} · wallets reales hasta ahora: {len(reales)}")
        if len(reales) >= a.max:
            break
    log(f"wallets reales con plata: {len(reales)} (el resto son pools, PDAs o cuentas vacías)")

    filas = []
    for i, r in enumerate(reales, 1):
        log(f"  auditando {i}/{len(reales)} {r['wallet'][:12]}… ({a.tx} tx)")
        try:
            m = AU.auditar_wallet(r["wallet"], a.tx)
        except Exception as e:
            log(f"    ! error: {e}")
            m = None
        nota, ok, no = puntaje_10(m)
        fila = {"wallet": r["wallet"], "saldo_sol": r["sol"], "peso_flujo": r["peso"],
                "nota": nota, "aprobados": ok, "fallados": no,
                "cerradas": (m or {}).get("cerradas", 0), "pnl": (m or {}).get("pnl_neto_sol"),
                "win_rate": (m or {}).get("win_rate"), "roi": (m or {}).get("roi_total"),
                "hold": (m or {}).get("min_tenencia_mediano"), "t_stat": (m or {}).get("t_stat"),
                "concentracion": (m or {}).get("concentracion_pnl"),
                "sin_compra": (m or {}).get("pct_sin_compra"),
                "fondeo_fresco": (m or {}).get("fondeo_fresco"),
                "fondeo": (m or {}).get("fondeo_principal")}
        filas.append(fila)
        log(f"    nota {nota}/10 · cerradas {fila['cerradas']} · pnl {fila['pnl']} SOL · wr {fila['win_rate']}%")

    filas.sort(key=lambda f: -f["nota"])
    salida = os.path.join(AQUI, f"red_{w[:8]}.json")
    json.dump({"wallet": w, "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "candidatas": filas}, open(salida, "w"), ensure_ascii=False, indent=2, default=str)
    log(f"guardado en {salida}")

    if a.json:
        print(json.dumps(filas, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"\n=== WALLETS CONECTADAS A {w[:12]}… (puntuadas sobre 10) ===")
        for f in filas:
            veredicto = "SEGUIR ⭐" if f["nota"] >= 8 else ("VIGILAR 🔎" if f["nota"] >= 5 else "EVITAR 🚫")
            print(f"\n{f['nota']:>4}/10  {veredicto}  {f['wallet']}")
            print(f"      saldo {f['saldo_sol']} SOL · flujo con la original {f['peso_flujo']} SOL · "
                  f"{f['cerradas']} cerradas · PnL {f['pnl']} SOL · wr {f['win_rate']}% · "
                  f"ROI {f['roi']}% · hold {f['hold']} min · t {f['t_stat']}")
            if f["aprobados"]:
                print(f"      ✅ {' · '.join(f['aprobados'])}")
            if f["fallados"]:
                print(f"      ❌ {' · '.join(f['fallados'])}")

    if a.cargar:
        buenas = [f for f in filas if f["nota"] >= 8]
        for f in buenas[:5]:
            etq = f"⭐CANDIDATA {f['nota']:.0f}10 pnl{f['pnl']:+.1f}SOL wr{f['win_rate']:.0f}%"
            os.system(f'cd {AQUI} && python3 wallet_tracker.py --agregar {f["wallet"]} "{etq}"')
            log(f"cargada al tracker: {etq} · {f['wallet']}")
        if not buenas:
            log("ninguna llegó a 8/10: no se cargó nada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
