#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Primeros compradores — busca wallets que COMPRAN ANTES QUE TODOS (info privilegiada).

No mide quién gana más: mide quién entra primero y si eso se repite.
  1. Toma tokens que ya corrieron (mcap alto, recientes) con su hora de creación.
  2. Lee las transacciones más viejas de la curva/pool y saca los PRIMEROS compradores,
     con los segundos que tardaron desde que se creó el token.
  3. Cuenta qué wallets aparecen como primeros compradores en VARIOS tokens.
  4. Para las que se repiten, busca de dónde salió su plata y la compara con el CREADOR
     del token: si comparten fuente de fondeo (o reciben tokens sin comprar), hay info
     privilegiada, no velocidad.

Etiquetas:
  🚨 INFO PRIVILEGIADA · entró primero en ≥2 tokens Y su fondeo viene del mismo lado que el dev
                        (o recibió los tokens en vez de comprarlos)
  ⚡ SNIPER/BOT        · entra primero en varios tokens pero comprando con su propia plata
  ❓ PRIMISO           · entró primero en 1 solo token (puede ser suerte o un bot distinto)

Uso:
  python3 primeros_compradores.py --tokens 20 --primeros 20 --min-apariciones 2
  python3 primeros_compradores.py --tokens 20 --min-apariciones 2 --cargar 5
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pump_analizador as PA          # noqa: E402
import wallet_tracker as WT           # noqa: E402
import auditor_wallets as AU          # noqa: E402

SALIDA = os.path.join(AQUI, "primeros_compradores.json")
PAUSA = 0.10


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def firmas_del_arranque(direccion, creado_ts, ventana_s=120, max_paginas=12, por_pagina=1000):
    """Firmas desde la creación hasta la ventana indicada (los que compran los primeros segundos).

    En vez de paginar hasta el arranque absoluto (imposible en un token con 40.000 tx), pagina
    hacia atrás y CORTA cuando la firma más vieja que trajo ya es de la ventana inicial. Así el
    costo queda acotado (≤12 páginas) y el resultado es exactamente lo que se busca: los primeros.
    """
    sigs = WT.rpc("getSignaturesForAddress", [direccion, {"limit": por_pagina}]) or []
    if not sigs:
        return [], False
    limite = creado_ts + ventana_s
    paginas = 0
    while (paginas < max_paginas and len(sigs) % por_pagina == 0
           and sigs[-1].get("blockTime") and sigs[-1]["blockTime"] > limite):
        mas = WT.rpc("getSignaturesForAddress",
                     [direccion, {"limit": por_pagina, "before": sigs[-1]["signature"]}]) or []
        if not mas:
            break
        sigs += mas
        paginas += 1
    alcance = bool(sigs[-1].get("blockTime")) and sigs[-1]["blockTime"] <= limite
    dentro = [s for s in sigs if s.get("blockTime") and creado_ts - 5 <= s["blockTime"] <= limite]
    return dentro or sigs[-50:], alcance


def firmas_mas_viejas(direccion, max_paginas=25, por_pagina=1000):
    """Pagina hacia atrás hasta el ARRANQUE de la cuenta.

    Clave para medir 'quién compró primero': el RPC devuelve las firmas más nuevas primero y corta
    en 1.000, así que sin paginar se termina mirando compras de horas después (medido: daba 30.067 s
    en vez de los primeros segundos). Se corta cuando una página vuelve incompleta (ahí está el inicio).
    """
    sigs = WT.rpc("getSignaturesForAddress", [direccion, {"limit": por_pagina}]) or []
    if not sigs:
        return [], False
    paginas = 0
    while len(sigs) % por_pagina == 0 and len(sigs) < por_pagina * max_paginas:
        mas = WT.rpc("getSignaturesForAddress",
                     [direccion, {"limit": por_pagina, "before": sigs[-1]["signature"]}]) or []
        if not mas:
            break
        sigs += mas
        paginas += 1
    completo = len(sigs) < por_pagina * (paginas + 1)
    return sigs, completo


