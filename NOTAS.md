# memecoins / pump.fun — analizador + tracker de wallets

Creado 2026-10-06. **Solo informa y analiza**: no opera, no pide claves privadas, no toca fondos.
Cero tokens de LLM: todo son reglas explícitas y APIs públicas.

## Qué hay acá

| Archivo | Qué es |
|---|---|
| `pump_analizador.py` | Analiza un token de pump.fun (o un lote) y devuelve veredicto **VALE LA PENA / DUDOSO / NO** con puntaje 0-100, banderas rojas/amarillas/verdes y contexto. |
| `wallet_tracker.py` | Sigue wallets de Solana; avisa compras/ventas/transferencias y, **si la wallet compra, corre el analizador solo** y adjunta el veredicto. |
| `wallets.json` | Lista de wallets seguidas (dirección + etiqueta). |
| `tracker_state.json` | Firmas ya vistas (dedupe) + hora de la última pasada. |
| `tokens_cache.json` | Símbolos de tokens y mints ya analizados. |
| `logs/cron.log`, `logs/tracker.log` | Salida de los cron y traza del tracker. |
| `GUIA_MEMECOINS_2026.md` | La investigación completa (estrategias, bots, wallets, checks, evidencia). |

## Cron instalado (crontab del sistema, sobrevive reinicios)

```
*/3 * * * *  cd ~/.hermes/data/memecoins && /usr/bin/python3 wallet_tracker.py --una-pasada >> logs/cron.log 2>&1
0 12 * * *   cd ~/.hermes/data/memecoins && /usr/bin/python3 pump_analizador.py --movidos 60 --max-edad 24 --enviar-top 3 >> logs/cron.log 2>&1
```

- El tracker corre cada 3 min. **La primera pasada de cada wallet nueva es "línea base"**: anota sus
  últimas firmas y NO avisa, para no inundar el canal con el historial viejo.
- El radar manda a las **08:00 hora Bolivia** (el cron usa la hora local del equipo = UTC-4) el resumen
  de los lanzamientos de las últimas 24 h + el detalle de los 3 mejor puntuados.

### Diseño visual de los avisos (2026-10-06)
Lenguaje común en los tres canales: **jerarquía vertical** (título con emoji de acción → veredicto en
`###` → separador `────────` → bloques), **métricas en columnas** (`inline: True` de a tres) con cifras
en `code`, **banderas con `›`**, **enlaces al pie** (gráfico/token/transacción) en vez de texto crudo,
**imagen del token** en el embed y **timestamp** de Discord para la hora relativa.
- Radar de memecoins: autor «Radar de pump.fun» + 6 columnas (mcap, curva, edad, holders/top10,
  liquidez/volumen, compras/ventas) y debajo las banderas agrupadas.
- Wallet trackeada: `### etiqueta de la wallet` + columnas SOL/tokens/hora + bloques
  **🧪 ¿Buena compra?** y **🕵️ Info privilegiada** (el embed se pinta naranja si entró en el arranque).
- Auditoría: veredicto arriba, y después bloques **💰 Le mandaron SOL · 📤 Mandó SOL a · 🪙 Tokens
  operados · 🏆 Wallets útiles de esta red · ✅ Qué hacer**.
- Trampa: el emoji de la etiqueta ya viene en el texto → no volver a prefijarlo (salía «⚠️ ⚠️ DUDOSO»).

## Los tres canales (arquitectura final)

| Canal | Qué llega | Quién lo dispara |
|---|---|---|
| `#memecoins` | Radar de memecoins nuevas (puntuadas) + parte diario 08:00 Bolivia | `pump_analizador.py --vigilar` (*/10) y el parte diario |
| `#wallets-trackeadas` | **Cada movimiento de las wallets seguidas**, con el veredicto del token y el chequeo de información privilegiada | `wallet_tracker.py` (*/3) |
| `#auditoria-wallets` | Auditoría de la wallet que el usuario pega (rastro del dinero + wallets útiles de su red) | `canal_wallets.py` (*/5) |

Claves en `~/.hermes/.env`: `DISCORD_PUMP_CHANNEL`, `DISCORD_TRACKED_WALLETS_CHANNEL`,
`DISCORD_WALLETS_CHANNEL` (auditorías).

### El aviso de wallet trackeada trae tres bloques
1. Qué hizo (compró/vendió/movió SOL), cuánto y el link a Solscan.
2. **Veredicto del token** (analizador): ✅/⚠️/🚫 con puntaje y banderas.
3. **🕵️ ¿Información privilegiada?** con nivel **ALTA/MEDIA/BAJA/SIN SEÑALES** y las señales:
   · compró a los N segundos de creado el token (≤300 s = entró en el arranque → el embed se pinta naranja)
   · el **creador del token financió a esa wallet** (vínculo directo con el dev → ALTA)
   · % de posiciones que cobró **sin comprar** (recibir tokens = acceso al supply → ALTA)
   El nivel ALTA también aparece si recibe tokens sin comprar; con 2+ señales es MEDIA.

