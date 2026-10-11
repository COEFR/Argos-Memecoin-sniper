#!/bin/bash
# Registra el comando /best en Discord: el gateway tiene que reiniciarse para re-publicar los
# slash commands con el plugin nuevo.
#
# POR QUÉ DESDE EL CRON: hacerlo desde adentro del gateway mata el propio script (el SIGTERM se
# propaga a los hijos), y el sistema de Hermes lo bloquea justamente por eso. El cron del sistema
# corre fuera del proceso, así que el reinicio termina limpio.
LOG="$(dirname "$0")/logs/registrar_best.log"
echo "$(date '+%F %T') arranca el reinicio del gateway para publicar /best" >> "$LOG"
"${HOME}/.local/bin/hermes" gateway restart >> "$LOG" 2>&1
echo "$(date '+%F %T') listo (código $?)" >> "$LOG"
sleep 3
# se saca solo del crontab, cuidando de NO pisar el crontab si la lectura falla
nueva=$(crontab -l 2>/dev/null | grep -v registrar_best.sh)
if [ -n "$nueva" ]; then printf '%s\n' "$nueva" | crontab -; fi
