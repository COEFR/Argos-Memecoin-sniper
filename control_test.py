#!/usr/bin/env python3
"""Test de control — ¿los avisos de Argos le ganan a tokens frescos al azar?

Por qué existe: el paper de cohortes (arxiv 2607.02795) demuestra que los números crudos engañan — la
mitad del "efecto" de una cohorte es contaminación aritmética (sus propias compras contadas en el
resultado) y, con matching por calidad del lanzamiento, el efecto real es +16% de compradores y el SOL
indistinguible de cero. La única forma de saber si Argos aporta de verdad es compararlo contra un
CONTROL medido IGUAL: tokens frescos de la misma hora y del mismo tamaño que NO avisamos.

Cómo se hace comparable: a los dos grupos se los mide con el mismo reloj y la misma regla — FDV en el
momento de detectarlo vs el pico posterior, en muestras cada ~5 min (sólo DexScreener, cero créditos).

Uso:
  python3 control_test.py --sembrar 10     # pone 10 tokens no avisados en seguimiento
  python3 control_test.py --muestrear      # toma una muestra de FDV de los dos grupos
  python3 control_test.py --informe        # compara avisos vs control (con prueba de permutación)
"""
import argparse
import json
import os
import random
import statistics
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pump_analizador as PA      # noqa: E402

CONTROL = os.path.join(AQUI, "control_tokens.json")
HIST = os.path.join(AQUI, "calls_historial.json")
LANZ = os.path.join(AQUI, "lanzamientos.json")
VENTANA_H = 6          # se sigue cada token 6 h, igual que el registro de calls
CADA_S = 240           # una muestra cada ~4 min


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {m}", flush=True)