## Forense + clasificación de wallets (2026-10-06)

`wallet_forense.py <direccion>` devuelve, además del análisis: **las wallets útiles de la red**
(fondeadores, receptoras de la plata y hermanas), cada una **etiquetada** por
`clasificar_wallets.etiquetar()`:

### `forense_profunda.py <direccion>` (historial completo, con caché)

`wallet_forense.py` baja cada tx **dos veces** (flujos + tokens) y con wallets de 1.000+ tx se pasa
de 7 minutos y muere por timeout sin dejar resultado. `forense_profunda.py` baja cada tx **una sola
vez**, guarda lo derivado en `cache_tx_<prefijo>.json` y de ahí saca todo. **Releer el caché es
gratis** (segundos, sin RPC), así que se puede reanalizar o cambiar umbrales sin volver a bajar nada.
Opciones: `--max-tx N`, `--refrescar` (ignora el caché), `--json`. Guarda `forense_<prefijo>.json`.
El RPC público rinde **0,5-1,0 tx/s** con los crons corriendo encima: 1.200 tx ≈ 20-25 min → va en
background con aviso. `--tx`/`--max-tx` altos **no** se lanzan en foreground.

### `buscar_insiders.py <wallet>` — cazar info privilegiada (2026-10-06)

Busca las tres señales del insider: **entró en el arranque** (compró en los primeros N segundos),
**recibió tokens sin pagar** (con filtro de tamaño: ≥0,05 % del supply — sin eso entran 200 wallets
con migajas de spam) y **el creador del token la fondeó**.

**Leer el arranque = leer las tx del MINT, no las del pool.** El `pairAddress` de DexScreener es el
pool de PumpSwap, que arranca en la graduación: leerlo da "primeros post-graduación", no el
lanzamiento. Las primeras tx del mint sí son el lanzamiento (compras en la curva + reparto del bundle).

**Resultado medido (16 tokens del radar, 15 wallets auditadas): cero insiders rentables.** El patrón
dominante fue un **grupo coordinado de 13 wallets que compró `HOTBOT` en el arranque** (mismo token,
misma ventana, 4-7 SOL cada una, 3 posiciones cerradas, 0 % de aciertos, hold ~840 min y pérdidas de
−20 a −59 SOL cada una): entraron con info de primera mano y fueron **la liquidez de salida de otro**.
Ningún allocation grande gratis y ningún vínculo con creadores en la muestra.

**Trampa de ranking**: puntuar por *cantidad* de señales llena la muestra con los compradores tempranos
de un solo token (empates). Para buscar un insider **rentable** hay que rankear por **ganancia
realizada en el token del lanzamiento** (el que puso 1 SOL y sacó 20), no por contar señales.

### Buscar candidatas nuevas (2 rondas de descubrimiento) — 2026-10-06

Tres piezas encadenadas, todas con Helius (ver abajo) y en background:

| Script | Qué hace |
|---|---|
| `buscar_10.py` | Embudo: universo → compradores reales de pool → criba rápida (150 tx) → **top N re-evaluado con 1.200 tx** y nota 0-10. `--ventana viejas` lee el **arranque** del pool (los primeros compradores, donde está el edge); `--ventana nuevas` las últimas. |
| `red_wallets.py` | Contrapartes de una wallet, filtrando lo que no es wallet (owner ≠ System Program = pools/PDAs) y puntuadas 0-10. |
| `evaluar_wallet.py` | Evaluación profunda paginada de una wallet (usa la tubería de `forense_profunda.py`). |

**Resultado medido (75 wallets auditadas, 3 rondas): cero con ventaja copiable.** El patrón: los que
ganan son **bots** (hold 1-9 min) con el resultado colgando de 1-2 trades; los de **hold humano pierden**
(rugs de −90 % o sangría). Consistente con la tasa base del proyecto (~6 % de traders rentables).
Universo útil: los memecoins que **ya pasó el radar** (score ≥70) del `logs/cron.log` — la lista cruda
de pump.fun trae stablecoins y activos tokenizados (USDF, USDP, DOTF) cuyos compradores son bots de
arbitraje. Y `--ventana viejas` trajo **92 compradores vs 45** de la ventana reciente: el arranque
concentra a los que entran temprano.

**Filtro anti-suerte que hay que exigir siempre**: `pnl_sin_top3_sol` (PnL sin los 3 mejores trades)
tiene que seguir **positivo**. Una wallet sacó 9/10 con +38,94 SOL y sin sus 3 mejores caía a
**−3,59 SOL**: era lotería. Está integrado en `puntaje_10` (junto con concentración <50 %).

**Cupo Helius**: cada ronda de este tamaño gasta ~10-20 % del 1 M mensual (una ronda = 17 k-20 k tx ≈
170 k-200 k créditos; `getTransaction` = 10 créditos). Medir antes de lanzar y avisar al usuario.

