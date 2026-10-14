#!/usr/bin/env python3
"""Lanzador de Argos para GitHub Actions.

La Action se dispara cada 5 minutos. Como cada corrida arranca sin memoria, acá se decide qué tareas
tocan según el reloj, con la misma cadencia que los cron del equipo. El estado se versiona en el repo.

Uso:  python3 argos_run.py            (corre lo que toca)
      python3 argos_run.py --probar   (sólo informa qué correría, sin ejecutar)
"""
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TOPE = 1200  # segundos por tarea (el workflow tiene 20 min; la vigilancia hoy tarda ~5-6)

# (script, argumentos, cada N turnos de 5 min, fase, hora fija UTC o None)
# La Action dispara en los minutos 0, 5, 10..., así que la cadencia se cuenta en turnos, no en minutos.
TAREAS = [
    ("canal_wallets.py",   ["--una-pasada", "--tx", "25", "--max-red", "3"], 1, 0, None),
    ("registro_calls.py",  ["--muestrear"], 1, 0, None),
    ("control_test.py",    ["--muestrear"], 1, 0, None),
    ("comando_discord.py", [], 1, 0, None),
    ("cohortes.py",        ["--vigilar"], 1, 0, None),
    ("cohortes.py",        ["--cosechar", "20"], 3, 1, None),
    ("cohortes.py",        ["--clasificar"], 6, 2, None),
    ("registro_calls.py",  ["--evaluar"], 6, 3, None),
    ("consumo.py",         ["--cron"], 12, 4, None),
    ("control_test.py",    ["--sembrar", "8"], 12, 5, None),
    ("tabla_calls.py",     ["--argos", "--discord"], None, None, 13),
    ("control_test.py",    ["--informe", "--discord"], None, None, 13),
]


def toca(minuto, cada, fase, hora_fija, hora):
    if hora_fija is not None:
        return hora == hora_fija and minuto < 5
    turno = (hora * 60 + minuto) // 5
    return turno % cada == fase


def main():
    probar = "--probar" in sys.argv
    ahora = time.gmtime()
    minuto, hora = ahora.tm_min, ahora.tm_hour
    pendientes = [t for t in TAREAS if toca(minuto, t[2] or 0, t[3], t[4], hora)]

    print("=== %s UTC · %d tarea(s) por correr ===" % (time.strftime("%Y-%m-%d %H:%M", ahora), len(pendientes)))
    if probar:
        for script, args, cada, fase, fija in pendientes:
            print("  (probaría) %s %s" % (script, " ".join(args)))
        return 0

    fallas, corridas = [], 0
    for script, args, cada, fase, fija in pendientes:
        cmd = [PY, os.path.join(AQUI, script)] + args
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=AQUI, capture_output=True, text=True, timeout=TOPE)
            dur = time.time() - t0
            estado = "ok" if r.returncode == 0 else "falla(%d)" % r.returncode
            if r.returncode != 0:
                fallas.append("%s %s" % (script, " ".join(args)))
                cola = (r.stderr or r.stdout).strip().splitlines()[-3:]
                for l in cola:
                    print("    ! %s" % l[:160])
        except subprocess.TimeoutExpired:
            estado, dur = "timeout", time.time() - t0
            fallas.append("%s (timeout)" % script)
        corridas += 1
        print("  %-22s %-9s %.1fs" % (script, estado, dur))

    # informe del ciclo, para la auditoría del sistema
    with open(os.path.join(AQUI, "estado_actions.txt"), "w") as fh:
        fh.write("%s UTC\n" % time.strftime("%Y-%m-%d %H:%M", ahora))
        fh.write("tareas corridas: %d\n" % corridas)
        fh.write("fallas: %s\n" % (", ".join(fallas) if fallas else "ninguna"))
    print("=== fin: %d corridas, %d fallas ===" % (corridas, len(fallas)))
    return 1 if fallas else 0


if __name__ == "__main__":
    raise SystemExit(main())
