#!/usr/bin/env python3
"""Escritura atómica de JSON, SEGURA ENTRE PROCESOS.

Por qué existe: los scripts del proyecto corren en crons que se solapan (patrulla cada 3 min, cosecha
cada 15, muestreo cada 5, comandos cada minuto). Con el clásico `tmp = path + ".tmp"` dos procesos
escriben el MISMO temporal, uno lo renombra y el otro muere con FileNotFoundError — y ese proceso
muerto es el que no alcanza a avisar (se midió el 08/10: la patrulla se caía a mitad de pasada y el
canal "seguir" quedaba mudo).

Solución: el temporal lleva el PID (nunca se pisan) y hay reintentos antes de rendirse.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time


@contextlib.contextmanager
def bloqueo(path: str, timeout: float = 10.0):
    """Bloqueo entre PROCESOS para los ciclos leer-modificar-escribir.

    Por qué: dos procesos del cron (patrulla cada 3 min, cosecha cada 15, muestreo cada 5) pueden leer
    el mismo archivo, agregar cada uno su parte y escribir: el segundo pisa al primero y **se pierde la
    entrada** (medido el 09/10: la call de $Dream se registró en el log pero no quedó en el archivo).
    La escritura atómica no alcanza: el problema es el ciclo completo, así que se serializa con flock.
    """
    ruta_lock = f"{path}.lock"
    try:
        fh = open(ruta_lock, "w")
    except Exception:
        yield
        return
    t0 = time.time()
    try:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() - t0 > timeout:
                    break
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass


def guardar(path: str, datos) -> bool:
    """Escribe `datos` como JSON en `path`, atómico y sin colisiones entre procesos."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(datos, fh)
    except Exception:
        return False
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return True
        except Exception:
            time.sleep(0.08)
    try:
        os.remove(tmp)
    except Exception:
        pass
    return False