## Infraestructura de insiders (investigación propia 2026-10-07, sin listas públicas)

Cinco rutas sobre tokens del radar que despegaron, y el mapa que salió:

- **Bundle del lanzamiento**: NO hay (una sola compra de arranque, <1 % del supply).
- **Receptores sin pagar**: miles por token **y son cáscaras** (0 operaciones, 0,002-0,01 SOL).
- **Cruce «recibió gratis → vendió»**: vacío.
- **Creadores**: wallets frescas desechables (`creatorTokens: 0`).
- **Distribuidores**: un remitente por token que reparte a **300-380 wallets**, con **7,381 SOL
  idénticos** entre lanzadores distintos (`CiGGtPSvs8UR6A…`, `7dKJmcLEfnNKhQ…`, `6jvFjWGZnRfXR3…`)
  → **un solo operador con servicio de lanzamiento**. Ese patrón es **alerta roja** (ver el skill
  `memecoins-pumpfun`): reparto masivo = no hay demanda orgánica.
- **Dónde sí hay insider**: en el **clúster que el creador fondea**. De 7 wallets fondeadas por devs,
  **1 real** (`5Dx8dhAAmA1BqP5Us7gu6vh5YgwLeWJENmJY4GBuwyLA`: recibió tokens sin comprar y cobró
  **+81,75 SOL**, vaciada después) y 6 cáscaras.

Scripts nuevos: `cazar_anillos.py` (co-firing + union-find), `buscar_insiders.py` (arranque + airdrops),
`insider_token.py` (bundle por slot de creación + % del supply + clúster de fondeo), `insiders_cruce.py`
(cruce lanzamiento↔pico), `cazar_devs.py` (creadores + su clúster), `cazar_distribuidores.py` (quién
reparte), `cazar_privilegiados.py`, `verificar_cluster_devs.py`. Dossier de técnicas con fuentes:
`TECNICAS_INSIDERS.md`. Mapa + lo que no se pudo: `INFRAESTRUCTURA_INSIDERS.txt`.

**Estado del cupo**: la jornada consumió ~92 % del 1 M mensual de Helius (una pasada de este tamaño
cuesta 100-200 k créditos). Antes de lanzar otra investigación grande, avisar y pedir OK.

### ⚡ Detector de RÁFAGAS / calls (2026-10-07) — integrado al radar de #memecoins

`calls_detector.py` + gancho en `pump_analizador.vigilar()` y `embed_de()`.

**Qué detecta**: en el pool de un token, ≥3 wallets DISTINTAS comprando dentro de ≤90 s poniendo
≥0,5 SOL cada una = compra coordinada (call de un grupo de alpha tipo Pastel / Heaven or Hell, o
bundle). La unidad de señal es **el token, no la wallet**: los grupos son multitudes y usan wallets
nuevas cada vez (medido: 42 ráfagas en 28 tokens, 162 wallets implicadas y **0 repetidas**).

**Cómo se comporta ahora** (cron del radar, cada 10 min):
1. El radar analiza los tokens que pasan el prefiltro y avisa los que pasan el umbral.
2. Además, para hasta **2 tokens jóvenes** (edad ≤2 h, mcap ≥20k) por pasada, lee las **25 últimas tx
   del pool** y busca la ráfaga.
3. Si hay ráfaga → avisa **aunque el puntaje no llegue al umbral**, con el título
   «⚡ RÁFAGA · COMPRA COORDINADA DETECTADA» y el bloque en el embed:
   `⚡ CALL DETECTADA — N wallets entraron en X s · Y SOL en total · arrancó HH:MM UTC`.
4. Si un token que ya se iba a avisar tiene ráfaga, el bloque ⚡ se agrega al aviso normal.

**Tope de gasto**: `calls_detector.TOPE_DIARIO = 25.000` créditos/día (≈95 chequeos; cada chequeo =
10 créditos por tx leída, ~260 por token). El contador vive en `presupuesto_calls.json` y **se corta
solo** al agotarse — así el detector no se come el cupo de Helius sin avisar.

**Verificado**: alerta real enviada al canal `#memecoins` (1557008761292329072) y **leída de vuelta**
por API (título, veredicto e imagen resuelta como `cdn.discordapp.com/attachments/...`). En la prueba
suelta el detector encontró una ráfaga real (3 wallets en el mismo segundo, 3,01 SOL).

**Modo actual del radar (por pedido del usuario, 07/10): `--solo-rafagas`.**
El canal `#memecoins` recibe **únicamente las ⚡ CALL**. El radar sigue analizando por dentro (hace
falta para evaluar las candidatas a ráfaga y para el candado de tamaño) pero **no publica tokens
normales**: el usuario se quejó del volumen (125 avisos en un día, 87 % con puntaje 70-79). El
**parte diario de las 08:00 quedó comentado** en el crontab (se reactiva quitando el `#`).
Para volver a los avisos normales: quitar `--solo-rafagas` de la línea del cron.

