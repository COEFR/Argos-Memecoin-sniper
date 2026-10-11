#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CONSUMO — cuánto cupo de Helius llevamos y cuánto queda, sin depender del panel.

Suma los contadores internos de cada sistema (cada uno anota sus créditos), los acumula por día en
`consumo.json` y avisa a Discord **una sola vez** cuando el gasto del ciclo cruza el umbral (80 %),
para no quedarnos sin calls a mitad de mes sin darnos cuenta.

Uso:
  python3 consumo.py                # muestra el estado
  python3 consumo.py --cron         # anota el día y avisa si cruzó el umbral (lo llama el cron)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

CONSUMO = os.path.join(AQUI, "consumo.json")
PLAN = 1_000_000              # créditos del ciclo (plan gratis de Helius)
INICIO_CICLO = "2026-10-07"
UMBRAL = 0.80                 # avisar al 80 %


def _leer(path, dflt):
    try:
        return json.load(open(path))
    except Exception:
        return dflt


def creditos_de_hoy():
    """Suma de los contadores internos del día (lo que sí medimos)."""
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total, detalle = 0, {}
    for archivo, nombre in (("cohortes_creditos.json", "cohortes (argos)"),
                            ("vigilante_estado.json", "vigilante en vivo"),
                            ("presupuesto_calls.json", "detector del radar")):
        d = _leer(os.path.join(AQUI, archivo), {})
        if d.get("dia") != hoy:
            continue
        n = int(d.get("creditos", 0))
        total += n
        detalle[nombre] = n
    return total, detalle


def anotar():
    """Guarda el gasto del día y devuelve el estado del ciclo."""
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_hoy, detalle = creditos_de_hoy()
    d = _leer(CONSUMO, {"por_dia": {}, "avisado": False, "inicio": INICIO_CICLO})
    d.setdefault("por_dia", {})[hoy] = total_hoy
    d["detalle_ultimo"] = detalle
    # el ciclo arrancó en INICIO_CICLO: se suman sólo los días desde ahí
    ciclo = sum(v for k, v in d["por_dia"].items() if k >= d.get("inicio", INICIO_CICLO))
    d["ciclo"] = ciclo
    tmp = CONSUMO + ".%d.tmp" % os.getpid()
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, CONSUMO)
    return d, ciclo, total_hoy


def avisar(ciclo):
    """Un solo aviso cuando se cruza el umbral."""
    d = _leer(CONSUMO, {})
    if ciclo < PLAN * UMBRAL or d.get("avisado"):
        return False
    try:
        import pump_analizador as PA
        import cohortes as CO
        msg = (f"⚠️ **CUPO DE HELIUS** — vas por **{ciclo:,}** de {PLAN:,} créditos "
               f"({ciclo / PLAN * 100:.0f} %). Con el ritmo actual queda poco: si querés seguir con la "
               f"cosecha de cohortes, es momento de pasar al plan pago (10 M) o bajar el ritmo.")
        PA.enviar_en_canal(msg, canal=CO.canal_argos())
    except Exception as e:
        print("no pude avisar:", e)
    d["avisado"] = True
    tmp = CONSUMO + ".tmp"
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, CONSUMO)
    return True


def main():
    ap = argparse.ArgumentParser(description="Consumo de créditos de Helius")
    ap.add_argument("--cron", action="store_true")
    a = ap.parse_args()
    d, ciclo, hoy = anotar()
    pct = ciclo / PLAN * 100
    dias = len([k for k in d["por_dia"] if k >= d.get("inicio", INICIO_CICLO)])
    print(f"gasto de hoy (medido): {hoy:,} · detalle: {d.get('detalle_ultimo')}")
    print(f"acumulado del ciclo: {ciclo:,} de {PLAN:,} ({pct:.1f} %) en {dias} día(s)")
    if dias:
        print(f"promedio: {ciclo / dias:,.0f} créditos/día → el ciclo alcanza para "
              f"{max(0, (PLAN - ciclo) / max(ciclo / dias, 1)):.1f} días más")
    if a.cron:
        if avisar(ciclo):
            print("aviso enviado a #argos (cruzó el 80 %)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
