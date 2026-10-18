# Incidente de memoria — 14 al 18 de octubre de 2026

Registro del agotamiento de memoria del servidor que tumbó el gateway de mensajería, de la causa y de
los límites que se aplicaron para que no vuelva a ocurrir.

Servidor: VPS de 4 GB de RAM y 2 GB de swap, Ubuntu 26.04.

## Síntoma

El bot dejaba de responder. En algunos momentos el proceso seguía vivo y conectado a Telegram, pero sin
capacidad de procesar mensajes porque no había memoria. El propio sistema emitía avisos de presión de
memoria crítica antes de cada caída.

## Causa

Los jobs de cron del sistema no tenían control de concurrencia ni de duración. `cohortes.py`
(`--vigilar`, `--cosechar 20`, `--clasificar`) y `registro_calls.py` (`--muestrear`, `--evaluar`) se
lanzaban por intervalo fijo de 5, 15 y 30 minutos, pero varias corridas tardaban más que su propio
intervalo: `--cosechar 20` llegó a tardar 7 minutos en un solo token.

Con la memoria llena cada corrida se volvía más lenta, así que la siguiente encontraba a la anterior
todavía en ejecución y se apilaba. El ciclo se retroalimentaba: más procesos, menos memoria disponible,
corridas más lentas.

Cada proceso ocupa entre 46 y 115 MB de RSS. Sin límite, el 18 de octubre había 84 procesos simultáneos,
load average 42, 49 MB de memoria disponible y la swap al 100 %.

## Por qué el kernel mató al gateway y no a los jobs

`oom_score_adj` define a quién sacrifica el kernel primero cuando la memoria se agota. El gateway de
Hermes corría con **+200** y los jobs de cron con **0**. El kernel eligió al gateway.

systemd lo relanzaba, pero con la memoria todavía ocupada el proceso nuevo volvía a quedar sin recursos:
conectado, sin poder responder.

## Evidencia recogida

- **407 líneas `Killed`** en `logs/cron.log` entre el 14 y el 18 de octubre: 2 el día 14, 23 el 15, 121 el
  16, 149 el 17 y 112 el 18. El problema escaló durante cinco días.
- **9 invocaciones del OOM killer** visibles con `sudo dmesg`, matando procesos `python3` de cron
  (91–114 MB de RSS anónimo) y al proceso `hermes` con `oom_score_adj=200`.
- **Lifecycle ledger** del gateway: `suspected_oom=true`, `mem_available=50224 KiB`,
  `swap_used=2097020 KiB` en el último latido antes de la caída.
- **Línea de tiempo**: primera caída el 14 de octubre hacia las 18:30; reinicio manual a las 19:03; último
  latido el 15 de octubre a las 16:30; el 18 de octubre el vigilante reportó 265.143 segundos sin latido y
  el OOM killer volvió a matar el proceso a las 14:17.

El error `Failed to connect to user scope bus` fue consecuencia de la saturación: el systemd del usuario
dejó de responder. No hubo reinicio del servidor (28 días de uptime).

## Corrección aplicada (tres capas)

### 1. Límites por job en el cron

Cada job pesado se ejecuta con un lock propio, un tope de tiempo y un tope de espacio de direcciones:

```
/usr/bin/flock -n /tmp/hermes-<job>.lock /usr/bin/timeout <600|900> \
  /usr/bin/prlimit --as=1572864000 -- /usr/bin/python3 <script> <args>
```

- `flock -n` con un lock distinto por job: si la corrida anterior sigue viva, la nueva termina de
  inmediato con código 1 en lugar de apilarse.
- `timeout`: ninguna corrida pasa de 10 o 15 minutos.
- `prlimit --as=1572864000` (1,5 GiB): un job desbocado muere por sí mismo sin tocar el resto del sistema.

Cubre nueve jobs: `cohortes.py` (tres modos), `registro_calls.py` (dos modos), `canal_wallets.py`,
`ath_server.py`, `control_test.py --muestrear` y `consumo.py`. Las líneas completas están en
[`ops/crontab-con-limites.txt`](../ops/crontab-con-limites.txt).

### 2. Protección del gateway frente al OOM killer

Drop-in de systemd con `OOMScoreAdjust=-500`
([`ops/hermes-gateway-oom.conf`](../ops/hermes-gateway-oom.conf)). El gateway deja de ser el primer
candidato: bajo presión, el kernel sacrifica un job de cron y el bot sigue respondiendo.

### 3. Guardia de memoria

[`ops/guardia_memoria.py`](../ops/guardia_memoria.py), en el cron cada 3 minutos:

- con menos de 600 MB disponibles, avisa por Telegram (con enfriamiento de 20 minutos);
- con menos de 350 MB, corta los jobs más viejos (SIGTERM y luego SIGKILL) hasta recuperar margen;
- nunca toca el proceso del gateway.

## Verificación

- Segunda copia del mismo job mientras la primera corre: sale con **código 1**.
- `/proc/<pid>/limits` muestra `Max address space 1572864000` en los procesos de cron; el RSS medido es
  de 83 MB y 46 MB.
- La guardia registra su lectura cada 3 minutos en `~/.hermes/logs/guardia_memoria.log`.
- El gateway corre con `oom_score_adj=-500` y la memoria disponible se mantiene en 2,7–2,9 GB.

## Pendiente

- Reinicio del servidor para aplicar actualizaciones de kernel. Los límites de cron ya están activos y
  sobreviven al reinicio.
- El contador de créditos de RPC marca 31.695.836 sobre un ciclo de 1.000.000 en 11 días. No causó la
  caída, pero conviene revisarlo.
