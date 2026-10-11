#!/usr/bin/env python3
"""Tabla de resultados de las calls: la misma cuenta que hace Rick con /ga.

Mide del FDV en el momento del aviso al pico que alcanzó después (no al precio de ahora, que castiga
al que ya se fue). Rick no puede hacer esta comparación con nosotros: ignora los mensajes de otros bots
y Discord no deja que un bot use los comandos de otra aplicación.

Lo que agrega sobre Rick: separa las calls que SALIERON al canal de las que frenó el filtro de calidad
(marca `silenciosa`), así se ve con datos si el filtro sirve o si nos está tapando ganadores.

Uso:
  python3 tabla_calls.py                 # la tabla del día, por pantalla
  python3 tabla_calls.py --todo          # histórico completo
  python3 tabla_calls.py --discord       # y la manda al canal
  python3 tabla_calls.py --pegar         # sólo el bloque de contratos listos para pegar en Rick
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


def _calls():
    try:
        return json.load(open(os.path.join(AQUI, "calls_historial.json")))
    except Exception:
        return []


def _es_argos(x):
    """¿El registro salió del detector de grupos (Argos) y no del viejo detector de ráfagas?"""
    if x.get("es_cohorte"):
        return True
    return any(("grupo" in m.lower() or "cohorte" in m.lower()) for m in (x.get("motivos") or []))


def _pico(x):
    """(mcap del aviso, pico posterior, múltiplo del pico)."""
    m0 = x.get("mcap_call") or 0
    seg = [s for s in (x.get("seguimiento") or []) if s.get("mcap")]
    if not m0 or not seg:
        return None, None, None
    top = max(seg, key=lambda s: s["mcap"])
    return m0, top["mcap"], top["mcap"] / m0


def _horas_al_pico(x):
    seg = [s for s in (x.get("seguimiento") or []) if s.get("mcap")]
    if not seg:
        return None
    top = max(seg, key=lambda s: s["mcap"])
    return (top["t"] - (x.get("ts_epoch") or top["t"])) / 3600


def _usd(v):
    if not v:
        return "$0"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1000:.0f}k"
    return f"${v:.0f}"


def fila(x, i, con_actual=False):
    m0, mk, mm = _pico(x)
    if mk is None:
        return f"  {i:>2}. {str(x.get('simbolo'))[:13]:<13} sin datos de precio"
    h = _horas_al_pico(x)
    etq = "🚫 frenada" if x.get("silenciosa") else "📣 avisada"
    t = f"{h*60:.0f} min" if h is not None and h < 1 else (f"{h:.1f} h" if h is not None else "?")
    est = "🚀" if mm >= 2 else ("✅" if mm >= 1.3 else ("➖" if mm >= 0.8 else "📉"))
    linea = (f"  {i:>2}. {est} {str(x.get('simbolo'))[:13]:<13} {_usd(m0):>7} → {_usd(mk):>8} = "
             f"{mm:>5.2f}x  (pico a {t}, {etq})")
    return linea


def ranking(xs, titulo, n=10):
    xs = [x for x in xs if _pico(x)[2] is not None]
    if not xs:
        return [f"{titulo}: sin datos todavía"]
    xs.sort(key=lambda x: -_pico(x)[2])
    out = [titulo]
    for i, x in enumerate(xs[:n], 1):
        out.append(fila(x, i))
    return out


def resumen(xs, titulo):
    """Cuántas llegaron a 1,5x / 2x / 3x, y la comparación avisadas vs frenadas por el filtro."""
    out = [titulo]
    for nombre, grupo in (("todas", xs),
                          ("📣 avisadas al canal", [x for x in xs if not x.get("silenciosa")]),
                          ("🚫 frenadas por el filtro", [x for x in xs if x.get("silenciosa")])):
        mm = [_pico(x)[2] for x in grupo if _pico(x)[2] is not None]
        if not mm:
            out.append(f"   {nombre:<24} sin mediciones todavía")
            continue
        mm.sort()
        med = mm[len(mm)//2]
        out.append(f"   {nombre:<24} {len(mm):>3} calls · mediana {med:>5.2f}x · "
                   f"≥1,5x {100*sum(1 for v in mm if v>=1.5)/len(mm):>3.0f}% · "
                   f"≥2x {100*sum(1 for v in mm if v>=2)/len(mm):>3.0f}% · "
                   f"≥3x {100*sum(1 for v in mm if v>=3)/len(mm):>3.0f}%")
    return out


def bloque_pegar(xs, n=5):
    """Contratos del día listos para pegar a mano en el chat: así Rick los escanea y su board se llena."""
    xs = [x for x in xs if _pico(x)[2] is not None]
    xs.sort(key=lambda x: -_pico(x)[2])
    lineas = [x.get("mint") for x in xs[:n] if x.get("mint")]
    return lineas


def contratos(n=8, todo=False, minutos=None):
    """Los N contratos con mejor pico, listos para pegar en el chat (uno por línea).

    `minutos`: sólo calls de los últimos N minutos. Es lo importante para Rick: él toma como línea
    base el FDV del PRIMER SCAN, así que si el contrato se pega horas después, su "ganancia" arranca
    desde el pegado y pierde todo lo que el token ya subió (y si bajó, lo cuenta igual de abajo).
    Pegando al toque, la línea base de Rick queda pegada a la del aviso.
    """
    todas = _calls()
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if minutos:
        desde = time.time() - minutos * 60
        base = [x for x in todas if (x.get("ts_epoch") or 0) >= desde]
    else:
        base = todas if todo else [x for x in todas if str(x.get("ts", "")).startswith(hoy)]
    return bloque_pegar(base, n)


def _buscar(mint):
    return next((x for x in _calls() if (x.get("mint") or "").lower() == (mint or "").lower()), None)


def evaluar(mint):
    """¿Hubo PROFIT después de la call? El chequeo tipo Rick, con nuestra línea base.

    La diferencia con Rick: él mide desde el FDV del primer scan (el momento en que alguien pega el
    contrato), así que si el pegado fue tarde su cuenta arranca corrida. Acá la línea base es el FDV
    del AVISO, que es lo que de verdad se podía tomar.
    """
    x = _buscar(mint)
    if not x:
        p = PA.dexscreener(mint) if mint else None
        if not p:
            return f"🔎 `{mint}` no está en nuestro registro y tampoco lo encuentro en el mercado."
        return (f"🔎 `{mint}` **no es una de nuestras calls** (no tengo el FDV del aviso, así que no "
                f"puedo decir si hubo profit después de la call).\n"
                f"   Ahora: FDV `{_usd(p.get('marketCap') or p.get('fdv') or 0)}` · "
                f"liquidez `{_usd((p.get('liquidity') or {}).get('usd') or 0)}`")
    m0, mk, mm = _pico(x)
    lin = [f"📊 **${x.get('simbolo')}** — ¿hubo profit después de la call?",
           f"   · aviso: {str(x.get('ts'))[5:16]} UTC · FDV en el aviso `{_usd(m0)}`"]
    if mk:
        h = _horas_al_pico(x)
        t = f"{h*60:.0f} min" if h is not None and h < 1 else (f"{h:.1f} h" if h is not None else "?")
        lin.append(f"   · pico: `{_usd(mk)}` = **{mm:.2f}x** (a los {t} del aviso)")
    r = x.get("resultados") or {}
    if r:
        hs = ", ".join(f"+{k} h: {v.get('multiplo'):.2f}x" for k, v in sorted(r.items(), key=lambda kv: int(kv[0])) if v.get("multiplo") is not None)
        if hs:
            lin.append(f"   · medido: {hs}")
    p = PA.dexscreener(mint)
    if p:
        ahora = p.get("marketCap") or p.get("fdv") or 0
        if ahora and m0:
            lin.append(f"   · ahora: `{_usd(ahora)}` = {ahora/m0:.2f}x")
    # veredicto en llano, que es lo que se pregunta
    seg = [s for s in (x.get("seguimiento") or []) if s.get("mcap")]
    edad = (time.time() - (x.get("ts_epoch") or 0)) / 60
    if len(seg) < 2 and edad < 20:
        # OJO: una call de hace 2 minutos tiene UNA sola muestra (la del aviso), así que el "pico" es
        # el propio aviso y decir "no hubo profit" sería mentir. Se avisa que falta medir.
        lin.append(f"   ⏳ **Muy reciente** ({edad:.0f} min): el muestreo todavía no tiene con qué "
                   f"medir el después. Volvé a preguntar en un rato.")
    elif mm is None:
        lin.append("   ⏳ todavía sin medición de precio.")
    elif mm >= 1.5:
        lin.append(f"   ✅ **SÍ HUBO PROFIT** — llegó a {mm:.2f}x; el que entró en el aviso y salió en "
                   f"el pico ganó. (Y si aguantó, abajo está dónde quedó.)")
    elif mm >= 1.1:
        lin.append(f"   🟡 **PROFIT MARGINAL** — {mm:.2f}x: daba para un 10% y poco más, contando comisiones.")
    else:
        extra = ""
        p2 = PA.dexscreener(mint)
        if p2 and m0:
            a = p2.get("marketCap") or p2.get("fdv") or 0
            if a:
                extra = f" Y sigue bajando: hoy está en {a/m0:.2f}x del aviso." if a/m0 < mm else ""
        lin.append(f"   ❌ **NO HUBO PROFIT** — nunca pasó de {mm:.2f}x después del aviso.{extra}")
    return "\n".join(lin)


def ultimas_evaluaciones(n=6):
    """El veredicto corto de las últimas N calls (sí/no hubo profit)."""
    xs = [x for x in _calls() if _pico(x)[2] is not None]
    xs.sort(key=lambda x: -(x.get("ts_epoch") or 0))
    lin = [f"📋 **Las últimas {min(n, len(xs))} calls — ¿hubo profit después?**", ""]
    for x in xs[:n]:
        m0, mk, mm = _pico(x)
        seg = [s for s in (x.get("seguimiento") or []) if s.get("mcap")]
        edad = (time.time() - (x.get("ts_epoch") or 0)) / 60
        if len(seg) < 2 and edad < 20:            # reciente: el pico ES el aviso, no se puede juzgar
            lin.append(f"   ⏳ **${str(x.get('simbolo'))[:12]}** — reciente ({edad:.0f} min), todavía "
                       f"sin medir el después  ·  `{x.get('mint')}`")
            continue
        ico = "✅" if mm >= 1.5 else ("🟡" if mm >= 1.1 else "❌")
        # "ahora" sale de la última muestra (ya está tomada cada ~4 min): cero llamadas extra
        ult = next((s["mcap"] for s in reversed(x.get("seguimiento") or []) if s.get("mcap")), None)
        ahora_txt = f" · ahora {ult/m0:.2f}x" if (ult and m0) else ""
        nota = " (nunca pasó del aviso)" if mm < 1.05 else ""
        lin.append(f"   {ico} **${str(x.get('simbolo'))[:12]}** — aviso {_usd(m0)} → pico "
                   f"{_usd(mk)} = {mm:.2f}x{nota}{ahora_txt}  ·  `{x.get('mint')}`")
    return "\n".join(lin)


def edad_min(x):
    """Cuántos minutos pasaron desde el aviso."""
    return (time.time() - (x.get("ts_epoch") or 0)) / 60


def texto_tabla(todo=False, n=10, argos=False):
    todas = _calls()
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = todas if todo else [x for x in todas if str(x.get("ts", "")).startswith(hoy)]
    if argos:
        base = [x for x in base if _es_argos(x)]
    cuando = "histórico completo" if todo else f"hoy ({hoy} UTC)"
    lineas = [(f"📊 **TABLA DE ARGOS — {cuando}**" if argos else f"📊 **TABLA DE CALLS — {cuando}**"),
              "_(avisos del detector de grupos: del FDV del aviso al pico posterior)_" if argos
              else "_(del FDV del aviso al pico posterior — la misma cuenta que /ga de Rick)_", ""]
    lineas += ranking(base, f"🏆 TOP {n} del período:", n)
    lineas += [""] + resumen(base, "📈 RESUMEN DEL PERÍODO:")
    return "\n".join(lineas)


def main():
    ap = argparse.ArgumentParser(description="Tabla de resultados de las calls (estilo /ga de Rick)")
    ap.add_argument("--todo", action="store_true", help="histórico completo, no sólo hoy")
    ap.add_argument("--discord", action="store_true", help="manda la tabla al canal")
    ap.add_argument("--pegar", action="store_true", help="sólo los contratos para pegar en Rick")
    ap.add_argument("--argos", action="store_true", help="(por defecto) sólo los avisos de Argos")
    ap.add_argument("--todas", action="store_true", help="incluir también las calls del detector viejo (apagado)")
    ap.add_argument("--mint", default=None, help="¿hubo profit después de la call de ESE contrato?")
    ap.add_argument("--profit", action="store_true", help="sólo el veredicto de las últimas calls")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--canal", default=None)
    a = ap.parse_args()

    todas = _calls()
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    del_dia = [x for x in todas if str(x.get("ts", "")).startswith(hoy)]
    base = todas if a.todo else del_dia
    if not a.todas:                      # por defecto, SÓLO los avisos de Argos (el resto está apagado)
        base = [x for x in base if _es_argos(x)]

    if a.mint:
        print(evaluar(a.mint))
        return 0
    if a.profit:
        print(ultimas_evaluaciones(a.n))
        return 0
    if a.pegar:
        for m in bloque_pegar(base, a.n):
            print(m)
        return 0

    cuando = "histórico completo" if a.todo else f"hoy ({hoy} UTC)"
    titulo = "TABLA DE CALLS" if a.todas else "TABLA DE ARGOS"
    lineas = [f"📊 **{titulo} — {cuando}**",
              "_(avisos del detector de grupos: del FDV del aviso al pico posterior)_" if a.argos
              else "_(del FDV del aviso al pico posterior — la misma cuenta que /ga de Rick)_", ""]
    lineas += ranking(base, f"🏆 TOP {a.n} del período:", a.n)
    lineas += [""] + resumen(base, "📈 RESUMEN DEL PERÍODO:")
    if not a.todo and lineas and a.todas:
        top = bloque_pegar(del_dia, 5)
        if top:
            lineas += ["", "🧪 **Para Rick**: pegá estos contratos a mano en el chat (él ignora los mensajes",
                       "de bots). ⚠️ Ojo: Rick toma como línea base el precio del momento en que VOS los",
                       "pegás, así que con estos (del día) va a medir desde ahora y no desde el aviso:"]
            lineas += [f"`{m}`" for m in top]
    texto = "\n".join(lineas)
    print(texto)
    if a.discord:
        r = PA.enviar_en_canal(texto, canal=a.canal)
        print(f"\n[envío a Discord: {r}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
