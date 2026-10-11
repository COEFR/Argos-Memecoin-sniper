# Memecoins — Investigación de estrategias, bots, indicadores y wallets
**Fecha:** 6 de octubre de 2026 · **Fuentes:** Reddit (r/solana, r/memecoins, r/SolanaMemeCoins, r/pumpfun, r/CryptoMoonShots), docs oficiales de las herramientas, papers académicos y reportes de firmas.
**Nivel de evidencia:** cada bloque indica si es *dato verificado* (lo comprobé en la fuente), *reportado* (lo dice un hilo/guía, sin verificación) o *no verificado*.

---

## 0. Lo primero: ¿esto es rentable? (veredicto con números)

| Dato | Cifra | Fuente | Estado |
|---|---|---|---|
| Memecoins que ya están "muertas" | **97%**; vida media 1 año (vs. 3 de un proyecto cripto normal) | Chainplay / Fortune | corroborado |
| Tokens de Pump.fun que siguen vivos a los 90 días | **4,55%** (de 18,67 M lanzados, 68,67% murió el día 1) | CoinGecko / CryptoBriefing | verificado |
| Tokens nuevos en Solana (Orca/Raydium/Meteora) que son rug pull | **76.469 de 100.063 (~76,4%)**, 1er semestre 2025 | arXiv 2603.24625 (SolRugDetector) | verificado |
| Wallets de Pump.fun que materializaron +10.000 USD | **0,412%** (99,6% no) | Dune / Cointelegraph | verificado |
| Traders de memecoins de Solana rentables a 90 días | **6,16–6,25%** (mediana en pérdida ~-120 USD) | DWF Ventures / FOMO (Dune) | verificado |
| Mejor mes reciente (abr-2026): traders en verde | **73,28%**, pero 65% ganó 1–500 USD y solo 5,37% superó 1.000 USD | CoinGecko | verificado |
| Tokens de "alto retorno" (>100%) con crecimiento artificial | **82,8%** (wash trading + inflado de precio vía pool) | arXiv 2507.01963 (USENIX Security) | verificado |
| Inversores de $TRUMP con pérdida | ~1 M de personas, **-3.810 M USD** | NYT | reportado |
| Bots de sandwich en Solana | extrajeron **370–500 M USD** en 16 meses | Sandwiched.me | no verificado por mí |

**Lectura en llano:** el diseño del juego hace que la plataforma, los insiders y los bots ganen pase lo que pase; la mayoría de los participantes pierde. La "ventaja" que busca todo el mundo (ser más rápido que el otro) es precisamente donde el retail ya no compite. No es imposible sacar plata, pero la probabilidad base es de un dígito bajo y hay que tratarlo como casino, no como inversión. Varios hilos lo dicen con esas palabras ("it's a casino. Bet small.").

---

## 1. Mapa de comunidades en Reddit (lo que pidió: "canales")

| Subreddit | Para qué sirve | Calidad |
|---|---|---|
| **r/solana** | El núcleo real de información técnica y de bots: comparativas, hilos de "¿qué bot usas?", mecánica de pump.fun, avisos de estafa. Es donde está la gente que opera en serio. | Alta, con mucho ruido |
| **r/SolanaMemeCoins** | Guías, setups de caza, discusión de herramientas. | Media, con posts promocionales disfrazados de guía |
| **r/memecoins** | Testimonios (ganancias, pérdidas), psicología, "aceptá que es apuesta". | Media (mucho auto-reporte) |
| **r/pumpfun** | Mecánica de lanzamientos, volumen falso, bundles. | Media |
| **r/SolanaSniperBots**, r/SolanaTrading, r/TradingBotTalk | Comparativas de bots y configuraciones. | Baja-media: hay promo de afiliados |
| **r/CryptoMoonShots** | Antes era el epicentro de shilling; hoy guías suelto. | Baja |
| **Los "top" de cualquier sub** | ⚠️ Infestados de spam promocional de tokens (ej. posteos diarios de $KENDU). **No usarlos como fuente de estrategia.** | Basura |

