#!/usr/bin/env python3
"""humanizar_discord.py — canal de Discord que humaniza texto en castellano.

Lee los mensajes nuevos del canal configurado en DISCORD_HUMANIZAR_CHANNEL y, por cada
uno, devuelve: el puntaje del scanner, lo que encontró, y la versión limpia.

La edición la hace el propio agente (Hermes) con el skill humanizer-es; este script sólo
se ocupa del transporte (leer el canal, responder) y del scanner.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.expanduser("~/.hermes/skills/humanizer-es/scripts/scan_es.py")
ESTADO = os.path.join(BASE, "humanizar_vistos.json")
API = "https://discord.com/api/v10"
TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or ""
CANAL = os.environ.get("DISCORD_HUMANIZAR_CHANNEL") or ""
MIO = os.environ.get("DISCORD_BOT_ID") or ""
CANAL2 = os.environ.get("DISCORD_HUMANIZAR2_CHANNEL") or ""
# Los dos motores conviven: el 1 usa el agente (modelo) y el 2 es por reglas (port de Humanize-AI).
MOTOR2 = os.path.expanduser(os.path.join(BASE, "humanizar2.py"))


def log(m):
    print(f"{time.strftime('%H:%M:%S')}  {m}", flush=True)


def _req(metodo, ruta, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(API + ruta, data=datos, method=metodo)
    req.add_header("Authorization", f"Bot {TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "HermesHumanizer/1.0")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def _vistos():
    try:
        return set(json.load(open(ESTADO)))
    except Exception:
        return set()


def _guardar_vistos(s):
    try:
        json.dump(sorted(s)[-400:], open(ESTADO, "w"))
    except Exception:
        pass


ACADEMICAS = re.compile(
    r"(et al\.|\(20\d\d\)|seg[úu]n (los|el) (autores|estudio)|"
    r"investigaci[óo]n|metodolog[íi]a|hip[óo]tesis|marco te[óo]rico|"
    r"tesis|universidad|objetivo general|referencias bibliogr[áa]ficas)",
    re.IGNORECASE)


def escanear(texto, genero=None):
    """Devuelve (puntaje, texto del reporte). El puntaje se usa para el 'antes → después'."""
    try:
        args = [sys.executable, SKILL, "--texto", texto]
        if genero:
            args += ["--genero", genero]
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        salida = r.stdout.strip()
        m = re.search(r"LIMPIEZA:\s*(\d+)/100", salida)
        return (int(m.group(1)) if m else None), salida
    except Exception as e:
        return None, f"(no pude escanear: {e})"


def humanizar_reglas(texto):
    """Humanizador 2: reemplazo por diccionario, sin modelo."""
    import subprocess as sp
    try:
        # --sin-azar: sin agregar arranques/cierres inventados (en texto formal sobraban)
        r = sp.run([sys.executable, MOTOR2, "--texto", texto, "--sin-azar"],
                   capture_output=True, text=True, timeout=60)
        return r.stdout.strip()
    except Exception as e:
        return f"(no pude aplicar el humanizador 2: {e})"


def humanizar(entrada, reporte):
    """Le pide al agente la versión limpia, con el reporte del scanner como guía."""
    prompt = (
        "Actuá como el skill humanizer-es: sos editor de castellano y quitás las marcas de IA.\n"
        "Regla: borrá, no agregues. No inventes datos, no cambies el sentido, no agregues voz.\n\n"
        f"Reporte del scanner:\n{reporte}\n\n"
        f"Texto a limpiar:\n{entrada}\n\n"
        "Devolvé SOLO la versión limpia, sin comentarios ni explicaciones."
    )
    for exe in ("hermes", os.path.expanduser("~/.local/bin/hermes"),
                os.path.expanduser("~/.hermes/bin/hermes")):
        try:
            r = subprocess.run([exe, "-z", prompt], capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            continue
    return ""


def trocear(t, n=1900):
    return [t[i:i + n] for i in range(0, len(t), n)] or [""]


def main():
    # CANDADO: si ya hay una pasada corriendo (el agente puede tardar ~90 s), ésta se saltea.
    # Sin esto el cron y una corrida manual se pisaban y el canal recibía la respuesta DOS veces.
    import fcntl
    _lock = open("/tmp/humanizador.lock", "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("ya hay una pasada corriendo, salteo")
        return 0
    if not TOKEN or not CANAL:
        log("! falta DISCORD_BOT_TOKEN o DISCORD_HUMANIZAR_CHANNEL (van en ~/.hermes/.env)")
        return 1
    vistos = _vistos()
    # (canal, motor) — el 1 edita con el agente; el 2 reemplaza por reglas
    pares = [(CANAL, "1")] + ([(CANAL2, "2")] if CANAL2 else [])
    hubo = False
    for canal, motor in pares:
        try:
            msgs = _req("GET", f"/channels/{canal}/messages?limit=10")
        except urllib.error.HTTPError as e:
            log(f"! no pude leer el canal {motor}: HTTP {e.code}")
            continue
        n = _procesar(canal, motor, msgs, vistos)
        hubo = hubo or n
    _guardar_vistos(vistos)
    if not hubo:
        log("sin mensajes nuevos")
    return 0


def _procesar(canal, motor, msgs, vistos):
    nuevos = [m for m in reversed(msgs)
              if m.get("id") not in vistos
              and not m.get("author", {}).get("bot")
              and m.get("content", "").strip()]
    if not nuevos:
        return False
    hubo = False
    for m in nuevos:
        texto = m["content"].strip()
        log(f"→ humanizador {motor} · {len(texto)} caracteres de {m['author']['username']}")
        if motor == "2":
            limpio = humanizar_reglas(texto)
            texto_final = limpio
        else:
            # si parece texto de investigación, se escanea en modo académico (no marca
            # fórmulas legítimas de paper: "es importante destacar que" en una tesis no es jerga)
            gen = "academico" if ACADEMICAS.search(texto) else None
            nota_antes, reporte = escanear(texto, gen)
            limpio = humanizar(texto, reporte)
            nota_despues, _ = escanear(limpio, gen) if limpio else (None, "")
            # FORMATO: el texto limpio va primero y en texto plano (se copia de un toque);
            # el diagnóstico queda abajo, en una línea y en cursiva, para no competir con el texto.
            duras = reporte.count("⛔")
            blandos = reporte.count("• ")
            partes = []
            if nota_antes is not None and nota_despues is not None:
                partes.append(f"{nota_antes}/100 → **{nota_despues}/100**")
            elif nota_despues is not None:
                partes.append(f"**{nota_despues}/100**")
            if duras:
                partes.append(f"{duras} prohibición{'es' if duras > 1 else ''}")
            if blandos:
                partes.append(f"{blandos} marcador{'es' if blandos > 1 else ''}")
            pie = " · ".join(partes)
            texto_final = limpio + ("\n\n" + pie if pie else "")
        hubo = True
        if motor == "1" and not limpio:
            texto_final = "**🧹 Humanizador 1**\n(no pude editar: el agente no respondió)"
        # OJO: el mensaje se marca como visto SÓLO si la respuesta salió. Antes se marcaba antes de
        # mandarla, y un 403 transitorio dejaba el mensaje sin contestar para siempre.
        try:
            for i, trozo in enumerate(trocear(texto_final)):
                _req("POST", f"/channels/{canal}/messages",
                     {"content": trozo, "message_reference": {"message_id": m["id"]} if i == 0 else None})
            vistos.add(m["id"])
            log("  ✓ respondido")
        except Exception as e:
            log(f"  ! no pude responder (se reintenta en la próxima pasada): {e}")
    return hubo


if __name__ == "__main__":
    sys.exit(main())