Además se agregaron dos recortes de ruido que siguen valiendo en cualquier modo:
`max_alertas` default = **1 por pasada** (antes 3) y el candado `tamano_ok()`: para avisar un token
normal exige **mcap ≥25k** y (**liq ≥5k o curva ≥30 %**) — los de $3-4k puntuaban alto y no valían nada.

**Mejoras aplicadas al detector (07/10, sacadas de GitHub/Reddit):**
1. **Re-chequeo** de tokens de ≤2 h en cada pasada (antes se chequeaba UNA sola vez y la call que
   pasaba después se perdía — era la causa principal de que llegaran pocas).
2. Ventana 90 s → **150 s**; mínimo por wallet 0,5 → **0,3 SOL**; se leen **40 tx** del pool.
3. **Señales nuevas en el aviso**: cuántas compras cayeron en el **mismo slot** (misma fracción de
   segundo = bundle, se titula «🚨 BUNDLE»), cuántas **compras hubo en la ventana**, y si las carteras
   son **NUEVAS** (sin historial) o **ya conocidas** por nosotros.
4. **Candado de edad**: no se avisa una call de un token de <10 min (ventana de snipers).
Medido al aplicarlo: ráfagas de 5-7 wallets en 5-35 s, con 4-6 compras en el **mismo slot**, y las
carteras **ni frescas ni conocidas** (carteras con historia, no bundles de wallets nuevas) → refuerza
que la señal es la RÁFAGA del token, no la wallet.
Fuentes: baronguyen001/wallet-cluster-detector (cluster de carteras puntuadas), RachitSrivastava12/dec-clust,
Karma-234/sol-whisperer (spike de volumen), Helius LaserStream (stream de Pump AMM, próximo paso),
DeFade whitepaper (fresh wallet flagging), agiprolabs/sybil-detection (co-trade por slot).

### Vigilante de calls EN VIVO (WebSocket) — 2026-10-07

`vigilante_calls.py` + `arrancar_vigilante.sh` (cron cada 5 min lo levanta si se cayó; usa
**archivo de PID** — con `pgrep -f vigilante_calls.py` el script se encontraba a sí mismo y no
arrancaba nada).

Cómo funciona: se suscribe por **WebSocket de Helius** (`logsSubscribe mentions:[mint]`, incluido en
el plan) a los tokens jóvenes de `tokens_jovenes.json` (lo escribe el radar). Cuando un token muestra
**≥8 trades en 60 s**, baja las últimas 6 tx, cuenta wallets distintas / SOL / compras en el mismo
slot / carteras nuevas vs conocidas, y si hay ≥3 wallets con ≥0,3 SOL **avisa en segundos** con el
mismo formato del radar. También se puede sondear: `calls_detector.py` desde el radar (cada 10 min).

**Frenos de gasto medidos (imprescindibles):** cada evaluación ≈ 60 créditos; el vigilante evalúa
**como máximo 8 veces por hora en total** (`TOPE_EVAL_HORA`) y 12.000 créditos/día; el detector del
radar tiene su propio tope de 12.000/día. Sin el tope global, un token caliente se evaluaba en CADA
trade: medido **10.200 créditos en 3 minutos** (≈5.600/min). Con los frenos: **420 créditos** por
prueba de 100 s.

**Presupuesto real del sistema (a vigilar):** vigilante ≤12 k/día + detector del radar ~21 k/día
(medido) + **tracker de wallets ~24 k/día** (5 wallets cada 3 min) ≈ **57 k/día ≈ 1,7 M/mes**, por
encima del 1 M gratis. Para entrar en el plan gratis hay que bajar el tracker a cada 6-10 min o
pasar al plan pago (~49 USD).

### Imagen de la señal: la cadena tenía un agujero (arreglado 07/10)

Síntoma: algunas señales salían **sin imagen**. Causa medida: cuando DexScreener no trae `imageUrl`,
`imagen_de()` caía a la imagen de pump.fun servida por **`ipfs.io`, que desde este equipo responde 403**
(bloquea IPs de datacenter) y **no había respaldo**: la descarga fallaba y el embed salía sin foto.

Arreglado en tres partes: (1) `_variantes_ipfs()` reescribe la MISMA imagen por los gateways que sí
responden (`gateway.pinata.cloud`, `ipfs.filebase.io`, `4everland.io`); (2) `alertar_token` arma una
**lista de candidatas** que incluye `https://images.pump.fun/coin-image/<mint>?variant=600x600` y la
tarjeta `pump.fun/api/og/coin/<mint>` como respaldos; (3) `descargar_imagen` acepta lista y prueba todo.
Verificado con las 3 señales que habían salido sin foto (23 KB / 70 KB / 166 KB) y un envío real.

### RPC público: qué host sirve para qué (medido 2026-10-06, a golpes)