Truco de lectura: los hilos útiles son los de **comentarios**, no los posts. Los mejores hallazgos de esta investigación salieron de respuestas dentro de hilos de r/solana.

---

## 2. Estrategias que reporta la comunidad

### 2.1 Lo que la comunidad repite como regla de riesgo (lo más consistente de todo)
- **Tamaño por operación: 1–3% del portafolio.** "Lose on 20 in a row, you survive" (r/SolanaMemeCoins).
- **Salidas fijadas ANTES de entrar.** El error más citado como causa de ruina es no tomar ganancias: "Didn't sell. Watched it tank."
- **Take-profit escalonado**: la config más repetida es *recuperar capital en 2x* y luego vender 50% en cada duplicación; otra variante: 50% a 3x, 30% a 6x, 20% "moonbag".
- **Automatizar las ventas**, porque el humano no ejecuta: los bots con auto-sell/TP-SL aparecen una y otra vez justamente por eso.
- **Liquidez mínima** como filtro de salida, no de entrada: si ponés 10.000 USD en un token con 4.000 USD de liquidez, solo salís con 4.000.
- Rango de salida por TP/SL que reportan: TP +20/+50% con SL -25/-30%.

### 2.2 Timing
- El consenso duro: **si el token ya está "trending", llegaste tarde.** "Bots bought it, insiders dumped, you're exit liquidity."
- Los pumps de memecoins duran **4-8 horas**, no días.
- Corriente que recomienda **entrar en 2ª/3ª ola** (post-dump, cuando la comunidad sobrevive) en vez de sniping del lanzamiento.
- El **sniping pre-migración de pump.fun está saturado**: los bots compiten con bribes y detectan antes que el propio dev. Varios hilos lo declaran directamente no rentable para retail.

### 2.3 Mecánica de pump.fun (el bloque técnico más sólido)
- ~800 M de 1.000 M de tokens entran en la curva al lanzamiento.
- Al llegar al umbral (~69 k de mcap / ~85 SOL) la curva cierra y la liquidez **migra atómicamente a Raydium**; el LP se quema (el dev ya no puede retirar liquidez, pero sí vender su bolsa → "giga dump", distinto de un rug técnico).
- Tasa de graduación a DEX externa: **<1–2%** (Dune/CoinTelegraph).

### 2.4 Filtros de entrada que aparecen en los hilos
- DexScreener: par de menos de 2 h, volumen 24 h >15 M, esperar **4 velas verdes de 15 m con volumen creciente** y recién entrar (reportado en un hilo, con BOME/SLERF como ejemplos).
- Zona de mcap preferida: **100 k–500 k** con volumen masivo, buscando que haga 1 M antes de apagarse. <100 k = riesgo alto; >200 k = "más seguro".
- +1.000 holders, chequeo en Solsniffer/RugCheck, ordenar por nº de trades para ver si "no son las mismas 3 cuentas inflando las velas", y revisar la cuenta de Twitter (creada hoy = rug; vieja = comprada por credibilidad).
- Herramienta de descubrimiento: el ícono 🔥 de DexScreener (top 10 de los últimos 5 min).

### 2.5 Lo que la comunidad dice que NO funciona
- **Análisis fundamental**: "es una foto de un perro, no hay fundamentos". Proyecto real con devs doxxeados y liquidez bloqueada hizo 2x y murió; un scam con 40% del supply hizo 30x.
- **Análisis técnico clásico en memecoins**: "la moneda tiene 4 horas de vida, tus retrocesos de Fibonacci son cope". (Matiz: el volumen sí se usa y sirve; ver §5.)
- **Grupos de "alpha" pagos**: calls recicladas 3 h tarde, "estás comprando coordenadas de liquidez de salida".
- **Sentiment bots**: miden hype después de que el movimiento ya ocurrió.
- **Copy-trading de wallets snipers**: la wallet gana porque entra y sale en <10 segundos; vos entrás tarde y quedás atrapado (hilo "Stop Snipe Copy Trading"). El negocio beneficia a la plataforma por comisiones.
- **No tomar ganancias** (el error nº 1).

