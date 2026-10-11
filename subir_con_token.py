#!/usr/bin/env python3
"""Crea los repositorios en GitHub (privados) y sube los proyectos ya preparados.

El token se pasa por variable de entorno (GITHUB_TOKEN_NUEVO) y NO se escribe en ningún archivo del
repo. El remoto queda como URL limpia; el push usa el token en la URL una sola vez.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

BASE = os.path.expanduser("~/.hermes/data")
TOKEN = os.environ["GITHUB_TOKEN_NUEVO"]
USUARIO = "COEFR"
H = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
     "User-Agent": "hermes-gh", "Content-Type": "application/json"}

PROYECTOS = [
    ("dolarbolivia", "dolarbolivia", "Precio del dólar en Bolivia: sitio React + API propia"),
    ("edgar", "edgar-reporte-empleo", "Informe diario automático de búsqueda de empleo"),
    ("facebook", "boliviatoy-facebook", "Publicaciones automáticas para la página de BoliviaToys"),
    ("zeroscript", "zeroscript", "Panel de control para Roblox Studio (extensión)"),
    ("aivtuber", "ai-vtuber-tiktok", "VTuber automático para TikTok Live, costo cero"),
    ("memecoins", "Argos-Memecoin-sniper", "Argos: detección on-chain de grupos de wallets en Solana"),
]


def api(method, path, body=None):
    req = urllib.request.Request("https://api.github.com" + path, method=method, headers=H,
                                 data=json.dumps(body).encode() if body else None)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read().decode() or "null")
    except urllib.error.HTTPError as e:
        return {"__error": e.code, "__body": e.read().decode()[:200]}


def sh(cmd, cwd, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


# 0. quién es y qué puede hacer el token
u = api("GET", "/user")
print("token de:", u.get("login") if "__error" not in u else u)

urls = []
for carpeta, repo, desc in PROYECTOS:
    p = os.path.join(BASE, carpeta)
    if not os.path.isdir(os.path.join(p, ".git")):
        print(f"  {carpeta}: (sin git preparado, salteo)")
        continue
    # 1. crear el repo (privado)
    r = api("POST", "/user/repos", {"name": repo, "description": desc, "private": True})
    if "__error" in r and r["__error"] != 422:      # 422 = ya existe
        print(f"  ⛔ {repo}: {r['__error']} {r['__body'][:120]}")
        continue
    creado = "__error" not in r
    # 2. remoto limpio + push con el token una sola vez
    clean = f"https://github.com/{USUARIO}/{repo}.git"
    tok_url = f"https://x-access-token:{TOKEN}@github.com/{USUARIO}/{repo}.git"
    sh(["git", "remote", "remove", "origin"], p)
    sh(["git", "remote", "add", "origin", clean], p)
    rc, out = sh(["git", "push", tok_url, "main:main", "-f"], p)
    if rc == 0:
        print(f"  ✅ {'creado' if creado else 'ya existía'} + subido: {clean}")
        urls.append(clean)
    else:
        print(f"  ⛔ {repo}: {out[-160:]}")

print("\n=== PUBLICADOS ===")
for u_ in urls:
    print("  ", u_)
