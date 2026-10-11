#!/bin/bash
# Levanta el VIGÍA EN VIVO (WebSocket sobre las wallets copiables) si no está corriendo.
# Lo llama el cron cada 5 minutos. Usa el python del venv de Hermes (que tiene `websockets`) y
# comprueba que el proceso siga vivo antes de guardar el PID — si no, queda un PID muerto y no se
# vuelve a levantar (esa trampa ya nos costó un día entero de vigilante caído).
cd "$(dirname "$0")" || exit 1
mkdir -p logs
PIDFILE=vigia.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0
fi
PY="$(dirname "$(dirname "$0")")/../hermes-agent/venv/bin/python"
if ! "$PY" -c "import websockets" 2>/dev/null; then
    echo "$(date '+%F %T') el intérprete $PY no tiene websockets" >> logs/vigilante_copiable.log
    exit 1
fi
setsid nohup "$PY" vigilante_copiable.py >> logs/vigilante_copiable.log 2>&1 &
nuevo=$!
sleep 5
if kill -0 "$nuevo" 2>/dev/null; then
    echo "$nuevo" > "$PIDFILE"
    echo "$(date '+%F %T') vigía en vivo arrancado (pid $nuevo)" >> logs/vigilante_copiable.log
else
    echo "$(date '+%F %T') el vigía arrancó y murió en 5 s" >> logs/vigilante_copiable.log
    rm -f "$PIDFILE"
    exit 1
fi
