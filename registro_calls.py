#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REGISTRO Y EVALUACIÓN DE LAS CALLS — para recalibrar el puntaje con datos reales.

Qué hace:
  1. `registrar(raf, ev)`  — cada vez que se avisa una call, guarda la foto completa: puntaje de
     fuerza, motivos, wallets, SOL, slot, mcap y liquidez del momento, calidad del token.
  2. `--evaluar`           — para cada call, mide el resultado **sin gastar RPC** (sólo DexScreener)
     a la hora, a las 6 h y a las 24 h: guarda el mcap y el múltiplo respecto al momento del aviso.
  3. `--informe`           — qué predijo de verdad: resultado promedio por tramo de puntaje y por
     cada factor (wallets, SOL, mcap, slot), + sugerencia de cuánto subir/bajar cada peso.

Uso:
  python3 registro_calls.py --evaluar     # lo corre el cron cada 30 min
  python3 registro_calls.py --informe     # informe con los datos acumulados
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pump_analizador as PA          # noqa: E402

HIST = os.path.join(AQUI, "calls_historial.json")
CHECKPOINTS = (1, 6, 24)              # horas a las que se mide el resultado


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def _leer():
    try:
        d = json.load(open(HIST))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _guardar(d):
    """Escribe el historial de calls (atómico y sin colisiones: ver jsonio).

    El respaldo directo existe porque perder el historial es peor que un archivo a medio escribir:
    si jsonio no puede (p.ej. un dato no serializable), se escribe igual con default=str.
    """
    import jsonio
    if not jsonio.guardar(HIST, d):
        with open(HIST, "w") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1, default=str)


def registrar(raf, ev):
    """Guarda la call recién avisada (la llama el alertador).

    Todo el ciclo (leer → agregar → escribir) va BAJO BLOQUEO: si no, dos procesos del cron registran
    a la vez y uno pisa al otro (se perdían calls).
    """
    try:
        import jsonio
        ctx = ev.get("contexto") or {}
        with jsonio.bloqueo(HIST):
            d = _leer()
            return _registrar_dentro(raf, ev, d, ctx)
    except Exception as e:
        log(f"  ! no pude registrar la call: {e}")


def _registrar_dentro(raf, ev, d, ctx):
    # no repetir el mismo token: ya sea que se avisó hace minutos u horas (antes se duplicaba
    # cada vez que el mismo token volvía a mostrar ráfaga)
    if any(x.get("mint") == ev.get("mint") and (time.time() - (x.get("ts_epoch") or 0)) < 6 * 3600
           for x in d):
        return
    d.append({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ts_epoch": time.time(),
        "mint": ev.get("mint"), "simbolo": ev.get("simbolo"), "nombre": ev.get("nombre"),
        "nota": raf.get("nota"), "etq": raf.get("etq"),
        "n_wallets": raf.get("n_wallets"), "sol": raf.get("sol"),
        "segundos": raf.get("segundos"), "slots_iguales": raf.get("slots_iguales"),
        "frescas": raf.get("frescas"), "conocidas": raf.get("conocidas"),
        "compras_en_ventana": raf.get("compras_en_ventana"),
        "wallets": raf.get("wallets") or [],          # para detectar miembros que repiten
        "canal": raf.get("canal"),                    # de dónde salió la call (ráfaga o canal social)
        "mcap_call": ctx.get("mcap_usd"), "liq_call": ctx.get("liquidez_usd"),
        "curva_call": ctx.get("curva_pct"), "edad_call": ctx.get("edad_min"),
        "calidad_token": ev.get("puntaje"), "nota_calidad": raf.get("nota_calidad"),
        "motivos": raf.get("motivos") or [],
        "silenciosa": bool(raf.get("silenciosa")),      # True = no se avisó (filtro de calidad)
        "resultados": {},
        # seguimiento del precio DESPUÉS del aviso: con esto se mide el PICO (la mejor salida
        # posible) y el tiempo hasta ese pico, no sólo dónde quedó al final.
        "seguimiento": [{"t": time.time(), "mcap": ctx.get("mcap_usd") or 0}],
        "ath_post": {},
    })
    _guardar(d)
    log(f"  registrada la call de {ev.get('simbolo')} ({raf.get('nota')}/10)")