### 2.6 Una advertencia de método
Casi todos los "me va bien" de Reddit son **auto-reportes anónimos sin verificar** ($500→50 k, $200→73 k, 1.000 USD/día). Están sujetos a sesgo de supervivencia. Tomalos como anécdota, nunca como expectativa.
Los que sí suenan creíbles son los hilos de pérdida ("hice 2k en 3 meses y perdí 8k en un parpadeo") y los que describen procesos aburridos (tamaño chico, TP fijo, checklist de 2 minutos).

---

## 3. Bots y terminales (con costos y si te piden la clave)

### 3.1 El costo real, que es donde muere la cuenta
Cada operación paga **cuatro capas**:
1. **Comisión del bot/terminal: ~0,9–1% POR LADO** (compra y venta) → ~2% por ida y vuelta.
2. **Comisión del pool/DEX**: Raydium ~0,25%, pump.fun/pump-swap ~1%, algunos pools hasta ~5%.
3. **Priority fee** (pago a validadores para priorizar tu tx): los terminales traen por defecto ~0,001 SOL; Photon viene con valores altos (~0,008 SOL por defecto).
4. **Bribe / Jito tip** (subasta privada; en 2026 Jito corre en >95% del stake y los tips son >60% del volumen de priority fees de la red).

**Consecuencia medida en la comunidad:** dentro de los primeros 60 s de un token puede pasar un **6% de slippage+fees** por ciclo completo → necesitás >6% solo para empatar. Un caso citado: trade de 0,15 SOL con take-profit de +50% **terminó en pérdida** por fees y tips (Photon).

### 3.2 Comparativa de bots (datos de docs oficiales, oct-2026)
| Bot / terminal | Cadena | Comisión | Custodia | Nota |
|---|---|---|---|---|
| **Axiom** | Solana (+BNB/perps) | 0,95% entrada → 0,75% (cashback) | No custodial (Turnkey) | El más usado en 2026 (~40% de cuota según terceros); sniping de migración y perps; phishing de invite codes masivo |
| **GMGN** | Multichain | 1% (0,7–0,9% con código) | No custodial declarado | El mejor para **wallet-tracking** y copy-trading |
| **Trojan** | Solana | 0,9% (bot) / 1% (terminal) | No custodial, clave exportable | Rápido; hilo clásico de pérdida por acumulación de comisiones |
| **BONKbot** | Solana | 1% | No custodial (firma remota, auditado por sec3) | Sin copy-trading; volumen residual en 2026 |
| **Photon** | Solana/Eth | 1% + 1% (compra y venta) | No custodial (clave se muestra una vez) | Quejas fuertes por fees por defecto altos; cuota desplomada en 2026 |
| **BullX (Neo)** | Multichain | 1% + extra fijo por tx | Semi-no custodial (enclaves, sin equipo público) | ⚠️ Trading deshabilitado desde el 1-oct-2026 (solo retiros) |
| **Banana Gun / Maestro** | Multichain | 1% (Banana 0,5% en ETH manual) | No custodial | Ambos sufrieron exploits (2024) y **reembolsaron**; Maestro entrega mnemonic además de clave |
| **Bloom / Pepeboost / Nova / Sol Trading Bot** | Solana / multichain | 0,9–1% | Hot wallet con clave exportable | Bloom tiene un reporte de drenaje **no verificado** (posible bot impostor); Nova con acusaciones de scam no verificadas |
| **Terminal (ex-Padre, de pump.fun)** | Multichain | ~1% (reportado) | No custodial | Buenas órdenes (market/límite/DCA/SL/TP), poca tracción en Reddit |
| **DexScreener Swap** | Multichain | ~0,5% reportado + pool + gas | No custodial | El screener es gratis; el swap confunde a la gente |
| **Snipers de lanzamiento / bundlers** | Solana | Bundle tip + 4,5–10 SOL (15-20 wallets) + RPC 9–100 USD/mes + fee; snipers de servicio desde ~2.500 USD | Self-custody | Riesgo de manipulación: un bundle deja al dev gran parte del supply |

