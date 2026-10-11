#!/usr/bin/env python3
"""ATH y resultado de TODAS las calls que mandó el bot.

La línea base es la correcta: el `mcap_call` de `calls_historial.json`, que es el FDV en el momento
exacto en que el bot publicó el contrato. Sobre eso se mide:

  · pico   → el máximo de la serie de seguimiento que ya guarda el bot.
  · ath    → el máximo histórico real del token, sobre las velas diarias del pool del DEX
             (GeckoTerminal, sin clave). Se cachea y se mide de a poco por el límite de ritmo.

Salida: ath_server.json (lo que muestran los comandos !ath y !besti).
Uso: python3 ath_server.py [--tope N] [--todas]
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
CALLS = os.path.join(AQUI, "calls_historial.json")
SALIDA = os.path.join(AQUI, "ath_server.json")
CACHE = os.path.join(AQUI, "ath_cache.json")
UA = {"User-Agent": "hermes-argos/1.0"}
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
PAUSA = 4.2
ESPERA_429 = 65


def _json(url, intentos=3):
    for _ in range(intentos):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("    (429: espero %ds)" % ESPERA_429, flush=True)
                time.sleep(ESPERA_429)
            else:
                time.sleep(1.5)
        except Exception:
            time.sleep(1.5)
    return None


def ath_del_pool(mint):
    """(fdv_en_el_maximo, precio_max, fecha_max, fdv_hoy) sobre el pool más líquido del token."""
    p = _json(GT + "/tokens/" + mint + "/pools")
    if not p or not p.get("data"):
        return None
    mejor = max(p["data"], key=lambda x: float(x["attributes"].get("reserve_in_usd") or 0))
    pool = mejor["id"].split("_")[-1]
    fdv = float(mejor["attributes"].get("fdv_usd") or 0)
    o = _json(GT + "/pools/" + pool + "/ohlcv/day?limit=1000&currency=usd")
    if not o:
        return None
    velas = (o.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
    if not velas or not fdv:
        return None
    mx = max(v[2] for v in velas)
    actual = velas[0][4]
    if not actual:
        return None
    return fdv * (mx / actual), mx, [v[0] for v in velas if v[2] == mx][0], fdv


def _fmt(x):
    if not x:
        return "—"
    x = float(x)
    if x >= 1_000_000:
        return "%.1fM" % (x / 1_000_000)
    if x >= 1_000:
        return "%.0fk" % (x / 1_000)
    return "%.0f" % x


def mostrar(n=14):
    """Tabla de ATH para Discord: desde el FDV en que el bot publicó el contrato."""
    d = json.load(open(SALIDA)) if os.path.exists(SALIDA) else {}
    f = d.get("filas") or []
    if not f:
        return "Todavía no hay mediciones. Corré ath_server.py primero."
    l = ["**ATH de todas las calls** (línea base: el FDV cuando el bot publicó el contrato)",
         "_%d calls · %d con ATH de pool medido · %s_" % (d.get("contratos"), d.get("con_ath"), (d.get("generado") or "")[:16]),
         "```", "%-12s %7s %8s %7s %8s" % ("token", "aviso", "pico", "pico x", "ath pool")]
    for x in f[:n]:
        l.append("%-12s %7s %8s %6sx %8s" % (str(x.get("simbolo"))[:12], _fmt(x.get("fdv_aviso")),
                                             _fmt(x.get("pico")), "%.1f" % (x.get("mult_pico") or 0),
                                             _fmt(x.get("ath"))))
    l.append("```")
    l.append("_El ATH se mide sobre las velas del pool del DEX; el tramo de la curva de pump.fun no "
             "aparece ahí, así que en algunos tokens el pico del aviso es mayor._")
    return "\n".join(l)


def main():
    tope = 12
    if "--tope" in sys.argv:
        tope = int(sys.argv[sys.argv.index("--tope") + 1])
    todas = "--todas" in sys.argv
    if "--mostrar" in sys.argv:
        n = 14
        if "--n" in sys.argv:
            n = int(sys.argv[sys.argv.index("--n") + 1])
        print(mostrar(n))
        return 0

    calls = json.load(open(CALLS))
    # línea base = el FDV del momento en que el bot lo publicó
    calls = [c for c in calls if c.get("mcap_call") and c.get("mint")]
    calls.sort(key=lambda c: -(c.get("ts_epoch") or 0))   # lo más nuevo primero

    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    nuevos = 0
    for c in calls:
        m = c["mint"]
        if m in cache and cache[m].get("ath"):
            continue          # los que fallaron se reintentan
        if nuevos >= tope:
            break
        r = ath_del_pool(m)
        cache[m] = {"ath": r[0], "max": r[1], "cuando": r[2], "fdv_hoy": r[3]} if r else {"ath": None}
        nuevos += 1
        time.sleep(PAUSA)
    with open(CACHE, "w") as fh:
        json.dump(cache, fh)
    print("medidos esta pasada: %d · en caché: %d · calls con base: %d" % (nuevos, len(cache), len(calls)))

    salida = []
    for c in calls:
        seg = [s["mcap"] for s in (c.get("seguimiento") or []) if s.get("mcap")]
        base = c["mcap_call"]
        mp = max(seg) if seg else None
        g = cache.get(c["mint"], {})
        ath, hoy = g.get("ath"), g.get("fdv_hoy")
        salida.append({
            "simbolo": c.get("simbolo") or c.get("nombre") or "?",
            "mint": c["mint"],
            "canal": c.get("etq") or "",
            "ts": c.get("ts") or "",
            "fuente": "mcap_call (el bot lo publicó)",
            "fdv_aviso": base,
            "pico": mp,
            "observaciones": len(seg),
            "ath": ath,
            "actual": hoy,
            "mult_pico": (mp / base) if (mp and base) else None,
            "mult_ath": (ath / base) if (ath and base) else None,
            "cayo": (hoy / ath) if (hoy and ath) else None,
            "pool_no_cubre_el_pico": bool(ath and mp and mp > ath),
        })

    orden = sorted(salida, key=lambda x: -(x.get("mult_ath") or x.get("mult_pico") or 0))
    with open(SALIDA, "w") as fh:
        json.dump({"generado": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                   "contratos": len(salida),
                   "con_ath": sum(1 for x in salida if x["ath"]),
                   "filas": orden}, fh, ensure_ascii=False, indent=1)
    print("escrito ath_server.json · %d calls · %d con ATH" % (len(salida), sum(1 for x in salida if x["ath"])))
    for x in orden[:6]:
        print("  %-12s aviso %8s → pico %8s (%.1fx) · ath %8s" % (
            str(x["simbolo"])[:12], int(x["fdv_aviso"] or 0), int(x["pico"] or 0) if x["pico"] else 0,
            x["mult_pico"] or 0, int(x["ath"]) if x["ath"] else 0))


if __name__ == "__main__":
    raise SystemExit(main())