def primeros_de_un_token(c, cuantos=20, max_paginas=40):
    """Los primeros compradores del token, con segundos desde la creación y su slot."""
    # OJO: el ancla correcta son las cuentas ASOCIADAS de la curva, donde caen las compras.
    # `bonding_curve` (estado) tiene 2-4 firmas; `pool_address` (PumpSwap) se crea al graduar y
    # puede tener 40.000+ tx (medido: nunca se llegaba al arranque). `associated_bonding_curve`
    # llega al segundo 0 en 0-2 páginas.
    anclas = [c.get("associated_bonding_curve"), c.get("pool_address"), c.get("bonding_curve")]
    pool = next((x for x in anclas if x), None)
    if not pool:
        return []
    creado = (c.get("created_timestamp") or 0) / 1000
    sigs, alcance = firmas_del_arranque(pool, creado, max_paginas=max_paginas)
    if not sigs:
        return []
    completas = alcance
    # las firmas vienen de la más nueva a la más vieja: las primeras compras están al final
    candidatas = list(reversed(sigs))[:max(cuantos * 3, 30)]
    salida = []
    for s in candidatas:
        if s.get("err"):
            continue
        tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                      "encoding": "jsonParsed"}])
        time.sleep(PAUSA)
        if not tx or not tx.get("meta"):
            continue
        for pub in WT.cuentas_tx(tx):
            if pub in AU.AU_IGNORAR:
                continue
            ev = WT.parsear_tx(tx, pub)
            if ev and ev["accion"] == "compra" and ev["mint"] == c["mint"] and abs(ev["sol"]) >= 0.03:
                seg = (s.get("blockTime") or 0) - creado if s.get("blockTime") else None
                if seg is None or seg < -5 or seg > 180:
                    continue          # fuera de la ventana inicial: no es "primero" en nada
                salida.append({"wallet": pub, "seg": seg, "slot": tx.get("slot"),
                               "sol": round(abs(ev["sol"]), 4), "comprado": True})
        if len(salida) >= cuantos:
            break
    # ordenar por tiempo real (la más vieja primero)
    salida.sort(key=lambda e: (e["seg"] if e["seg"] is not None else 9e9, e["slot"] or 0))
    return salida[:cuantos], completas


def de_donde_viene(wallet, max_tx=25):
    """El fondeo principal de una wallet (para comparar con el dev)."""
    try:
        _, recibido, _ = __import__("wallet_forense").flujos_sol(wallet, max_tx)
    except Exception:
        return []
    return sorted(((w, round(d["sol"], 2)) for w, d in recibido.items()),
                  key=lambda kv: -kv[1])[:3]