**Punto clave de seguridad:** "no custodial con clave exportable" **no es lo mismo que una hardware wallet**. En la práctica el bot firma por vos y guarda la clave en su infraestructura. Hackeos documentados: Banana Gun (~3 M USD, 11 usuarios, sep-2024, reembolsado), Maestro+Unibot (~1,1 M USD, oct-2024, reembolsado), Solareum (~520 k USD y cierre del bot). Además hay **bots impostor y repos de GitHub falsos** que distribuyen malware que roba credenciales (SlowMist) y un bot real drenado desde adentro (Tradewiz, ~459 k USD).

### 3.3 Qué es una "bundle buy" (y por qué te infla el costo)
Un bundle compra con 15-20 wallets en un mismo bloque para que el lanzamiento parezca orgánico. Costo real: creación del token 0,02 SOL + tip de bundle + 4,5–10 SOL de compras + RPC premium + fee del bundler. Es **la firma del insiders**: si lo ves en un token, estás comprando después de alguien con ventaja de precio.

---

## 4. "Sniper wallets" y smart money (lo que pidió: wallets)

### 4.1 Herramientas
- **GMGN** — etiqueta wallets como Smart Money / Sniper / Insider / Bundler / KOL / Paper Hand; muestra first-buy time, top-10 y chequeo de honeypot por token. Es la más usada para esto.
- **Cielo** — tracker multichain (30+ cadenas) con alertas por Telegram/Discord, ranking por PnL/ROI/win rate y tags (Gem Finder, High Win Rate, Sniper, New Wallet, Flipper, MEV).
- **Bubblemaps** — mapa de burbujas que agrupa wallets de la misma entidad por color + bundle checker + mapa histórico (¿ya vendieron?).
- **Nansen / Arkham** — etiquetas de Smart Money por ventana (30/90/180 d) y atribución a entidades reales.
- **RayBot / SpyBot / Subglow** — bots de Telegram de alertas de swap por wallet; RayBot agrega "Auto Add" (wallets fondeadas por las que seguís) y reglas multi-wallet.
- **Birdeye / Solscan / DeBank / Dune** — holder lists, top traders, PnL y consultas SQL propias.

### 4.2 Qué se mira en una wallet (en orden)
1. **PnL realizado y ROI en 30/90/180 días** y all-time.
2. **Diversidad de tokens**: descartar que el resultado venga de un solo acierto.
3. **Win rate como dato secundario y sospechoso**: 90%+ con muchas operaciones es señal de alarma (bot, o wallet que vende a quienes la copian), no de talento.
4. **Hold time** medio.
5. **PnL realizado vs no realizado** (lo no realizado es papel).
6. **Timing de entrada**: lo más valorado es que haya comprado en los primeros segundos/bloques (first-buy time), no la etiqueta. Una "smart money" que compra a las 3 h es un turista.

**Ojo con las etiquetas:** `KOL` = fama (no rentabilidad), `Sniper` = compró en el bloque 0 (no rentabilidad), `Smart Money` = historial de PnL (**retrospectivo, no predictivo**). Y el PnL que muestra la plataforma **no incluye fees, priority ni bribes**, así que está inflado.

### 4.3 Umbrales de concentración (los que la comunidad usa, [reportado])
| Métrica | Verde | Amarillo | Rojo |
|---|---|---|---|
| Top-10 holders | <20% | 20–35% | >35% |
| Snipers (compraron <20 s del launch) | <5% | 5–15% | >15% |
| Bundles (misma mano repartida) | <10% | 10–20% | >20% |
| Insiders | <10% | 10–20% | >20% |
| Cluster de una entidad (Bubblemaps) | <15% | 15–30% | >30% (>50% = control total) |

