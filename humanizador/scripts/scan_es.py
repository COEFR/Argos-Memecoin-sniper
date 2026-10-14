#!/usr/bin/env python3
"""scan_es.py — scanner de jerga de IA en castellano.

Sin dependencias: sólo librería estándar. Devuelve un puntaje de limpieza (0-100),
las prohibiciones duras encontradas y los marcadores blandos.

Uso:
    python3 scan_es.py archivo.txt
    echo "texto" | python3 scan_es.py -
    python3 scan_es.py --texto "texto a analizar"
"""
import argparse
import re
import statistics
import sys

# Prohibiciones duras: frases que delatan texto generado. Cada una resta 8.
DURAS = [
    r"en el mundo actual", r"en la era digital", r"en la sociedad moderna",
    r"en la actualidad", r"hoy en día,?\s+en un mundo",
    r"es importante destacar", r"es importante mencionar", r"cabe mencionar",
    r"cabe destacar", r"vale la pena (señalar|mencionar|destacar)",
    r"es fundamental comprender", r"no es casualidad que",
    r"en resumen,", r"en conclusión,", r"para finalizar,", r"en definitiva,",
    r"juega un papel (crucial|fundamental|vital|clave)",
    r"(es|representa) un pilar fundamental", r"marca un antes y un después",
    r"sin duda alguna", r"sin lugar a dudas", r"es indiscutible que",
    r"no solo .{0,40}? sino (que )?también", r"no solamente .{0,40}? sino también",
    r"un abanico de posibilidades", r"un sinf[íi]n de",
    r"aprovechar al m[áa]ximo", r"desbloquear (todo )?el potencial",
    r"llevar al l[íi]mite", r"llevar a cabo", r"hacer uso de",
    r"en aras de", r"de cara a", r"a nivel de", r"el hecho de que",
    r"en el [áa]mbito de", r"en materia de", r"en t[ée]rminos de",
    r"implementar soluciones", r"brindar una (experiencia|soluci[óo]n)",
    r"tomar un paso atr[áa]s", r"hacer una diferencia",
    r"tener un impacto (en|significativo)", r"tomar decisiones informadas",
    r"promete revolucionar", r"cambiar[áa] para siempre", r"est[áa] transformando",
    r"experto en la materia", r"fuentes confiables", r"ampliamente reconocido",
    r"optimizar (los )?procesos", r"soluciones (innovadoras|integrales)",
    r"enfoque integral", r"experiencia integral", r"ecosistema (digital|de)",
    r"(lograr|obtener) resultados [óo]ptimos", r"potenciar (al m[áa]ximo)?",
    r"empoderar a", r"sinergia(s)? entre", r"robusto y escalable",
]

# Marcadores blandos: cada uno resta 2.
BLANDOS = {
    "raya larga (—) donde va punto o coma": r"—",
    "apertura con ¡ en texto formal": r"(?m)^¡",
    "empalme de párrafo (Además/Asimismo/Por otro lado)": r"(?m)^(Adem[áa]s|Asimismo|Por otro lado|Por su parte|A su vez)[, ]",
    "tricolon (tres elementos seguidos)": r"\b\w+o?, \w+o? y \w+o?\b",
    "comillas tipográficas «»": r"«|»",
    "dos puntos de énfasis de más": r"(?m)^(Importante|Clave|Ojo):",
    "transición vacía (dicho esto/así, entonces)": r"(Dicho esto|En ese sentido|En esta l[íi]nea)[, ]",
    "cita de autoridad vaga": r"(seg[úu]n los expertos|los especialistas (señalan|indican)|estudios (demuestran|revelan))",
    "énfasis redundante (muy único/completamente único)": r"(muy [úu]nico|completamente [úu]nico|totalmente gratuito)",
    "cierre que repite (en otras palabras/finalmente)": r"(En otras palabras|En pocas palabras|Finalmente,)",
}


def limpiar(t):
    return re.sub(r"\s+", " ", t).lower()


# En texto académico/de investigación, estas fórmulas son legítimas: no son jerga de IA.
# Sin esto el scanner marcaba papers correctos (medido: un párrafo de tesis bajaba a 44/100
# sólo por citar autores y usar conectores formales).
LEGITIMAS_ACADEMICAS = [
    r"en el [áa]mbito (de|académico|educativo|empresarial)",
    r"en materia de", r"en t[ée]rminos de",
    r"el estudio (desarrollado|realizado|presentado) por",
    r"los (autores|investigadores) (señalan|indican|concluyen)",
    r"seg[úu]n (el|los|la) (estudio|autores|investigadores)",
    r"(cabe|es necesario) (mencionar|señalar|destacar|aclarar)",
    r"es importante (destacar|mencionar|señalar)",
    r"a partir de (estos|los) hallazgos",
    r"en (resumen|conclusi[óo]n|s[íi]ntesis),?",
    r"no solo .{0,60}? sino (que )?tambi[ée]n",
]