def muestrear():
    """Toma una muestra del mcap de cada call de las últimas 6 h (cron cada 5 min).

    Es lo que permite responder "¿después del aviso subió?": guarda la serie de precios y de ahí
    sale el PICO posterior, cuánto duró la ventana y cuánto se ganaba vendiendo ahí.
    Sólo usa DexScreener (cero créditos de RPC).
    """
    d = _leer()
    ahora = time.time()
    nuevas = 0
    for x in d:
        if not x.get("mint") or (ahora - (x.get("ts_epoch") or 0)) > 6 * 3600:
            continue
        seg = x.setdefault("seguimiento", [])
        if seg and (ahora - seg[-1]["t"]) < 240:          # una muestra cada ~4 min
            continue
        mc = None
        try:
            p = PA.dexscreener(x["mint"]) or {}
            mc = p.get("marketCap") or p.get("fdv")
        except Exception:
            pass
        time.sleep(0.25)
        if mc:
            # Si la serie arranca mucho después del aviso, el PICO no es reconstruible hacia atrás:
            # se marca para no contarla como "no hubo ventana de ganancia" (sería mentira).
            if len(seg) <= 1 and (ahora - (x.get("ts_epoch") or ahora)) > 30 * 60:
                x["serie_tardia"] = True
            seg.append({"t": ahora, "mcap": mc})
            nuevas += 1
        # el ATH del token: si ocurrió DESPUÉS del aviso, es (parte del) pico posterior
        if not (x.get("ath_post") or {}).get("mcap"):
            try:
                coin = PA.buscar_coin(x["mint"]) or {}
                ath, ath_ts = coin.get("ath_market_cap"), coin.get("ath_market_cap_timestamp")
                if ath and ath_ts and (ath_ts / 1000) > (x.get("ts_epoch") or 0):
                    x["ath_post"] = {"mcap": ath, "t": ath_ts / 1000}
            except Exception:
                pass
    if nuevas:
        _guardar(d)
    log(f"muestras nuevas: {nuevas} · calls seguidas: {len([x for x in d if x.get('seguimiento')])}")
    return d


def _pico(x):
    """Pico alcanzado DESPUÉS del aviso: máximo entre las muestras y el ATH posterior.

    Devuelve (múltiplo respecto al mcap del aviso, minutos desde el aviso hasta ese pico) o (None, None).
    """
    base = x.get("mcap_call")
    if not base:
        return None, None
    t0 = x.get("ts_epoch") or 0
    cands = [(s["mcap"] / base, (s["t"] - t0) / 60.0)
             for s in (x.get("seguimiento") or []) if s.get("mcap") and s.get("t")]
    ath = x.get("ath_post") or {}
    if ath.get("mcap") and ath.get("t"):
        cands.append((ath["mcap"] / base, (ath["t"] - t0) / 60.0))
    if not cands:
        return None, None
    mult, mins = max(cands, key=lambda kv: kv[0])
    return round(mult, 3), round(mins, 1)