Regla combinada que circula: 1 amarillo no mata la operación; **2+ amarillos = reducir tamaño y apretar la salida**; rojo + holders planos = saltar.

### 4.4 Señales de que los insiders están distribuyendo (para salir)
- El cluster estable empieza a **fragmentarse en wallets nuevas** (reparten antes de vender).
- La lista de top holders **cambia rápido**; aparecen más conexiones entre burbujas (consolidación antes del dump).
- El mapa **se concentra** en vez de diversificarse mientras el precio sube (soft rug).
- El deployer mueve tokens o hace una **venta de prueba**.
- Ventas escalonadas de 10–20% en la primera vela verde.
- **Si no hay nuevo máximo en ~30 min, los snipers empiezan a sangrar** la posición.
- Truco visual reportado: varias wallets con **porcentajes casi idénticos y bajos** (2,3 / 2,2 / 2,1 / 1,89%) = un solo dueño repartido.

### 4.5 Copy-trading: por qué casi siempre pierde
- **Llegás tarde por diseño**: el original entra/sale en segundos; el desfase reportado va de 8–12% hasta 10–25% peor por operación, y con un margen medio del 15% eso deja en cero o en pérdida.
- **El líder te ve**: hay varios copiando la misma wallet, el flujo empuja el precio y el original vende sobre vos. Existe el patrón de wallets que **"farman" copiadores** (suben el precio con las compras de los que copian y dumpean).
- **Front-running**: adelantar al líder requiere streams de mempool/shred y pagos altos a validadores. No es realista para retail; si no lo tenés, el líder te front-runea a vos.
- Los "win rate 75-95%" suelen ser **farming de copiadores**.
- Costo de infraestructura propia: un hilo reporta **500+ USD/mes** mínimo, con slippage de 30-50% que mata el resultado.
- **Vender "wallets ganadoras" o "alpha de smart money" como servicio es estafa o casi-estafa.** Una wallet que gana no necesita venderse, y las listas suelen existir para que los compradores paguen la salida.

---

## 5. Indicadores, checks y filtros anti-rug

### 5.1 Checks de contrato (los que sí hay que hacer, 2 minutos)
| Check | Regla | Herramienta |
|---|---|---|
| Mint authority | Debe ser **null** (si no, pueden acuñar y diluirte) | RugCheck, Solscan, Birdeye `/token_security` |
| Freeze authority | Debe estar **revocada** (si no, congelan tu wallet y no podés vender) | RugCheck, Solscan |
| Metadata mutable | Preferir inmutable (solo afecta nombre/imagen, pero es bandera) | RugCheck, GoPlus `metadata_modifiable` |
| LP quemada o bloqueada | Exigir quemada/bloqueada (en pump.fun es por defecto) | RugCheck, Solsniffer |
| Tax de compra/venta | >21% ya es señal; >40% alto; **>50% = honeypot efectivo** | Honeypot.is, GoPlus `buy_tax`/`sell_tax` |
| Límites de venta / transfer bloqueado | Flags críticos: `effective_honeypot_low_sell_limit`, `transfer_blocked`, `closed_source` | Honeypot.is, TokenSniffer |
| Token-2022 | Vigilar `permanent delegate`, `transfer hook`, `transfer fee` (pueden congelar o cobrar) | Birdeye, GoPlus (beta Solana) |
| Ratio liquidez/mcap | Un mcap que es múltiplo grande de la liquidez se apoya en un pool minúsculo | DexScreener |
| Volumen falso | **"Regla 15x"**: 10 k de liquidez con 150 k de volumen en 60 s = inflado | DexScreener, Birdeye |
| Volumen vs holders | Volumen >> liquidez **con holder count plano** = reciclaje/wash | Birdeye, Dune |
| Re-escanear | El reporte es una foto: re-chequear antes de ampliar posición | RugCheck |

