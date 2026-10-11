#!/usr/bin/env python3
"""ATH de todos los contratos del server.

Toma lo que ya calculó la auditoría (por contrato: el FDV del aviso y el pico posterior) y completa
cada fila con el máximo histórico real, medido sobre las velas diarias del pool más líquido en
GeckoTerminal, que responde sin clave.

La API gratuita tiene límite de ritmo, así que cachea lo medido y procesa de a poco por corrida
(por defecto 40 contratos por pasada). Repetido desde el cron, termina cubriendo los 165.

Salida: ath_server.json, que es lo que muestra el comando !ath en Discord.
Uso: python3 ath_server.py [--tope N]
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(AQUI, "auditoria_server.json")
SALIDA = os.path.join(AQUI, "ath_server.json")
CACHE = os.path.join(AQUI, "ath_cache.json")
UA = {"User-Agent": "hermes-argos/1.0"}
GT = "https://api.geckoterminal.com/api/v2/networks/solana"


PAUSA = 4.2          # segundos entre contratos (30 llamadas/min ÷ 2 por contrato)
ESPERA_429 = 65      # si la API corta por ritmo, se espera y se sigue


def _json(url, intentos=3):
    for i in range(intentos):
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
    """(fdv_en_el_maximo, precio_max, precio_actual, fecha_del_maximo) sobre el pool más líquido."""
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
    return fdv * (mx / actual), mx, actual, [v[0] for v in velas if v[2] == mx][0], fdv


def fmt(x):
    if not x:
        return "—"
    x = float(x)
    if x >= 1_000_000:
        return "%.1fM" % (x / 1_000_000)
    if x >= 1_000:
        return "%.0fk" % (x / 1_000)
    return "%.0f" % x


def main():
    tope = 15
    if "--tope" in sys.argv:
        tope = int(sys.argv[sys.argv.index("--tope") + 1])

    aud = json.load(open(AUDIT))
    filas = [f for f in (aud.get("filas") or []) if f.get("mint")]
    # primero los que encabezan la tabla: así el ATH aparece arriba desde la primera pasada
    filas.sort(key=lambda f: -(f.get("mult") or 0))
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    nuevos = 0
    for f in filas:
        m = f["mint"]
        if m in cache and cache[m].get("ath"):
            continue   # los fallos se reintentan en la próxima pasada
        if nuevos >= tope:
            break
        r = ath_del_pool(m)
        time.sleep(PAUSA)
        cache[m] = {"ath": r[0], "max": r[1], "cuando": r[3], "fdv_hoy": r[4]} if r else {"ath": None}
        nuevos += 1
    with open(CACHE, "w") as fh:
        json.dump(cache, fh)
    print("medidos esta pasada: %d · en caché: %d de %d" % (nuevos, len(cache), len(filas)))

    salida = []
    for f in filas:
        c = cache.get(f["mint"], {})
        m0, mp = f.get("m0"), f.get("mp")
        ath = c.get("ath")
        actual = c.get("fdv_hoy")
        salida.append({
            "simbolo": f.get("simbolo"),
            "mint": f["mint"],
            "canal": f.get("canal"),
            "mult_pico": f.get("mult"),
            "fdv_aviso": m0,
            "pico": mp,
            "ath": ath,
            "actual": actual,
            "mult_ath": (ath / m0) if (ath and m0) else None,
            "cayo": (actual / ath) if (actual and ath) else None,
            "pool_no_cubre_el_pico": bool(ath and mp and mp > ath),
            "fuente": f.get("fuente"),
            "ts": f.get("ts"),
        })
    salida.sort(key=lambda x: -(x.get("mult_pico") or 0))
    with open(SALIDA, "w") as fh:
        json.dump({"generado": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                   "contratos": len(salida), "filas": salida}, fh, ensure_ascii=False, indent=1)
    print("escrito ath_server.json con %d contratos" % len(salida))
    for x in salida[:5]:
        print("  %-12s aviso %s → pico %s (%.1fx) · ath %s · hoy %s" % (
            x["simbolo"], fmt(x["fdv_aviso"]), fmt(x["pico"]), x["mult_pico"] or 0,
            fmt(x["ath"]), fmt(x["actual"])))


if __name__ == "__main__":
    raise SystemExit(main())
