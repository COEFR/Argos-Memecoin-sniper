#!/usr/bin/env python3
"""Genera el gráfico de resultados (img/resultados.svg) desde ath_server.json."""
import json
import os
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(AQUI, "ath_server.json")
S = os.path.join(AQUI, "img", "resultados.svg")
TOPE = 10

d = json.load(open(D))
filas = [x for x in (d.get("filas") or []) if x.get("mult_pico")][:TOPE]
total = d.get("contratos") or len(d.get("filas") or [])
con_ath = d.get("con_ath") or 0


def corto(x):
    x = float(x or 0)
    if x >= 1_000_000:
        return "%.1fM" % (x / 1_000_000)
    if x >= 1_000:
        return "%.0fk" % (x / 1_000)
    return "%.0f" % x


alto_fila, ancho, largo_barra = 46, 1000, 520
alto = 132 + alto_fila * len(filas) + 46
maxx = max((x["mult_pico"] or 0) for x in filas) or 1

p = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
     'font-family="Inter,Segoe UI,Helvetica,Arial,sans-serif">' % (ancho, alto, ancho, alto),
     '<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
     '<stop offset="0%" stop-color="#131519"/><stop offset="100%" stop-color="#0a0b0d"/></linearGradient>'
     '<linearGradient id="barra" x1="0" y1="0" x2="1" y2="0">'
     '<stop offset="0%" stop-color="#8a6410"/><stop offset="100%" stop-color="#f3d27a"/></linearGradient>'
     '<linearGradient id="linea" x1="0" y1="0" x2="1" y2="0">'
     '<stop offset="0%" stop-color="#d9a520" stop-opacity="0.9"/>'
     '<stop offset="100%" stop-color="#d9a520" stop-opacity="0.05"/></linearGradient></defs>',
     '<rect width="%d" height="%d" fill="url(#bg)"/>' % (ancho, alto),
     '<rect width="%d" height="3" fill="url(#linea)"/>' % ancho,
     '<text x="40" y="52" font-size="25" font-weight="800" fill="#f2f2f4">RESULTADOS MEDIDOS</text>',
     '<text x="40" y="78" font-size="14.5" fill="#9aa0a8">%s calls del registro · linea base: el FDV '
     'del momento en que el bot publico el contrato · pico posterior medido</text>'
     % ("{:,}".format(total).replace(",", ".")),
     '<text x="40" y="100" font-size="13" font-family="ui-monospace,Menlo,monospace" fill="#d9a520" '
     'fill-opacity="0.75">TOP %d POR MULTIPLO · %d con ATH de pool medido · %s</text>'
     % (len(filas), con_ath, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))]

y = 132
for i, x in enumerate(filas, 1):
    mult = x["mult_pico"] or 0
    w = max(6, largo_barra * (mult / maxx))
    cy = y + 26
    p.append('<text x="40" y="%d" font-size="15" fill="#6b7178" font-family="ui-monospace,monospace">%2d</text>' % (cy, i))
    p.append('<text x="72" y="%d" font-size="16" font-weight="700" fill="#e8e8ea">%s</text>' % (cy, str(x.get("simbolo") or "?")[:16]))
    p.append('<text x="230" y="%d" font-size="13.5" fill="#9aa0a8" font-family="ui-monospace,monospace">%s → %s</text>' % (cy, corto(x.get("fdv_aviso")), corto(x.get("pico"))))
    p.append('<rect x="430" y="%d" width="%d" height="15" rx="7" fill="url(#barra)"/>' % (y + 14, w))
    p.append('<text x="%d" y="%d" font-size="15" font-weight="800" fill="#f3d27a" font-family="ui-monospace,monospace">%.1fx</text>' % (430 + w + 12, cy, mult))
    if x.get("ath"):
        p.append('<text x="866" y="%d" font-size="12.5" fill="#7f858c" font-family="ui-monospace,monospace">ath %s</text>' % (cy, corto(x["ath"])))
    y += alto_fila

p.append('<line x1="40" y1="%d" x2="%d" y2="%d" stroke="#23262b"/>' % (y + 6, ancho - 40, y + 6))
p.append('<text x="40" y="%d" font-size="12.5" fill="#6b7178">El ATH se mide sobre las velas del pool del DEX: '
         'el tramo de la curva de pump.fun no aparece ahi, por eso en algunos tokens el pico del aviso es mayor.</text>' % (y + 32))
p.append('</svg>')

os.makedirs(os.path.dirname(S), exist_ok=True)
open(S, "w").write("\n".join(p))
print("escrito img/resultados.svg · %d filas · top %.1fx · %d bytes" % (len(filas), maxx, os.path.getsize(S)))