**Scores:** Solsniffer (0-100, 20+ indicadores; ejemplos reales: WIF 68, POPCAT 61, TRUMP 55, MEW 47), RugCheck (GOOD/riesgo), TokenSniffer ("Smell Test"; su propio monitoreo marca 50-75% de tokens nuevos como scam).

**Límite honesto de los checkers:** un simulador prueba **un par en un bloque**. En una prueba de 150 tokens, de 89 que Honeypot.is v2 y GoPlus llamaron "limpios", **49 (55%) tenían alguna capacidad owner-gated** según GoPlus. Si dos checkers discrepan → parar.

### 5.2 Indicadores de gráfico (y la verdad incómoda)
Lo que la comunidad usa de verdad, y que sobrevive al "TA no sirve acá":
- **Volumen relativo (RVOL)**: expansión >2-3x sobre la base marca ignición; volumen que expande en impulsos verdes y **contrae en los retrocesos** = sano; volumen creciente en cada caída = distribución.
- **Cuadrantes volumen/precio**: vol alto+precio sube = continuación; vol alto+precio baja = presión vendedora; vol bajo+precio sube = rally insostenible.
- **Estructura de impulso**: impulso → retroceso superficial (con menos volumen) → impulso → micro-consolidación → blow-off.
- **Market cap targets**: la gente escala por hitos de mcap (100 k / 500 k / 1 M), no por precios.
- **Ciclo de atención**: Atención → Liquidez → Expansión → FOMO → Distribución → Colapso. Identificar en qué fase estás antes de entrar.
- **Wyckoff** (comparar "esfuerzo" vs "resultado") para detectar distribución; **pump&dump clásico**: subida vertical sin consolidación + caída instantánea + movimientos súbitos de insiders.
- Con reservas: VWAP anclado, EMA 9/20/50, fibo 0.382/0.5/0.618, RSI en 1m/5m. **Aviso repetido:** "el RSI se vuelve inútil en fases parabólicas" y "las medias se destrozan"; el RSI se usa para **divergencias** de agotamiento, no como sobrecompra/sobreventa mecánico.

---

## 6. Cómo te sacan la plata (estafas documentadas)
1. **Rug pull / pull de liquidez** — ~76% de tokens nuevos en Solana (2025).
2. **Freeze authority** — congelan tu wallet para que no vendas.
3. **Honeypot** — se compra pero no se vende (64.661 detectados en 2022, +83%).
4. **Pump-and-dump coordinado** + **wash trading / inflado de precio vía pool** (82,8% de los tokens de "alto retorno").
5. **Influencers pagados** — hasta 65.000 USD por un video; el **76% de los influencers de X** promocionó memecoins que hoy están muertas; solo el 1% promocionó alguna vez un 10x.
6. **Cuentas hackeadas** de famosos/políticos para shillear y ruggear.
7. **Tokens copycat/falsos** (+700 copias de TRUMP).
8. **"Wallets ganadoras" y señales de pago** → te usan de contraparte.
9. **Bots y repos con backdoor/malware** (SlowMist; Tradewiz drenado).
10. **Wallet drainers**: aprobaciones maliciosas, extensiones/apps falsas, dominios clonados (abundan imitaciones de Photon, Axiom, BullX).
11. **Fuga estructural**: bots de sandwich/MEV (370–500 M USD en Solana en 16 meses, no verificado por mí) + fees de plataformas (pump.fun >1.000 M USD) salen del bolsillo del retail hacia la infraestructura y los insiders.

**Regla de oro:** nunca dar la seed phrase a nada que no sea una hardware wallet; verificar el **@ exacto** del bot (hay clones); no instalar repos/extensiones de "bots gratis"; y desconfiar de todo invite code que llegue por DM.

---

## 7. Bolivia / marco local (sin asesorar)
- La prohibición del BCB (Res. 044/2014 y RND 144/2020) **se dejó sin efecto el 26 de junio de 2024**; hoy se puede adquirir cripto vía sistema financiero.
- **No hay ley tributaria específica** sobre cripto: vacío normativo. No son moneda de curso legal, y el BCB advierte que el riesgo es de quien opera.
- Si va a mover montos reales, consultar a un contador/abogado. Esto no es asesoría financiera, tributaria ni legal.