**Con Helius no hace falta nada de esto**: hay `HELIUS_API_KEY` en `~/.hermes/.env` (capa gratis: 1M
créditos/mes y 10 req/s; cada `getTransaction` cuesta 10 créditos → ~100.000 tx por mes) y
`wallet_tracker.rpc()` lo toma solo como **host principal** en los tres órdenes (`RPCS`, `RPCS_FIRMAS`,
`RPCS_TX`). Medido con la key: paginado de 1.000 firmas en 0,08 s y **15/15 tx leídas en 0,77 s**.
Verificar siempre con una llamada real después de cambiar la key.

**Trampa grande, la que arruinaba las auditorías: las transacciones v1.** Desde 2026 la red trae
tx **versión 1** y pedirlas con `maxSupportedTransactionVersion: 0` **no da error de red**: el nodo
devuelve un error por tx (`Transaction version (1) is not supported`) o `null` según el host, y el
historial de una wallet activa se ve **vacío**. Medido sobre la misma docena: con 0 → **1 de 14**
con datos; con 1 → **14 de 14**. Está centralizado en `WT.MAX_VERSION = 1` y hay que usarlo en
**todos** los `getTransaction`.
Segundo efecto de lo mismo: en las tx versionadas parte de las cuentas vienen en
`meta.loadedAddresses` (lookup tables), así que la lista de cuentas **no** es `message.accountKeys`:
si no se concatenan estáticas + writable + readonly, los índices se desalinean con `pre/postBalances`
y el delta que se lee es de otra cuenta. Usar `WT.cuentas_tx(tx)`.

| Host (sin key) | `getSignaturesForAddress limit=1000` | `getTransaction` bajo carga |
|---|---|---|
| `api.mainnet-beta.solana.com` | **1.000 firmas** (el único que pagina completo) | rápido, pero da **429** tras miles de llamadas |
| `rpc.solanatracker.io/public` | **112 firmas** (corta la lista) | aguanta (~0,26 s/tx) |
| `solana-rpc.publicnode.com` | 565 firmas | sirve, con límite |
| `solana.api.onfinality.io/public` | 429 | 429 |

Por eso `wallet_tracker.rpc()` **elige el orden de hosts según el método**: las firmas van siempre por
el oficial y `getTransaction` prefiere solanatracker (`RPCS_FIRMAS` / `RPCS_TX`). Trampa que casi
arruina un informe: paginar contra un host que corta la lista hace que una wallet de **9.000 tx**
parezca tener **112** y que todo el historial se vea como «hoy».

**El otro corte es la cobertura**: los nodos gratis sirven las tx **recientes** y devuelven `null`
para las viejas (y `null` no es error: hay que insistir contra los otros hosts). Con throttling, una
corrida de 1.172 tx juntó **195**. Por eso `descargar()` va en dos pasadas (rápida y reintento), guarda
cada 200 tx y `--desde-cache` permite analizar lo parcial sin tocar el RPC.

**Trampa del PnL con cobertura parcial**: si faltan las compras, las ventas quedan como «cobró sin
haber comprado» y el PnL crudo sale **inflado** (medido: +153 SOL que en realidad eran ventas sin
costo visible). Para juzgar hay que mirar sólo las posiciones con **compra Y venta vistas**: en la
misma wallet esas 18 daban **−3,8 SOL, win rate 28%**, mientras el total crudo decía +153 SOL. Nunca
reportar el PnL crudo sin separar «completas» de «sólo venta».

### `red_wallets.py <direccion>` — wallets conectadas puntuadas 0-10

Saca las contrapartes de los flujos (usa el caché de `forense_profunda.py` si existe: cero RPC) y
**descarta lo que no es una wallet de persona**: cuentas que no son del System Program (vaults de
pools en wSOL, PDAs del programa de fees, cuentas de token), las que no existen y las que tienen
<0,02 SOL. Después audita cada una con `auditor_wallets` y le pone **nota 0-10** con los filtros del
proyecto (gana +2 · ROI>0 +1,5 · ≥30 cerradas +1,5 · win rate 45-80 +1 · concentración <50% +1 ·
t≥1,645 +1 · ≤20% sin compra +1 · hold ≥2 min “copiable” +1 · fondeo fresco −1).

Medido sobre una wallet de 3.000 tx: de **633 contrapartes con SOL sólo 10 eran wallets reales** (el
94% es infraestructura). Y las que "ganan" suelen ser el patrón trampa: **win rate 100% con 5-15
posiciones y 60-85% sin compra** = bundle, no trader. Regla: **no cargar al tracker nada por debajo
de 8/10**, y desconfiar de win rate >85% con muestra chica.

**Firma de clúster que aparece sola**: varias wallets con el mismo **prefijo Y sufijo** de vanidad
(ej. `A6zD……Y3a` en tres, dos de ellas pagando fees) = set generado por el mismo operador. Un prefijo
de 4 caracteres compartido ya es 1 en ~11M por azar: si coincide además el sufijo, no es casualidad.



### Patrón «wallet de granja» (medido 2026-10-06 sobre `7UBjMaYsE3tK…`)