def analizar(texto, genero=None):
    # OJO: los fragmentos se cortan sobre la MISMA cadena en la que se busca. Antes se buscaba en
    # 'bajo' (normalizado) y se cortaba sobre 'texto' (crudo): con saltos de línea o espacios dobles
    # los índices se corrían y salían cortes a mitad de palabra.
    texto_n = re.sub(r"\s+", " ", texto)
    bajo = texto_n.lower()
    duras_halladas = []
    exentas = []
    for pat in DURAS:
        for m in re.finditer(pat, bajo):
            frag = texto_n[max(0, m.start() - 10): m.end() + 10].strip()
            # en modo académico, si la fórmula es legítima para un paper, no descuenta
            if genero == "academico" and any(re.search(ex, frag, re.IGNORECASE)
                                             for ex in LEGITIMAS_ACADEMICAS):
                exentas.append(frag)
                continue
            duras_halladas.append((pat, frag))

    blandos_hallados = []
    for nombre, pat in BLANDOS.items():
        for _ in re.finditer(pat, texto):
            blandos_hallados.append(nombre)

    # ritmo: variación del largo de las oraciones (burstiness)
    oraciones = [o for o in re.split(r"[.!?]+\s", texto) if len(o.split()) >= 3]
    if len(oraciones) >= 4:
        largos = [len(o.split()) for o in oraciones]
        media = statistics.mean(largos)
        cv = (statistics.pstdev(largos) / media) if media else 0
    else:
        cv = None

    score = 100 - 8 * len(duras_halladas) - 2 * len(blandos_hallados)
    if cv is not None and cv < 0.35:
        score -= 6                      # todo del mismo largo: ritmo de máquina
    score = max(0, min(100, score))
    return score, duras_halladas, blandos_hallados, cv, exentas


def main():
    ap = argparse.ArgumentParser(description="Scanner de jerga de IA en castellano")
    ap.add_argument("archivo", nargs="?", help="archivo .txt (o '-' para stdin/--texto)")
    ap.add_argument("--texto", help="texto a analizar directamente")
    ap.add_argument("--genero", choices=["academico"], help="académico: no marca fórmulas legítimas de paper")
    a = ap.parse_args()

    if a.texto:
        texto = a.texto
    elif a.archivo and a.archivo != "-":
        texto = open(a.archivo, encoding="utf-8", errors="replace").read()
    else:
        texto = sys.stdin.read()

    if not texto.strip():
        print("(texto vacío)"); return 1

    score, duras, blandos, cv, exentas = analizar(texto, genero=a.genero)
    partes = []
    if score >= 90:
        partes.append("limpio")
    elif score >= 70:
        partes.append("retoque puntual")
    elif score >= 50:
        partes.append("edición de fondo")
    else:
        partes.append("rehacer")

    print("=== scan_es: jerga de IA en castellano ===\n")
    print(f"LIMPIEZA: {score}/100  [{', '.join(partes)}]")
    print("  (90+ limpio · 70-89 retoque · 50-69 edición · <50 rehacer)")
    print(f"\n  -{8*len(duras)}  prohibiciones duras: {len(duras)}")
    print(f"  -{2*len(blandos)}  marcadores blandos: {len(blandos)}")
    if cv is not None:
        est = "parejo (ritmo de máquina)" if cv < 0.35 else "variado (humano)"
        print(f"       ritmo: CV={cv:.3f} → {est}")

    if duras:
        print("\nPROHIBICIONES DURAS:")
        vistos = set()
        for pat, frag in duras:
            if pat in vistos:
                continue
            vistos.add(pat)
            print(f"  ⛔ «{frag}»")
    if blandos:
        print("\nMARCADORES BLANDOS:")
        for nombre in dict.fromkeys(blandos):
            print(f"  • {nombre}")
    if exentas and a.genero == "academico":
        print(f"\n(académico: {len(exentas)} fórmula(s) legítima(s) de paper, no descontadas)")
    if not duras and not blandos:
        print("\n✓ sin marcas de IA detectadas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
