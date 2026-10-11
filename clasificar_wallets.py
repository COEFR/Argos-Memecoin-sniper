#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clasificador de wallets para el tracker — devuelve la lista ETIQUETADA:
  🚨 INSIDER     · vendió tokens que nunca compró (recibidos de un bundle) / fondeo del dev
  🤖 BOT/MEV     · hold <2 min, muchas operaciones, ritmo de máquina
  📤 PASARELA    · recibe de muchos y reparte a muchos, casi no opera tokens
  📉 PIERDE      · compra de verdad pero pierde → NO seguir
  ⭐ CANDIDATA   · compró con su propia plata, resultado positivo y hold humano → a verificar
  ❓ SIN DATOS   · no hay operaciones cerradas en la ventana revisada

Uso:
  python3 clasificar_wallets.py --tokens 30 --clasificar 12          # solo lista
  python3 clasificar_wallets.py --tokens 30 --clasificar 12 --cargar # y las mete en el tracker
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import auditor_wallets as AU          # noqa: E402
import wallet_tracker as WT           # noqa: E402

SALIDA = os.path.join(AQUI, "wallets_clasificadas.json")


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def candidatas_por_compradores(ganadores, tx_por_pool=10, min_compras=1):
    """DESCOBERTA ÚTIL: saca los que COMPRARON de verdad leyendo las transacciones del pool.

    A diferencia de mirar los holders (que trae insiders con tokens regalados), acá solo entran
    wallets que pusieron su propio SOL en el mercado. Es el filtro que separa trader de beneficiario.
    """
    compras = {}
    for i, c in enumerate(ganadores, 1):
        pool = c.get("pool_address") or c.get("bonding_curve")
        if not pool:
            continue
        sigs = WT.rpc("getSignaturesForAddress", [pool, {"limit": tx_por_pool}]) or []
        for s in sigs:
            if s.get("err"):
                continue
            tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                           "encoding": "jsonParsed"}])
            time.sleep(0.08)
            if not tx or not tx.get("meta"):
                continue
            for pub in WT.cuentas_tx(tx):
                if pub in AU.AU_IGNORAR:
                    continue
                ev = WT.parsear_tx(tx, pub)
                if ev and ev["accion"] == "compra" and ev["mint"] == c["mint"] and abs(ev["sol"]) >= 0.05:
                    e = compras.setdefault(pub, {"veces": 0, "tokens": [], "sol": 0.0})
                    e["veces"] += 1
                    e["tokens"].append(c.get("symbol") or "?")
                    e["sol"] += abs(ev["sol"])
        if i % 5 == 0:
            log(f"  pools leídos: {i}/{len(ganadores)} · compradores únicos: {len(compras)}")
    orden = [(v, k) for k, v in compras.items() if v["veces"] >= min_compras]
    orden.sort(key=lambda kv: (-kv[0]["veces"], -kv[0]["sol"]))
    return {w: d for d, w in orden}


def etiquetar(a):
    """Traduce las métricas a una etiqueta con emoji y una razón legible."""
    if not a or not a.get("cerradas"):
        return {"emoji": "❓", "tipo": "SIN DATOS",
                "razon": "no tiene operaciones cerradas en la ventana revisada"}
    sin_compra = a.get("pct_sin_compra", 0)
    wr = a.get("win_rate") or 0
    hold = a.get("min_tenencia_mediano")
    pnl = a["pnl_neto_sol"]
    if sin_compra >= 40 or (sin_compra >= 25 and wr >= 90):
        return {"emoji": "🚨", "tipo": "INSIDER",
                "razon": f"{sin_compra:.0f}% de sus posiciones las cobró SIN haber comprado "
                         f"(tokens recibidos de un bundle) · win rate {wr:.0f}%"}
    if hold is not None and hold < 2 and a["cerradas"] >= 3:
        return {"emoji": "🤖", "tipo": "BOT/MEV",
                "razon": f"hold mediano {hold} min con {a['cerradas']} operaciones: ritmo de máquina"}
    if pnl < 0:
        return {"emoji": "📉", "tipo": "PIERDE",
                "razon": f"compró de verdad y perdió {abs(pnl):.2f} SOL (ROI {a.get('roi_total')}%)"
                         f" → no seguir"}
    if a.get("copiable", "").startswith("NO"):
        return {"emoji": "🤖", "tipo": "BOT/MEV", "razon": a["copiable"]}
    razones = [f"PnL {pnl:+.2f} SOL", f"win rate {wr:.0f}%", f"{a['cerradas']} cerradas"]
    if a.get("roi_total") is not None:
        razones.append(f"ROI {a['roi_total']}%")
    if a.get("concentracion_pnl") is not None:
        razones.append(f"{a['concentracion_pnl']}% del PnL en un solo trade")
    if a.get("t_stat") is not None:
        razones.append(f"t={a['t_stat']}")
    if a.get("fondeo_fresco"):
        razones.append("fondeo fresco (sin track record)")
    if a["cerradas"] < 30:
        razones.append("⚠️ muestra chica: no se puede certificar")
    return {"emoji": "⭐", "tipo": "CANDIDATA",
            "razon": " · ".join(razones) + " → compró con su propia plata y va positivo"}