Señales juntas (ninguna sola alcanza): `getAccountInfo` = **null** (la cuenta no existe, 0 lamports),
**delta de lamports exacto = 0** en todo el historial (no entra ni sale SOL), **nunca es el pagador
del fee** (keys[0] siempre es un bot), **45% de las tx fallan con `InsufficientFundsForRent`**,
y **200-300 cuentas de token con 100+ saldos** (recibe memecoins regaladas y **nunca vende**).
El clasificador del proyecto la deja en «❓ SIN DATOS» porque **no hay swaps**: eso no es «no sé», es
«no es un trader». Traducción para el informe: **no sirve para copiar**, no se carga al tracker.
Lo útil de esa red no es la wallet sino quién la mueve: el **distribuidor** que le manda tokens y
paga los fees, y las wallets **consolidadoras** (mismo perfil, 200+ cuentas de token) que reciben lo
suyo. Ojo con `getSignaturesForAddress` paginado: el parámetro `before` va **dentro del mismo dict**
que `limit` (`[direccion, {"limit":1000,"before":sig}]`); si se pasan dos dicts separados el RPC
falla en silencio y parece que la wallet tuviera solo 1.000 tx (medido: tenía 10.000+).

| Etiqueta | Criterio | Para qué sirve |
|---|---|---|
| 🚨 INSIDER | ≥40% de posiciones cerradas sin compra previa (o ≥25% con win rate ≥90%) | Vigilar cuándo suelta |
| ⭐ CANDIDATA | Compró con su propia plata, PnL positivo, hold humano | Candidata a seguir (muestra chica = a verificar) |
| 🤖 BOT/MEV | hold mediano <2 min con ≥3 operaciones | No copiable |
| 📤 PASARELA | Muchos fondeadores/destinatarios y casi sin tokens | La que opera está en otro lado |
| 📉 PIERDE | Compró de verdad y perdió | Lista negra |

- El informe cierra con **«✅ QUÉ HACER»**: SEGUIR ⭐ (la mejor candidata), VIGILAR 🚨 (insiders) o
  EVITAR (perdedores/bots).
- `--cargar N` mete las N más útiles al tracker ya etiquetadas (el aviso muestra el emoji y el tipo).
- `--max-red N` limita cuántas wallets de la red se clasifican (timepo de RPC); `--tx N` la profundidad.
- **Se descartan los hubs/CEX** como wallets a seguir (`es_hub()`: 1.000 firmas en la primera página) —
  es el punto que la investigación marca como el error nº 1 al agrupar wallets.
- Medido: con ventanas cortas (25-70 tx) la mayoría de las hermanas sale «❓ SIN DATOS» (0 posiciones
  cerradas). Para un veredicto serio hace falta `--tx 300` o más por wallet.

## Formato de los avisos (Discord, con imagen)

- **Solo Discord** (pedido del usuario): las funciones `enviar()` de los dos scripts ya NO mandan a
  Telegram por defecto. `alertar_token()` (radar/alertador) y `alertar_evento()` (tracker) usan
  **embeds**: barra de color por veredicto (verde/ámbar/rojo), campos con mcap, curva, edad, holders,
  liquidez y compras/ventas, y **la imagen del memecoin** adjunta y mostrada dentro del embed.
- **Cadena de imágenes** (`imagen_de()`): CDN de DexScreener (`info.imageUrl`) → `image_uri` de
  pump.fun (por gateways IPFS) → **`https://images.pump.fun/coin-image/<mint>?variant=600x600`**
  (funciona para casi cualquier token de pump.fun).
- **Gateways IPFS medidos desde este equipo**: `ipfs.io`, `dweb.link`, `w3s.link` y `nftstorage.link`
  dan **403** (bloquean IP de datacenter). Sirven `gateway.pinata.cloud`, `ipfs.filebase.io` y
  `4everland.io` (200 con la misma imagen) — están primero en la lista.
- La CDN de pump.fun devuelve una **imagen genérica** cuando el token no tiene arte: se descarta
  comparando el md5 contra `PLACEHOLDER_MD5` (si no, mandás un dibujo que no es el memecoin).
- Trampa de implementación: el `payload_json` tiene que armarse **después** de reemplazar la URL de
  la imagen por `attachment://<nombre real del archivo>`; si no, Discord no encuentra el adjunto y
  **descarta la imagen en silencio** (el mensaje sale igual, sin foto).

## Entrega (verificada con lectura del canal)

- Alertas por Telegram (Bot API) **y** Discord.
- **Canal dedicado: `#memecoins` en el servidor (ID `1557008761292329072`)**, creado el 2026-10-06 y
  apuntado en `~/.hermes/.env` como `DISCORD_WALLETS_CHANNEL` y `DISCORD_PUMP_CHANNEL`.