def _leer(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def _guardar(p, x):
    tmp = f"{p}.{os.getpid()}.tmp"
    json.dump(x, open(tmp, "w"))
    os.replace(tmp, p)


def sembrar(n=10):
    """Toma n lanzamientos que NO generaron aviso y los pone en seguimiento (el grupo de control)."""
    control = _leer(CONTROL, {})
    avisados = {x.get("mint") for x in (_leer(HIST, []) or [])}
    lanz = (_leer(LANZ, {}) or {}).get("tokens", {})
    ahora = time.time()
    cands = []
    for m, d in lanz.items():
        if m in avisados or m in control:
            continue
        mc = d.get("mcap_cosecha") or 0
        f = d.get("fecha")
        if not mc or not f:
            continue
        try:
            t0 = datetime.fromisoformat(f).timestamp()
        except Exception:
            continue
        if (ahora - t0) > VENTANA_H * 3600 or (ahora - t0) < 60:
            continue                                  # ni viejos ni recién nacidos
        cands.append({"mint": m, "simbolo": d.get("simbolo"), "mcap_inicial": mc, "t": t0})
    if not cands:
        log("  (no hay candidatos nuevos para el control)")
        return 0
    random.shuffle(cands)                             # al azar, no los "buenos"
    # El FDV de arranque sale de DexScreener (la MISMA fuente que las muestras): si el token todavía no
    # tiene par ahí (sigue en la curva), no se puede medir y se descarta — así el control es comparable
    # con el tratamiento, que también se mide por DexScreener.
    nuevos = []
    for c in cands:
        if len(nuevos) >= n:
            break
        mc = None
        try:
            p = PA.dexscreener(c["mint"])
            mc = (p.get("marketCap") or p.get("fdv")) if p else None
        except Exception:
            mc = None
        time.sleep(0.12)
        if not mc:
            continue
        c["mcap_inicial"] = mc
        c["serie"] = [{"t": ahora, "mcap": mc}]
        nuevos.append(c)
    for c in nuevos:
        control[c["mint"]] = c
    _guardar(CONTROL, control)
    log(f"  control: +{len(nuevos)} tokens en seguimiento (total {len(control)})")
    return len(nuevos)


def muestrear():
    """Muestra el FDV de los tokens del control (misma cadencia que el registro de calls)."""
    control = _leer(CONTROL, {})
    ahora = time.time()
    n = 0
    for m, c in list(control.items()):
        if (ahora - c["t"]) > VENTANA_H * 3600:
            continue
        serie = c.setdefault("serie", [])
        if serie and (ahora - serie[-1]["t"]) < CADA_S:
            continue
        mc = None
        try:
            p = PA.dexscreener(m)
            mc = (p.get("marketCap") or p.get("fdv")) if p else None
        except Exception:
            mc = None
        time.sleep(0.12)
        if mc:
            serie.append({"t": ahora, "mcap": mc})
            n += 1
    _guardar(CONTROL, control)
    log(f"  control: {n} muestras nuevas")
    return n


def _pico(c):
    serie = [s for s in (c.get("serie") or []) if s.get("mcap")]
    m0 = c.get("mcap_inicial") or 0
    if len(serie) < 2 or not m0:
        return None
    return max(s["mcap"] for s in serie) / m0


def _permutacion(a, b, vueltas=5000):
    """p-valor por permutación sobre la diferencia de medianas (sin librerías)."""
    if not a or not b:
        return None
    obs = statistics.median(a) - statistics.median(b)
    juntos = list(a) + list(b)
    na = len(a)
    rng = random.Random(7)
    peor = 0
    for _ in range(vueltas):
        rng.shuffle(juntos)
        d = statistics.median(juntos[:na]) - statistics.median(juntos[na:])
        if abs(d) >= abs(obs):
            peor += 1
    return peor / vueltas


def informe():
    """Compara los avisos (tratamiento) contra el control, en la MISMA métrica."""
    control = _leer(CONTROL, {})
    trat, ctrl = [], []
    for x in (_leer(HIST, []) or []):
        seg = [s for s in (x.get("seguimiento") or []) if s.get("mcap")]
        if len(seg) >= 2 and x.get("mcap_call"):
            trat.append(max(s["mcap"] for s in seg) / x["mcap_call"])
    for c in control.values():
        v = _pico(c)
        if v:
            ctrl.append(v)
    print("=== TEST DE CONTROL — ¿los avisos le ganan a tokens al azar? ===")
    if not ctrl:
        print("  sin datos de control todavía: se está llenando (cada ~5 min). Volvé en unas horas.")
        return
    def res(nom, xs):
        xs = sorted(xs)
        print("  %-22s n=%-4d mediana %5.2fx · ≥1,5x %3.0f%% · ≥2x %3.0f%% · ≥3x %3.0f%%"
              % (nom, len(xs), statistics.median(xs),
                 100 * sum(1 for v in xs if v >= 1.5) / len(xs),
                 100 * sum(1 for v in xs if v >= 2) / len(xs),
                 100 * sum(1 for v in xs if v >= 3) / len(xs)))
    res("📣 AVISOS (Argos)", trat)
    res("🎲 CONTROL (al azar)", ctrl)
    p = _permutacion(trat, ctrl)
    dif = statistics.median(trat) - statistics.median(ctrl)
    print(f"\n  diferencia de medianas: {dif:+.2f}x · p-valor (permutación): {p:.3f}"
          if p is not None else "\n  (no alcanza para comparar)")
    if p is not None:
        if p < 0.05 and dif > 0:
            print("  ✅ los avisos le GANAN al azar de forma significativa (p<0,05)")
        elif p < 0.05:
            print("  ❌ los avisos rinden PEOR que el azar (p<0,05): hay que revisar la selección")
        else:
            print("  ⚠️ sin diferencia significativa todavía: con más muestras se sabrá")


def informe_texto():
    """El informe como texto (para mandarlo al canal)."""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        informe()
    return buf.getvalue().strip()


def main():
    ap = argparse.ArgumentParser(description="Test de control de los avisos de Argos")
    ap.add_argument("--sembrar", type=int, default=0, metavar="N")
    ap.add_argument("--muestrear", action="store_true")
    ap.add_argument("--informe", action="store_true")
    ap.add_argument("--discord", action="store_true", help="manda el informe al canal")
    a = ap.parse_args()
    if a.sembrar:
        sembrar(a.sembrar)
    if a.muestrear:
        muestrear()
    if a.informe:
        informe()
        if a.discord:
            import cohortes as CO
            try:
                PA.enviar_en_canal(informe_texto(), canal=CO.canal_argos())
            except Exception as e:
                log(f"  ! no pude mandar el informe: {e}")
    if not any([a.sembrar, a.muestrear, a.informe]):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