def main():
    ap = argparse.ArgumentParser(description="Clasifica wallets y las etiqueta para el tracker")
    ap.add_argument("--tokens", type=int, default=30, help="tokens ganadores a revisar")
    ap.add_argument("--clasificar", type=int, default=12, help="cuántas wallets clasificar a fondo")
    ap.add_argument("--tx", type=int, default=80, help="transacciones por wallet")
    ap.add_argument("--origen", choices=("holders", "compradores"), default="holders",
                    help="de dónde salen las candidatas: holders (trae insiders) o compradores reales del pool")
    ap.add_argument("--cargar", action="store_true", help="las agrega al tracker etiquetadas")
    ap.add_argument("--max-cargar", type=int, default=8, help="tope de wallets a cargar al tracker")
    a = ap.parse_args()

    log("1/3 universo de tokens que funcionaron…")
    ganadores = AU.tokens_ganadores(tope=a.tokens)
    log(f"    {len(ganadores)} tokens")
    if a.origen == "compradores":
        log("2/3 candidatas por COMPRADORES REALES (leyendo las tx de cada pool)…")
        cuenta = candidatas_por_compradores(ganadores)
    else:
        log("2/3 candidatas por holders…")
        cuenta = AU.candidatas_por_holders(ganadores)
    # el camino de "compradores" no trae pct_max (solo holders lo tienen): ordenar sin romper
    orden = sorted(cuenta.items(), key=lambda kv: (-kv[1].get("veces", 0), -kv[1].get("pct_max", 0.0)))
    cand = [w for w, d in orden if d["veces"] >= 2][:a.clasificar]
    if len(cand) < a.clasificar:
        cand += [w for w, d in orden if d["veces"] == 1][:a.clasificar - len(cand)]
    log(f"    {len(cand)} candidatas a clasificar ({a.tx} tx cada una)")

    log("3/3 clasificando on-chain…")
    filas = []
    for i, w in enumerate(cand, 1):
        try:
            r = AU.auditar_wallet(w, a.tx)
        except Exception as e:
            log(f"  ! error {w[:8]}: {e}")
            r = None
        et = etiquetar(r)
        if a.origen == "compradores":
            _d = cuenta.get(w, {})
            fila_extra = {"compras_vistas": _d.get("veces", 0), "sol_puesto": round(_d.get("sol", 0), 2)}
        else:
            fila_extra = {}
        fila = {"wallet": w, **et, **fila_extra, "aparece_en": cuenta.get(w, {}).get("veces", 0),
                "tokens_vistos": cuenta.get(w, {}).get("tokens", [])[:5],
                "metricas": {k: (r or {}).get(k) for k in
                             ("cerradas", "pnl_neto_sol", "win_rate", "roi_total",
                              "pct_sin_compra", "min_tenencia_mediano", "concentracion_pnl",
                              "t_stat", "fondeo_fresco", "copiable")} if r else {}}
        filas.append(fila)
        log(f"  {i}/{len(cand)} {et['emoji']} {et['tipo']:<10} {w[:12]}… "
            f"{(r or {}).get('pnl_neto_sol','')} SOL")

    # orden: primero los insiders (avisarte cuando sueltan es lo más útil), después candidatas
    prioridad = {"INSIDER": 0, "CANDIDATA": 1, "BOT/MEV": 2, "PIERDE": 3, "PASARELA": 4, "SIN DATOS": 5}
    filas.sort(key=lambda f: (prioridad.get(f["tipo"], 9), -f["aparece_en"]))
    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tokens_revisados": [c.get("symbol") for c in ganadores], "wallets": filas},
              open(SALIDA, "w"), ensure_ascii=False, indent=2)
    log(f"guardado en {SALIDA}")

    print("\n=== WALLETS CLASIFICADAS ===")
    for f in filas:
        m = f["metricas"]
        print(f"{f['emoji']} {f['tipo']:<10} {f['wallet']}")
        print(f"     {f['razon']}")
        print(f"     aparece en {f['aparece_en']} ganadores {f['tokens_vistos']}")
    resumen = {}
    for f in filas:
        resumen[f["tipo"]] = resumen.get(f["tipo"], 0) + 1
    print(f"\nresumen: {resumen}")

    if a.cargar:
        utiles = [f for f in filas if f["tipo"] in ("INSIDER", "CANDIDATA")][:a.max_cargar]
        for f in utiles:
            etq = f"{f['emoji']}{f['tipo']}"
            if f["tipo"] == "CANDIDATA":
                m = f["metricas"]
                etq += f" pnl{m['pnl_neto_sol']:+.1f}SOL wr{m['win_rate']:.0f}%"
            os.system(f'cd {AQUI} && python3 wallet_tracker.py --agregar {f["wallet"]} "{etq}"')
            log(f"cargada al tracker: {etq} · {f['wallet']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