def evaluar():
    """Mide el resultado de cada call en los puntos de control (1, 6, 24 h). Sólo DexScreener."""
    d = _leer()
    ahora = time.time()
    pendientes = 0
    for x in d:
        base = x.get("mcap_call")
        if not base:
            continue
        horas = (ahora - (x.get("ts_epoch") or ahora)) / 3600
        for h in CHECKPOINTS:
            if horas < h or str(h) in (x.get("resultados") or {}):
                continue
            mc = None
            try:
                p = PA.dexscreener(x["mint"]) or {}
                mc = p.get("marketCap") or p.get("fdv")
            except Exception:
                pass
            time.sleep(0.3)
            if mc:
                x.setdefault("resultados", {})[str(h)] = {
                    "mcap": round(mc, 2), "multiplo": round(mc / base, 3),
                    "medido": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                pendientes += 1
    if pendientes:
        _guardar(d)
    log(f"mediciones nuevas: {pendientes} · calls en el registro: {len(d)}")
    return d


def _mult(x):
    """Mejor dato disponible: 24 h si está, si no 6 h, si no 1 h."""
    r = x.get("resultados") or {}
    for h in ("24", "6", "1"):
        if h in r:
            return r[h]["multiplo"], h + "h"
    return None, None


def _prom(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def informe():
    """Qué puntaje predijo de verdad + sugerencia de pesos."""
    d = [x for x in _leer() if _mult(x)[0] is not None]
    if not d:
        print("Sin mediciones todavía: corré `--evaluar` cuando haya pasado 1 h desde la primera call.")
        return
    print(f"=== INFORME DE CALLS ({len(d)} con resultado medido) ===\n")

    print("RESULTADO POR TRAMO DE PUNTAJE")
    for nombre, lo, hi in (("🔥 ALTA (≥8)", 8, 99), ("✅ MEDIA-ALTA (6-8)", 6, 8),
                           ("⚠️ MEDIA (4-6)", 4, 6), ("🚫 BAJA (<4)", 0, 4)):
        g = [x for x in d if lo <= (x.get("nota") or 0) < hi]
        if not g:
            continue
        mult = [(_mult(x)[0]) for x in g]
        gana = len([m for m in mult if m > 1.05])
        print(f"  {nombre:<20} {len(g):>3} calls · múltiplo promedio {_prom(mult):<6} · "
              f"ganaron {gana}/{len(g)} · mejor {max(mult):.2f}x · peor {min(mult):.2f}x")

    print("\nCADA FACTOR: ¿el peso que le puse sirve?")
    def bloque(titulo, extractor, tramos):
        print(f"  {titulo}")
        for etiqueta, pred in tramos:
            g = [x for x in d if pred(x)]
            if not g:
                continue
            mult = [_mult(x)[0] for x in g]
            print(f"     {etiqueta:<22} {len(g):>3} calls · múltiplo promedio {_prom(mult)}")
    bloque("· WALLETS en la ráfaga:", lambda x: x.get("n_wallets"),
           [("≥15 (doy +4)", lambda x: (x.get("n_wallets") or 0) >= 15),
            ("10-14 (doy +3,5)", lambda x: 10 <= (x.get("n_wallets") or 0) < 15),
            ("8-9 (doy +3)", lambda x: 8 <= (x.get("n_wallets") or 0) < 10),
            ("5-7 (doy +1,5)", lambda x: 5 <= (x.get("n_wallets") or 0) < 8),
            ("<5 (doy +0,5)", lambda x: (x.get("n_wallets") or 0) < 5)])
    bloque("· SOL en la ráfaga:", lambda x: x.get("sol"),
           [("≥50 (doy +3)", lambda x: (x.get("sol") or 0) >= 50),
            ("25-50 (doy +2)", lambda x: 25 <= (x.get("sol") or 0) < 50),
            ("10-25 (doy +1)", lambda x: 10 <= (x.get("sol") or 0) < 25),
            ("<10 (doy 0)", lambda x: (x.get("sol") or 0) < 10)])
    bloque("· mcap del token:", lambda x: x.get("mcap_call"),
           [("≥150k (doy +2)", lambda x: (x.get("mcap_call") or 0) >= 150_000),
            ("100-150k (+1,5)", lambda x: 100_000 <= (x.get("mcap_call") or 0) < 150_000),
            ("50-100k (+0,5)", lambda x: 50_000 <= (x.get("mcap_call") or 0) < 100_000),
            ("<50k (−1,5)", lambda x: (x.get("mcap_call") or 0) < 50_000)])
    bloque("· slots iguales:", lambda x: x.get("slots_iguales"),
           [("≥3 (resto −2)", lambda x: (x.get("slots_iguales") or 0) >= 3),
            ("<3 (sin resta)", lambda x: (x.get("slots_iguales") or 0) < 3)])

    print("\n¿HUBO VENTANA DE GANANCIA DESPUÉS DEL AVISO? (pico alcanzado desde la call)")
    filas = []
    for x in d:
        pico, mins = _pico(x)
        fin, _ = _mult(x)
        filas.append((x, pico, mins, fin))
    con_serie = [f for f in filas if not f[0].get("serie_tardia") and len(f[0].get("seguimiento") or []) >= 2]
    tardias = [f for f in filas if f[0].get("serie_tardia")]
    conp = [f for f in con_serie if (f[1] or 0) > 1.05]
    print(f"  calls con serie de precios DESDE el aviso: {len(con_serie)} "
          f"({len(tardias)} quedaron fuera: su serie arrancó tarde y el pico no se puede reconstruir)")
    print(f"  con ventana de ganancia (>1,05x después del aviso): {len(conp)}/{len(con_serie)}")
    mejores = [(x["simbolo"], p) for x, p, _m, _f in filas if (p or 0) > 1.2][:6]
    print(f"  pico promedio: {_prom([f[1] for f in filas])} · los que más subieron: {mejores}")
    mts = [f[2] for f in filas if f[2] is not None]
    if mts:
        print(f"  tiempo hasta el pico (min): mediana {sorted(mts)[len(mts)//2]:.0f} · "
              f"el más rápido {min(mts):.0f} · el más lento {max(mts):.0f}")
    print(f"  si vendías en el pico: promedio {_prom([f[1] for f in filas])} · "
          f"si aguantabas hasta hoy: {_prom([f[3] for f in filas])}")
    print("  detalle:")
    for x, pico, mins, fin in sorted(filas, key=lambda f: -(f[1] or 0))[:12]:
        print(f"     {x['simbolo'][:11]:<11} nota {str(x.get('nota')):<5} · pico {('%.2fx' % pico) if pico else '?':>7} "
              f"a los {str(mins):>6} min · ahora {('%.2fx' % fin) if fin else '?':>7}")

    print("\nSUGERENCIA (comparando cada tramo contra el promedio general)")
    gen = _prom([_mult(x)[0] for x in d])
    print(f"  promedio general: {gen}")
    for etiqueta, pred in [("≥15 wallets", lambda x: (x.get("n_wallets") or 0) >= 15),
                           ("≥50 SOL", lambda x: (x.get("sol") or 0) >= 50),
                           ("mcap ≥150k", lambda x: (x.get("mcap_call") or 0) >= 150_000),
                           ("mcap <50k", lambda x: (x.get("mcap_call") or 0) < 50_000),
                           ("slots iguales ≥3", lambda x: (x.get("slots_iguales") or 0) >= 3)]:
        g = [_mult(x)[0] for x in d if pred(x)]
        if len(g) < 3:
            print(f"  · {etiqueta:<18} sin muestra suficiente ({len(g)})")
            continue
        pr = _prom(g)
        d_if = (pr or 0) - (gen or 0)
        accion = ("subir el peso" if d_if > 0.15 else
                  ("bajar el peso" if d_if < -0.15 else "dejarlo igual"))
        print(f"  · {etiqueta:<18} n={len(g):<3} promedio {pr:<6} vs general {gen} "
              f"({d_if:+.2f}) → {accion}")
    print("\n(Con menos de ~30 calls esto es orientativo: la muestra todavía es chica.)")


def main():
    ap = argparse.ArgumentParser(description="Registro y evaluación de las calls")
    ap.add_argument("--evaluar", action="store_true")
    ap.add_argument("--muestrear", action="store_true")
    ap.add_argument("--informe", action="store_true")
    a = ap.parse_args()
    if a.muestrear:
        muestrear()
        return 0
    if a.evaluar:
        evaluar()
        return 0
    if a.informe:
        informe()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