- Cadena de fallback si esas claves faltan: `DISCORD_WALLETS_CHANNEL` → `DISCORD_PUMP_CHANNEL` →
  `DISCORD_SIGNALS_CHANNEL` (era donde caía antes: `#señales-rsi-premium`) → `DISCORD_HOME_CHANNEL`.
  Telegram: `TELEGRAM_WALLETS_CHANNEL` → `TELEGRAM_HOME_CHANNEL`.
- Para mover el canal: cambiar esa clave en `~/.hermes/.env` (no hay que tocar código). El bot de este
  equipo **sí tiene permiso de `MANAGE_CHANNELS`** en el servidor: puede crear canales él mismo.

## Cómo se usa

```bash
cd ~/.hermes/data/memecoins
python3 pump_analizador.py --mint <mint>          # un token
python3 pump_analizador.py --scan 40 --top 8      # últimos lanzamientos (<24 h)
python3 pump_analizador.py --movidos 20           # los que están operando ahora
python3 pump_analizador.py --scan 60 --min-edad 15 --max-edad 24   # descarta la ventana de snipers (<15 min)
python3 pump_analizador.py --mint <mint> --json   # salida para scripts
python3 wallet_tracker.py --descubrir 25          # propone wallets desde tokens que corrieron
python3 wallet_tracker.py --agregar <dir> "etiqueta"
python3 wallet_tracker.py --listar | --quitar <dir>
python3 wallet_tracker.py --una-pasada --dry-run  # prueba sin enviar ni guardar
```

## Qué mide el analizador (y con qué fuente)

| Dato | Fuente | Regla aplicada |
|---|---|---|
| mint/freeze authority, metadata mutable, Token-2022, transfer fee | RugCheck | authority activa = **descalificante** |
| top-10 holders | RugCheck (**descontando curva/pool/creador**) | >35% rojo · 20-35% amarillo · <20% verde |
| % del creador | RugCheck `creatorBalance`/supply | >20% rojo |
| redes de insiders y nº de wallets | RugCheck `insiderNetworks` | penaliza según tamaño del grupo |
| tokens previos del creador | RugCheck `creatorTokens` | >3 = lanzador serial |
| curva de fondeo | pump.fun (`real_sol_reserves`/85 SOL) | al 100% graduó a PumpSwap |
| mcap, social, comentarios, edad | pump.fun v3 | comentarios y redes como señal de comunidad |
| liquidez, volumen, compras/ventas, cambios | DexScreener | volumen 24 h < 500 USD = **descalificante** (no hay a quién venderle) |
| boosts activos | DexScreener | promoción pagada = amarillo |

Puntaje: **≥70 ✅ VALE LA PENA · 45-69 ⚠️ DUDOSO · <45 🚫 NO**. Cualquier bandera roja fuerza **NO**.

## Cosas verificadas contra la realidad (no de memoria)

- **Graduación de la curva: ~85 SOL recaudados** (equivale a ~69k USD con SOL a 120-200). Medido
  sobre 250+ tokens reales del API.
- **Fees oficiales** (pump.fun/docs/fees): crear 0 SOL · graduar 0,015 SOL · **en la curva 1,25% por
  lado** (creator 0,30 + protocol 0,95). Post-graduación baja por tramos de mcap (1,25% → 0,325%).
  Ida y vuelta en la curva ≈ **2,5%**: con eso, un +2,5% apenas empata.
- **Los "top holders" de RugCheck incluyen la propia curva de pump.fun** (aparece con 94-100% del
  supply). Sin descontarla, TODO token nuevo parece un rug. Se excluye por `knownAccounts`
  (tipos AMM/LOCKER/CREATOR/POOL/BURN).
- **Patrón real observado**: 6 wallets con el *mismo porcentaje* (0,9878660849676077%) en un token
  → clúster repartido de un solo dueño. Es la firma que la investigación describía.
