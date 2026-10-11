#!/usr/bin/env python3
"""Avisa al canal si /best quedó publicado en Discord (lo corre el cron después del reinicio).

Sin esto no habría forma de saber si el comando llegó: el registro de los slash commands es global y
Discord los propaga con retraso, así que se consulta la lista real de la aplicación y se avisa.
"""
import json
import os
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))


def env():
    d = {}
    for l in open(os.path.expanduser("~/.hermes/.env")):
        if "=" in l and not l.strip().startswith("#"):
            k, v = l.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def api(path, token):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": f"Bot {token}",
                                          "User-Agent": "hermes-best/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


def main():
    e = env()
    tok = e["DISCORD_BOT_TOKEN"]
    canal = e.get("DISCORD_ARGOS_CHANNEL") or e.get("DISCORD_HOME_CHANNEL")
    try:
        app = api("/applications/@me", tok)
        cmds = api(f"/applications/{app['id']}/commands", tok)
        nombres = sorted(c.get("name") for c in cmds)
        ok = "besti" in nombres
        msg = (f"✅ **`/besti` quedó publicado en Discord** — probá escribir `/best` (si no aparece en la "
               f"lista del cliente, recargá Discord con Ctrl+R).\nComandos de la app: {len(nombres)}"
               if ok else
               f"⚠️ `/besti` todavía no aparece en la lista de Discord (la app tiene {len(nombres)} "
               f"comandos). Puede ser la demora de propagación: probá en unos minutos.")
    except urllib.error.HTTPError as ex:
        msg = f"⚠️ No pude consultar los comandos de la app: HTTP {ex.code}"
    except Exception as ex:                                     # noqa: BLE001
        msg = f"⚠️ Error verificando /best: {ex}"
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{canal}/messages",
        method="POST",
        headers={"Authorization": f"Bot {tok}", "User-Agent": "hermes-best/1.0",
                 "Content-Type": "application/json"},
        data=json.dumps({"content": msg}).encode())
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass
    print(msg)
    # y se saca solo del crontab
    import subprocess
    nueva = subprocess.run(["bash", "-lc", "crontab -l 2>/dev/null | grep -v verificar_best.py"],
                           capture_output=True, text=True).stdout
    if nueva.strip():
        subprocess.run(["crontab", "-"], input=nueva, text=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
