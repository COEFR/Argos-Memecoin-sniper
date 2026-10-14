#!/usr/bin/env python3
"""humanizar2.py — Humanizador 2: reemplazo de palabras por reglas (port del algoritmo
de OrbitWebTools/Humanize-AI, que corre en el navegador, adaptado al castellano).

Diferencia con el humanizador 1: aquél usa un modelo y edita respetando el original; éste
es determinista por diccionario, cambia palabras "de IA" por equivalentes humanos y varía
el arranque/cierre de las oraciones. Los dos conviven a propósito.

Uso:
    python3 humanizar2.py --texto "texto a humanizar"
    python3 humanizar2.py archivo.txt
"""
import argparse
import json
import random
import re
import sys

# Equivalencias: jerga de IA -> forma humana. Se aplican primero las de varias palabras.
SINONIMOS = {
    # muletillas y conectores
    "es importante destacar que": "hay que decir que",
    "es importante mencionar que": "vale decir que",
    "cabe destacar que": "hay que aclarar que",
    "cabe mencionar que": "vale mencionar que",
    "es fundamental comprender": "hay que entender",
    "sin duda alguna": "de verdad",
    "sin lugar a dudas": "de verdad",
    "en el mundo actual": "hoy",
    "en la era digital": "hoy",
    "en la sociedad moderna": "hoy",
    "en la actualidad": "hoy",
    "no obstante": "pero",
    "asimismo": "también",
    "además": "y también",
    "por consiguiente": "por eso",
    "en consecuencia": "por eso",
    "en resumen": "en síntesis",
    "en conclusión": "en síntesis",
    "por otro lado": "a la vez",
    "de igual manera": "igual",
    "a fin de": "para",
    "con el fin de": "para",
    "con el objetivo de": "para",
    "debido al hecho de que": "porque",
    "en el ámbito de": "en",
    "en materia de": "sobre",
    "en términos de": "en",
    "a nivel de": "en",
    "de cara a": "para",
    "en aras de": "para",
    "el hecho de que": "que",
    "hacer uso de": "usar",
    "llevar a cabo": "hacer",
    "lleva a cabo": "hace",
    "a través de": "con",
    "por medio de": "con",
    # verbos hinchados
    "optimizar": "mejorar",
    "optimiza": "mejora",
    "utilizar": "usar",
    "utiliza": "usa",
    "implementar": "aplicar",
    "implementa": "aplica",
    "realizar": "hacer",
    "realiza": "hace",
    "brindar": "dar",
    "brinda": "da",
    "constituye": "es",
    "representa": "es",
    "posee": "tiene",
    "requiere": "necesita",
    "facilitar": "ayudar",
    "permitir": "dejar",
    "generar": "crear",
    "genera": "crea",
    "obtener": "conseguir",
    "adquirir": "comprar",
    "desarrollar": "hacer",
    "desarrolla": "hace",
    "incrementar": "subir",
    "disminuir": "bajar",
    # adjetivos de folleto
    "crucial": "clave",
    "fundamental": "central",
    "robusto": "firme",
    "integral": "de fondo",
    "innovador": "nuevo",
    "innovadora": "nueva",
    "significativo": "grande",
    "significativa": "grande",
    "importante": "importante",
    "relevante": "que importa",
    "escalable": "que crece",
    "sinérgico": "combinado",
    "óptimo": "mejor",
    "óptima": "mejor",
    "diversos": "varios",
    "diversas": "distintas",
    "amplio": "grande",
    "amplia": "grande",
    "exhaustivo": "completo",
    "exhaustiva": "completa",
    # sustantivos de folleto
    "sinergia": "trabajo en conjunto",
    "ecosistema": "entorno",
    "empoderamiento": "autonomía",
    "aprovechamiento": "uso",
    "fortalecimiento": "fuerza",
    "implementación": "puesta en marcha",
    "optimización": "mejora",
    "la utilización": "el uso",              # multi-palabra: arregla el artículo
    "utilización": "uso",
    "metodología": "forma de trabajar",       # femenino: "una método" quedaría mal
    "la metodología": "la forma de trabajar",
    "problemática": "asunto",                 # femenino, igual que la original
    "la problemática": "el asunto",
    "finalidad": "fin",
    "potencial": "margen",
    "beneficios": "ventajas",
    "aspectos": "puntos",
    "factores": "cosas",
    "elementos": "cosas",
    "iniciativas": "planes",
}

ARRANQUES = ["En concreto, ", "La cosa es que ", "Mirá, ", "En realidad, ", "Y bueno, "]
CIERRES = [" Y eso cuenta.", ", y no es poco.", ", sin vueltas."]


def humanizar(texto, azar=True):
    # 1. frases de varias palabras (primero las más largas, para que ganen)
    proc = texto
    def _sub_frase(m):
        rep = SINONIMOS[m.group(0).lower()]
        # conserva la mayúscula inicial (si no, "En resumen" quedaba "en síntesis")
        return rep[0].upper() + rep[1:] if m.group(0)[0].isupper() else rep

    for clave in sorted((k for k in SINONIMOS if " " in k), key=len, reverse=True):
        proc = re.sub(re.escape(clave) + r"\b", _sub_frase, proc, flags=re.IGNORECASE)

    # 2. palabra por palabra, conservando mayúscula inicial y acentos
    def _cambiar(m):
        palabra = m.group(0)
        limpia = palabra.lower()
        rep = SINONIMOS.get(limpia)
        if not rep:
            return palabra
        return rep[0].upper() + rep[1:] if palabra[0].isupper() else rep

    proc = re.sub(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", _cambiar, proc)

    # 3. variar arranque (~20%) y cierre (~10%), como el original
    oraciones = re.split(r"(?<=[.!?])\s+", proc)
    salida = []
    for o in oraciones:
        if azar and len(o) > 24 and random.random() > 0.8:
            o = random.choice(ARRANQUES) + o[0].lower() + o[1:]
        if azar and len(o) > 40 and random.random() > 0.9 and o.endswith("."):
            o = o[:-1] + random.choice(CIERRES)
        salida.append(o)
    return " ".join(salida).strip()


def main():
    ap = argparse.ArgumentParser(description="Humanizador 2 (por reglas, castellano)")
    ap.add_argument("archivo", nargs="?")
    ap.add_argument("--texto")
    ap.add_argument("--sin-azar", action="store_true", help="resultado reproducible")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    texto = a.texto or (open(a.archivo, encoding="utf-8", errors="replace").read()
                        if a.archivo else sys.stdin.read())
    if not texto.strip():
        print("(texto vacío)"); return 1
    res = humanizar(texto, azar=not a.sin_azar)
    if a.json:
        print(json.dumps({"entrada": texto, "salida": res, "cambios": texto != res},
                         ensure_ascii=False))
    else:
        print(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