def main():
    ap = argparse.ArgumentParser(description="Busca wallets que compran antes que todos")
    ap.add_argument("--tokens", type=int, default=20, help="tokens ganadores a revisar")
    ap.add_argument("--primeros", type=int, default=20, help="cuántos primeros compradores por token")
    ap.add_argument("--max-edad-horas", type=float, default=48,
                    help="solo tokens creados en las últimas N horas (los viejos tienen miles de tx "
                         "y no se llega al arranque; además su info ya no es accionable)")
    ap.add_argument("--max-paginas", type=int, default=40, help="páginas de 1.000 firmas a paginar")
    ap.add_argument("--min-apariciones", type=int, default=2,
                    help="en cuántos tokens debe haber entrado primero para ser candidata")
    ap.add_argument("--tx-fondeo", type=int, default=25, help="tx para buscar el fondeo de cada candidata")
    ap.add_argument("--cargar", type=int, default=0, metavar="N", help="carga las N mejores al tracker")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    log("1/3 tokens que ya corrieron…")
    ganadores = AU.tokens_ganadores(max_edad_dias=a.max_edad_horas / 24, mc_min=100_000, tope=a.tokens)
    log(f"    {len(ganadores)} tokens · buscando los primeros {a.primeros} compradores de cada uno")

    log("2/3 leyendo TODAS las transacciones de cada curva/pool desde el arranque…")
    apariciones = {}
    creadores = {}
    for i, c in enumerate(ganadores, 1):
        creadores[c["mint"]] = c.get("creator")
        res = primeros_de_un_token(c, a.primeros, a.max_paginas)
        if not res:
            continue
        primeros, completas = res
        if not primeros:
            log(f"  ⚠️ {c.get('symbol','?')}: no pude alcanzar los primeros segundos, lo salteo")
            continue
        for pos, e in enumerate(primeros, 1):
            d = apariciones.setdefault(e["wallet"], {"veces": 0, "seg": [], "pos": [], "sol": 0.0,
                                                    "tokens": [], "creadores": set()})
            d["veces"] += 1
            if e["seg"] is not None:
                d["seg"].append(e["seg"])
            d["pos"].append(pos)
            d["sol"] += e["sol"]
            d["tokens"].append(c.get("symbol") or "?")
            if c.get("creator"):
                d["creadores"].add(c["creator"])
        log(f"  {i}/{len(ganadores)} {c.get('symbol','?'):<12} primeros: {len(primeros)} "
            f"{'(lista completa)' if not completas else '(⚠️ puede faltar el arranque)'}")

    orden = [(v, k) for k, v in apariciones.items() if v["veces"] >= a.min_apariciones]
    orden.sort(key=lambda kv: (-kv[0]["veces"], statistics.median(kv[0]["seg"]) if kv[0]["seg"] else 9e9))
    log(f"3/3 {len(orden)} wallets entran primero en ≥{a.min_apariciones} tokens · buscando su fondeo")

    filas = []
    for v, w in orden[:25]:
        fondeo = de_donde_viene(w, a.tx_fondeo)
        wallets_fondeo = {x[0] for x in fondeo}
        # ¿el que la fondeó es el creador de alguno de esos tokens?
        coincide = wallets_fondeo & v["creadores"]
        audit = None
        try:
            audit = AU.auditar_wallet(w, 40)
        except Exception:
            pass
        sin_compra = (audit or {}).get("pct_sin_compra")
        if coincide:
            etiqueta, emoji, razon = "INFO PRIVILEGIADA", "🚨", (
                f"entró primero en {v['veces']} tokens y **la fondeó el mismo creador del token** "
                f"({', '.join(list(coincide))[:44]}…) → no es velocidad, es acceso")
        elif sin_compra is not None and sin_compra >= 40:
            etiqueta, emoji, razon = "INFO PRIVILEGIADA", "🚨", (
                f"entró primero en {v['veces']} tokens y el {sin_compra:.0f}% de sus posiciones "
                f"las cobró sin comprar (tokens regalados = acceso al supply)")
        elif v["veces"] >= 2:
            etiqueta, emoji, razon = "SNIPER/BOT", "⚡", (
                f"entró primero en {v['veces']} tokens comprando con su propia plata "
                f"(mediana {statistics.median(v['seg']) if v['seg'] else '?'}s desde la creación)")
        else:
            etiqueta, emoji, razon = "PRIMISO", "❓", "entró primero en un solo token: puede ser suerte"
        filas.append({"wallet": w, "emoji": emoji, "tipo": etiqueta, "razon": razon,
                      "veces": v["veces"], "tokens": v["tokens"][:6],
                      "seg_mediano": round(statistics.median(v["seg"]), 1) if v["seg"] else None,
                      "pos_mediana": round(statistics.median(v["pos"]), 1),
                      "sol_total": round(v["sol"], 2), "fondeo": fondeo,
                      "sin_compra": sin_compra,
                      "pnl": (audit or {}).get("pnl_neto_sol")})
    prio = {"INFO PRIVILEGIADA": 0, "SNIPER/BOT": 1, "PRIMISO": 2}
    filas.sort(key=lambda f: (prio.get(f["tipo"], 9), -f["veces"]))
    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tokens": [c.get("symbol") for c in ganadores], "wallets": filas},
              open(SALIDA, "w"), ensure_ascii=False, indent=2)
    log(f"guardado en {SALIDA}")

    print(f"\n=== WALLETS QUE ENTRARON PRIMERO (en ≥{a.min_apariciones} tokens) ===")
    for f in filas:
        print(f"{f['emoji']} {f['tipo']:<18} {f['wallet']}")
        print(f"     entró primero en {f['veces']} tokens {f['tokens']} · "
              f"mediana {f['seg_mediano']}s después de la creación · posición mediana {f['pos_mediana']} · "
              f"puesto {f['sol_total']} SOL")
        print(f"     {f['razon']}")
        if f["fondeo"]:
            print(f"     lo fondeó: {f['fondeo']}")
        if f["pnl"] is not None:
            print(f"     PnL en la ventana: {f['pnl']:+.3f} SOL")
    resumen = {}
    for f in filas:
        resumen[f["tipo"]] = resumen.get(f["tipo"], 0) + 1
    print(f"\nresumen: {resumen}")

    if a.cargar:
        for f in [x for x in filas if x["tipo"] in ("INFO PRIVILEGIADA", "SNIPER/BOT")][:a.cargar]:
            etq = f"{f['emoji']}{f['tipo']} {f['veces']}x primero"
            os.system(f'cd {AQUI} && python3 wallet_tracker.py --agregar {f["wallet"]} "{etq}"')
            log(f"cargada al tracker: {etq} · {f['wallet']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
