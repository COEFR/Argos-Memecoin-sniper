#!/bin/bash
# Levanta el VIGILANTE DE CALLS EN VIVO (WebSocket) si no está corriendo. Lo llama el cron cada 5 min.
#
# DOS TRAMPAS QUE YA NOS COSTARON TIEMPO (arregladas acá):
#  1) NADA de `pgrep -f vigilante_calls.py`: el script se encontraba a sí mismo (o a cualquier shell que
#     nombrara el archivo) y salía sin arrancar nada. Se usa un ARCHIVO DE PID.
#  2) El intérprete tiene que TENER `websockets`. El `python3` del sistema (3.14) no lo tiene: el
#     vigilante arrancaba, moría en un segundo con ModuleNotFoundError y el PID muerto quedaba guardado
#     (el keepalive creía que estaba vivo y no lo levantaba más). Ahora usa el python del venv de Hermes
#     (3.11, que sí lo tiene) y, después de arrancar, COMPRUEBA que siga vivo antes de guardar el PID.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
PIDFILE=vigilante.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0
fi

PY="$(dirname "$(dirname "$0")")/../hermes-agent/venv/bin/python"
[ -x "$PY" ] || PY=/usr/bin/python3
if ! "$PY" -c "import websockets" 2>/dev/null; then
    echo "$(date '+%F %T') el intérprete $PY no tiene websockets: no se arranca el vigilante" >> logs/vigilante_calls.log
    exit 1
fi

setsid nohup "$PY" vigilante_calls.py >> logs/vigilante_calls.log 2>&1 &
nuevo=$!
sleep 4
if kill -0 "$nuevo" 2>/dev/null; then
    echo "$nuevo" > "$PIDFILE"
    echo "$(date '+%F %T') vigilante arrancado (pid $nuevo, $PY)" >> logs/vigilante_calls.log
else
    echo "$(date '+%F %T') el vigilante arrancó y murió en 4 s: revisar el log" >> logs/vigilante_calls.log
    rm -f "$PIDFILE"
    exit 1
fi