---

## 8. Si igual quiere operar: guardrails mínimos basados en lo medido
1. Tratarlo como casino: **solo plata que puede perder entera**, sin excepción.
2. **1–3% del portafolio por token**, nunca "all in".
3. **Salida escrita antes de entrar** (TP escalonado + SL) y ejecutada por el bot, no a mano.
4. **Checklist de 2 minutos** obligatorio: mint/freeze revocados, LP quemado, top-10 <20%, bundles <10%, snipers <5%, no honeypot, liquidez suficiente para tu tamaño.
5. **Liquidez mínima** que permita salir con tu tamaño (si no podés salir, no entres).
6. No comprar velas verdes / no perseguir el pump ya visible.
7. **No copy-tradear snipers públicos** ni comprar listas de wallets.
8. Llevar registro de cada operación (entrada, salida, motivo). Sin registro no hay forma de saber si sirve.
9. Ignorar grupos de "alpha" pagos y DM con oportunidades.
10. Asumir que el 76% de lo que compres es rug y el 95% de los tokens muere a los 90 días — el plan tiene que funcionar **con eso en contra**.

---

## 9. Fuentes principales
**Estudios y datos verificados**
- CoinGecko / CryptoBriefing (4,55% de supervivencia): https://www.kucoin.com/news/flash/coingecko-study-only-4-55-of-pump-fun-tokens-survive-90-days
- Dune / Cointelegraph (0,412%): https://cointelegraph.com/news/pump-fun-crypto-traders-majority-do-not-realize-profits-dune-data
- Chainplay / Fortune (97% muertas): https://fortune.com/crypto/2024/08/07/memecoins-are-the-hottest-sector-in-crypto-but-97-of-them-are-already-dead
- DWF Ventures / FOMO (6,16%): https://www.cryptopolitan.com/6-solana-meme-traders-profit-in-90-days/
- CoinGecko abr-2026 (73,28% con matices): https://www.crowdfundinsider.com/2026/05/277755-solana-based-meme-coins-trading-platform-pump-fun-makes-recovery-research
- SolRugDetector, arXiv 2603.24625 (76.469 rugs): https://arxiv.org/abs/2603.24625
- A Midsummer Meme's Dream, arXiv 2507.01963 (82,8% artificial): https://arxiv.org/abs/2507.01963
- Galaxy Research, The State of Memecoins: https://www.galaxy.com/insights/research/memecoins-pump-fun-solana-kols
- Solana MEV / sandwich: https://sandwiched.me/research/state-of-solana-mev-may-2025-analysis

**Hilos de Reddit citados (los "canales")**
- Setup real de caza: https://www.reddit.com/r/SolanaMemeCoins/comments/1q8knvw/
- "Es puro juego, actuá en consecuencia": https://www.reddit.com/r/memecoins/comments/1q7rqms/
- Estrategia/TP-SL: https://www.reddit.com/r/solana/comments/1fhep99/
- Scalping > 100x: https://www.reddit.com/r/solana/comments/1g8wgrx/
- Umbrales de seguridad: https://www.reddit.com/r/solana/comments/1ru9xzo/
- Stop snipe copy trading: https://www.reddit.com/r/solana/comments/1isuj2h/
- Fees que se comen el trade (Photon): https://www.reddit.com/r/solana/comments/1gf7rjj/
- Volumen falso / regla 15x: https://www.reddit.com/r/pumpfun/comments/1tdpudm/
- Cómo evitar rugs: https://www.reddit.com/r/solana/comments/1gfo3in/
- Mecánica de la bonding curve: https://www.reddit.com/r/solana/comments/1ig4ib1/
- Guía de bundled coins en pump.fun: https://www.reddit.com/r/MemecoinSeason/comments/1hzdai4/
