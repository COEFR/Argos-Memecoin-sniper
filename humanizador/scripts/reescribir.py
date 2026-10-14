#!/usr/bin/env python3
"""reescribir.py - mide lo que MIDEN LOS DETECTORES (no lo que se ve bonito).

Un detector de IA puntua, sobre todo, dos cosas:
  - BURSTINESS: cuanto varia el largo de las oraciones. Texto de maquina = todas parecidas.
  - PERPLEJIDAD: cuanto se repiten las palabras. Texto de maquina = repite el vocabulario.

Este script no reescribe: mide e informa. La reescritura la hace el agente, guiado por
estas cifras y por las reglas del skill.
"""
import argparse
import collections
import re
import statistics
import sys

VACIAS = set(("de la que el en y a los se del las un por con no una su para es al lo como mas "
              "pero sus le ya o este si porque esta entre cuando muy sin sobre tambien me hasta "
              "hay donde quien desde todo nos durante todos uno les ni contra otros ese eso ante "
              "ellos e esto mi antes algunos unos yo otro otras otra tanto esa estos mucho "
              "quienes nada muchos cual poco ella estar estas algunas algo nosotros mis tu te ti "
              "tus ellas os mio mia suyo suya nuestro nuestra").split())

RELLENO = re.compile(
    r"\b(asimismo|en consecuencia|por consiguiente|por otro lado|de igual manera|"
    r"en resumen|en conclusion|en conclusión|cabe destacar|cabe mencionar|"
    r"es importante destacar|es importante mencionar|en definitiva|a fin de cuentas)\b",
    re.IGNORECASE)


def medir(texto):
    oraciones = [o.strip() for o in re.split(r"(?<=[.!?])\s+", texto) if len(o.split()) >= 3]
    largos = [len(o.split()) for o in oraciones]
    m = statistics.mean(largos) if largos else 0
    cv = (statistics.pstdev(largos) / m) if m else 0
    palabras = [p for p in re.findall(r"[a-záéíóúñü]+", texto.lower())
                if len(p) > 4 and p not in VACIAS]
    repes = [(w, n) for w, n in collections.Counter(palabras).most_common(8) if n > 1]
    relleno = RELLENO.findall(texto)
    return {"oraciones": len(oraciones), "largos": largos, "largo_medio": round(m, 1),
            "burstiness": round(cv, 3), "repeticiones": repes, "relleno": len(relleno)}


def informe(m, titulo=""):
    if titulo:
        print(f"  {titulo}")
    print(f"    oraciones: {m['oraciones']} - largo medio: {m['largo_medio']} palabras")
    print(f"    largos: {m['largos']}")
    if m["burstiness"] >= 0.45:
        marca = "OK humano"
    elif m["burstiness"] >= 0.35:
        marca = "~ medio"
    else:
        marca = "X maquina"
    print(f"    BURSTINESS: {m['burstiness']}  [{marca}]   (objetivo >0.45)")
    if m["repeticiones"]:
        print("    repite: " + ", ".join(f"{w}x{n}" for w, n in m["repeticiones"][:6]))
    else:
        print("    repite: nada relevante OK")
    print(f"    conectores de relleno: {m['relleno']}" + ("  <- sacar" if m["relleno"] else "  OK"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archivos", nargs="*")
    ap.add_argument("--texto", help="texto a medir en vez de archivos")
    a = ap.parse_args()
    if a.texto:
        import tempfile, os
        t = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        t.write(a.texto); t.close()
        a.archivos = [t.name]
        os.unlink(t.name) if False else None
    for f in (a.archivos or []):
        t = open(f, encoding="utf-8", errors="replace").read()
        informe(medir(t), titulo=f.split("/")[-1])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