- **Discord devuelve 403** al User-Agent por defecto de urllib: hay que mandar `User-Agent` propio.
- **Ojo con el intérprete**: `python3` en este equipo es **3.11** y `/usr/bin/python3` (el que usa el
  cron) es **3.14**. Los f-string con `\` adentro de las llaves (permitidos en 3.12+) revientan en
  3.11 → hay que compilar con los dos antes de dar algo por bueno.
- **RPC público**: `api.mainnet-beta.solana.com` responde `getSignaturesForAddress` y
  `getTransaction`. publicnode/ankr/drpc/onfinality **no** sirven (rate limit o plan pago).
- El clasificador de swaps se probó sobre 12 transacciones de swap reales: 6 ventas y 7 compras
  detectadas correctamente con sus contrapartes.

## Límites honestos

1. **Las wallets candidatas NO están verificadas**: `--descubrir` lista wallets que aparecen como
   holders grandes en varios tokens que corrieron. Eso **no** es lo mismo que ser rentable — puede
   ser infraestructura o un clúster que solo vende. Hay que mirar PnL/win rate (GMGN, Cielo) antes
   de seguirla en serio.
2. El analizador **no** simula la venta (honeypot real), no mide el historial de PnL del dev más
   allá de cuántos tokens lanzó, y no sabe si el "smart money" está entrando o saliendo.
3. El tracker detecta movimientos **con retraso** (polling cada 3 min): no sirve para copiar, sirve
   para enterarse. Con 3 min de atraso, en memecoins ya cambió todo.
4. La tasa base sigue siendo la del estudio: **~76% de los tokens nuevos de Solana son rug** y solo
   el **4,55%** de los de pump.fun sobrevive 90 días. Ninguna herramienta cambia eso.

## Pendiente

- Verificar el PnL real de las 3 wallets candidatas (o cargar las propias).
- Decidir si el canal se muda a uno dedicado (crear el canal en Discord y pasar el ID → se agrega
  `DISCORD_WALLETS_CHANNEL` al `.env`; el agente no puede crear canales).
- Opcional: sumar Helius (API key gratis) para pasar de polling a websocket y bajar el retraso.


## Trampas de operación (medidas, no supuestas)

- **El keepalive del vigilante en vivo necesita el python correcto.** El `python3` del sistema (3.14)
  **no tiene `websockets`**: el vigilante arrancaba, moría en un segundo con ModuleNotFoundError y
  dejaba el PID guardado, así que el keepalive lo daba por vivo y no lo levantaba nunca más (hoy estuvo
  caído sin que nadie lo notara). El script ahora usa `~/.hermes/hermes-agent/venv/bin/python` (3.11),
  comprueba `import websockets` ANTES de arrancar y verifica que el proceso siga vivo a los 4 s antes de
  guardar el PID.
- **Dos intérpretes distintos en la misma máquina**: los scripts del proyecto corren con
  `/usr/bin/python3` (3.11) y funcionan; pero `python3` a secas en el cron resuelve a 3.14. Si un script
  nuevo necesita dependencias, usar la ruta completa.
- **El costo del cupo se mudó a la patrulla de Argos**: con 153 wallets vigiladas cada 3 min, leer 6
  firmas por wallet son ~520k créditos/día (estimados), más que el radar. Si el cupo aprieta, el primer
  tornillo es la patrulla (frecuencia, cantidad de firmas o sólo wallets con registro positivo).


## Las 4 trampas que dejaban el canal de "seguir" mudo (medidas el 08/10)

Se diagnosticó a fondo por qué `#🟢-seguir` no recibía nada aunque el sistema sí detectaba compras
coordinadas (105-112 tokens con co-compra por pasada). Eran cuatro fallas de PLOMERÍA, no de detección:

1. **`for w, c in list(indice.items())[:40]`** — la patrulla leía sólo las PRIMERAS 40 wallets de las
   209 vigiladas (y siempre las mismas): las wallets de los grupos 🟢 nunca se revisaban, así que sus
   compras no entraban al búfer y no había nada que avisar. **Es la causa principal.** Se sacó el tope y
   se bajaron las firmas por wallet de 6 a 4 para compensar el cupo.
2. **El mínimo de miembros**: se exigían 2 wallets del mismo grupo en el mismo token. En los grupos 🟢
   (casi todos dúos) eso casi nunca pasa. Ahora un grupo 🟢 avisa con **UN** miembro ("entrada
   individual"), y el resto sigue con 2 (chicos) o 3 (grandes).
3. **El archivo temporal de nombre fijo** (`path + ".tmp"`), en 13 lugares: los crons se solapan
   (patrulla 3 min, cosecha 15, muestreo 5) y uno renombraba el temporal del otro → el proceso moría a
   mitad de pasada. Se centralizó en `jsonio.guardar()` (temporal con PID + reintentos).
4. **El tope por hora en ráfaga**: 6/hora por canal se agotaba en los primeros minutos y dejaba el canal
   mudo el resto de la hora. Ahora: hasta 10/hora por canal con **90 s mínimo entre avisos** (goteo).

**Lección de método:** verificar el FINAL del circuito (el mensaje que llega al canal), no el medio
(el log que dice "COORDINADO"). El sistema detectaba bien y el aviso no salía.

## 2026-10-18 · Límites de cron tras el OOM (incidente 14-18 oct)

- Los 9 jobs pesados corren con `flock -n` (lock propio por job) + `timeout` (600-900 s) + `prlimit --as=1572864000`.
- Gateway protegido con `OOMScoreAdjust=-500` (drop-in en `~/.config/systemd/user/hermes-gateway.service.d/oom.conf`).
  Antes corría en `+200` y el kernel lo mataba antes que a los jobs.
- Guardia `~/.hermes/scripts/guardia_memoria.py` cada 3 min: avisa <600 MB, corta jobs <350 MB.
- Backups del crontab: `~/crontab_backup_2026-10-18.txt` (original) y `~/crontab_final_2026-10-18.txt` (con límites).
- Pendiente: reinicio del VPS por actualizaciones de kernel (19 updates, kernel 7.0.0-38). Ya no hace falta
  para que el crontab aplique: los límites están activos y sobreviven al reinicio.
