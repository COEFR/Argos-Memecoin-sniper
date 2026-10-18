#!/usr/bin/env python3
"""Guardia de memoria: avisa y corta jobs pesados antes de que el kernel mate el gateway.

Copia de referencia del script en producción (~/.hermes/scripts/guardia_memoria.py).
Se instala en el cron del usuario cada 3 minutos; lee el token del bot de ~/.hermes/.env.

Corre desde cron cada 3 min. Capas de defensa:
  1) MemAvailable < 600 MB  -> avisa por Telegram (con enfriamiento de 20 min).
  2) MemAvailable < 350 MB  -> mata los jobs de memecoins MAS VIEJOS (SIGTERM, luego SIGKILL)
     hasta recuperar margen, y avisa.
Nunca toca el gateway ni procesos de Hermes.

Uso: --cron (normal) | --dry-run (no actua) | --test (manda un aviso de prueba)
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
ENV = HOME / ".hermes/.env"
ESTADO = HOME / ".hermes/state/guardia_memoria.json"

UMBRAL_AVISO_MB = int(os.environ.get("GUARDIA_UMBRAL_AVISO_MB", 600))
UMBRAL_MATA_MB = int(os.environ.get("GUARDIA_UMBRAL_MATA_MB", 350))
MARGEN_OBJETIVO_MB = int(os.environ.get("GUARDIA_MARGEN_MB", 500))
ENFRIAMIENTO_S = 20 * 60
ESPERA_SIGTERM_S = 8
AVISAR = "--no-avisar" not in sys.argv  # solo para pruebas locales

# Scripts que pueden desmadrarse (todos corren desde cron y ya tienen flock/timeout).
OBJETIVOS = (
    "cohortes.py",
    "registro_calls.py",
    "canal_wallets.py",
    "ath_server.py",
    "control_test.py",
    "consumo.py",
)
PROTEGIDOS = ("hermes", "gateway", "guardia_memoria")


def leer_meminfo() -> dict[str, int]:
    datos: dict[str, int] = {}
    for linea in Path("/proc/meminfo").read_text().splitlines():
        clave, _, resto = linea.partition(":")
        if resto.strip().endswith("kB"):
            datos[clave] = int(resto.split()[0]) // 1024
    return datos


def leer_presion() -> str:
    try:
        for linea in Path("/proc/pressure/memory").read_text().splitlines():
            if linea.startswith("full"):
                campos = dict(p.split("=") for p in linea.split()[1:])
                return f"full avg10={campos.get('avg10', '?')}%"
    except OSError:
        pass
    return "presion: n/d"


def jobs_pesados() -> list[dict]:
    """Procesos de los scripts objetivo, del mas viejo al mas nuevo."""
    encontrados = []
    yo = os.getpid()
    for entrada in Path("/proc").iterdir():
        if not entrada.name.isdigit():
            continue
        pid = int(entrada.name)
        if pid == yo:
            continue
        try:
            cmd = (entrada / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            if "guardia_memoria" in cmd or not any(o in cmd for o in OBJETIVOS):
                continue
            if any(p in cmd for p in PROTEGIDOS):
                continue
            stat = (entrada / "stat").read_text()
            campos = stat[stat.rfind(")") + 2:].split()
            arranque = int(campos[19])  # starttime en ticks desde el boot
            try:
                rss_mb = int((entrada / "statm").read_text().split()[1]) * 4096 // (1024 * 1024)
            except (OSError, IndexError, ValueError):
                rss_mb = 0
            encontrados.append({"pid": pid, "cmd": cmd.strip()[:120], "arranque": arranque, "rss_mb": rss_mb})
        except (OSError, IndexError, ValueError):
            continue
    return sorted(encontrados, key=lambda j: j["arranque"])


def telegram(texto: str) -> bool:
    try:
        env = {}
        for linea in ENV.read_text().splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, v = linea.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        chat = env.get("TELEGRAM_HOME_CHANNEL", "")
        if not token or not chat:
            return False
        datos = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", datos, timeout=20
        ) as resp:
            return resp.status == 200
    except Exception as exc:  # aviso best-effort: nunca debe tumbar la guardia
        print(f"[guardia] fallo el aviso Telegram: {exc}")
        return False


def cargar_estado() -> dict:
    try:
        return json.loads(ESTADO.read_text())
    except (OSError, ValueError):
        return {}


def guardar_estado(estado: dict) -> None:
    try:
        ESTADO.parent.mkdir(parents=True, exist_ok=True)
        ESTADO.write_text(json.dumps(estado))
    except OSError:
        pass


def main() -> int:
    modo = sys.argv[1] if len(sys.argv) > 1 else "--cron"
    info = leer_meminfo()
    disp_mb = info.get("MemAvailable", 0)
    swap_mb = info.get("SwapFree", 0)
    presion = leer_presion()
    sello = time.strftime("%Y-%m-%d %H:%M:%S")

    if modo == "--test":
        ok = telegram(
            "🛡️ Guardia de memoria instalada.\n"
            f"Estado ahora: {disp_mb} MB disponibles, swap libre {swap_mb} MB, {presion}.\n"
            f"Avisa si bajas de {UMBRAL_AVISO_MB} MB y corta jobs si bajas de {UMBRAL_MATA_MB} MB."
        )
        print(f"[guardia] aviso de prueba enviado: {ok}")
        return 0 if ok else 1

    print(f"{sello} disp={disp_mb}MB swap_libre={swap_mb}MB {presion}")

    if disp_mb < UMBRAL_MATA_MB:
        matados = []
        for job in jobs_pesados():
            if disp_mb + sum(m["rss_mb"] for m in matados) >= MARGEN_OBJETIVO_MB:
                break
            try:
                print(f"{sello} RAM CRITICA → SIGTERM pid={job['pid']} ({job['rss_mb']}MB) {job['cmd']}")
                os.kill(job["pid"], signal.SIGTERM)
                matados.append(job)
            except ProcessLookupError:
                continue
            except PermissionError:
                print(f"{sello} sin permiso para matar pid={job['pid']}")
        time.sleep(ESPERA_SIGTERM_S)
        for job in matados:
            try:
                os.kill(job["pid"], signal.SIGKILL)
                print(f"{sello} seguia vivo → SIGKILL pid={job['pid']}")
            except (ProcessLookupError, PermissionError):
                pass
        if matados:
            liberado = sum(m["rss_mb"] for m in matados)
            if AVISAR:
                telegram(
                    f"🚨 RAM al límite en el VPS: {disp_mb} MB disponibles (swap libre {swap_mb} MB).\n"
                    f"Corté {len(matados)} job(s) de memecoins (~{liberado} MB): "
                    + ", ".join(f"pid {m['pid']} ({m['rss_mb']}MB)" for m in matados[:6])
                + "\nEl gateway quedó protegido (OOM score -500)."
            )
            return 0
        print(f"{sello} RAM critica pero no habia jobs pesados que cortar")
    elif disp_mb < UMBRAL_AVISO_MB:
        estado = cargar_estado()
        ahora = time.time()
        if ahora - float(estado.get("ultimo_aviso", 0)) > ENFRIAMIENTO_S:
            if AVISAR:
                telegram(
                    f"⚠️ Memoria baja en el VPS: {disp_mb} MB disponibles "
                    f"(swap libre {swap_mb} MB, {presion}).\n"
                    "Los jobs tienen flock+timeout: no se apilan. Si sigue bajando, la guardia los corta."
                )
            estado["ultimo_aviso"] = ahora
            guardar_estado(estado)
            print(f"{sello} aviso enviado (enfriamiento {ENFRIAMIENTO_S // 60} min)")
        else:
            print(f"{sello} memoria baja, aviso en enfriamiento")
    return 0


if __name__ == "__main__":
    sys.exit(main())
