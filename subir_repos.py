#!/usr/bin/env python3
"""Sube los proyectos a GitHub una vez que los repositorios YA EXISTEN.

El token disponible no tiene permiso para CREAR repositorios (devuelve 403), así que la creación hay
que hacerla desde la web (o con un token con permiso). En cuanto existan, este script hace todo lo demás:
conecta cada proyecto con su repo por SSH y empuja el commit ya preparado.

Uso:  python3 subir_repos.py
"""
import os
import subprocess

BASE = os.path.expanduser("~/.hermes/data")
USUARIO = "COEFR"
PROYECTOS = [
    ("dolarbolivia", "dolarbolivia"),
    ("edgar", "edgar-reporte-empleo"),
    ("facebook", "boliviatoy-facebook"),
    ("zeroscript", "zeroscript"),
    ("aivtuber", "ai-vtuber-tiktok"),
    ("memecoins", "Argos-Memecoin-sniper"),
]


def sh(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


if __name__ == "__main__":
    print("=== subiendo a GitHub ===")
    for carpeta, repo in PROYECTOS:
        p = os.path.join(BASE, carpeta)
        if not os.path.isdir(os.path.join(p, ".git")):
            print(f"  {carpeta}: (no tiene git)")
            continue
        url = f"git@github.com:{USUARIO}/{repo}.git"
        sh(["git", "remote", "remove", "origin"], p)
        sh(["git", "remote", "add", "origin", url], p)
        rc, out = sh(["git", "push", "-u", "origin", "main"], p)
        if rc == 0:
            print(f"  ✅ {carpeta} → https://github.com/{USUARIO}/{repo}")
        else:
            print(f"  ⛔ {carpeta}: {out[-150:]}")
