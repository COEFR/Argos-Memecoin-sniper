# Argos — Memecoin sniper (Solana)

Argos, el gigante de los cien ojos, no dejaba de mirar. Esto hace lo mismo con la blockchain de Solana:
lee los lanzamientos de pump.fun, registra quién compró cada uno con su monto y el segundo exacto en que
entró, y con esas coincidencias reconstruye los grupos de wallets que vienen comprando juntas desde antes
de que el token sea noticia. Sin grupos de pago, sin listas de KOLs y sin pedirle permiso a nadie.

El circuito:

1. **Cosecha.** Cada pasada baja los lanzamientos nuevos de pump.fun y hasta 40 compradores por
   lanzamiento, con monto y timestamp. Más compradores son más parejas posibles, así que los grupos
   aparecen antes.
2. **Agrupa.** Para cada token se buscan las wallets que compraron dentro de una ventana de co-compra de
   300 segundos. Un par de wallets se une recién cuando coincide en **dos lanzamientos distintos**; el
   agrupamiento se resuelve con union-find con compresión de caminos.
3. **Rastrea el origen.** De cada grupo se sigue el fondeo seis saltos hacia atrás hasta la wallet que
   pagó. Ahí se separa un grupo de personas de una granja con un mismo pagador.
4. **Vigila en vivo.** Un WebSocket con `logsSubscribe` filtrado por `mentions` del mint detecta la ráfaga
   en el bloque en que ocurre: la ventana es de 150 segundos y avisa con 2 wallets de 0,1 SOL o más.
5. **Mide.** Cada aviso se evalúa contra las velas posteriores: FDV al momento del aviso contra el pico
   posterior, con una ventana de seguimiento de 6 horas.
6. **Controla.** En paralelo corre un test con tokens frescos al azar, con la misma ventana y la misma
   vara. Si el sistema no le gana al azar, no sirve, y eso tiene que poder verse.

Solo informa. No opera, no mueve fondos y no pide claves privadas. Los datos y las claves quedan fuera del
repositorio.

## Cómo encuentra un grupo sin que se lo cuenten

El corazón son treinta líneas: una clase con union-find. Cada wallet que compró un lanzamiento se une con
las que coincidieron en ese mismo token y en otro distinto. Treinta líneas, y ningún dato comprado.

<img src="img/codigo.png" alt="la clase Unión, el union-find que arma los grupos, en cohortes.py" width="100%">

Lo que decide todo no es la estructura de datos, son los umbrales:

| parámetro | valor | por qué |
|---|---|---|
| ventana de co-compra | 300 s | más ancha, más parejas; demasiado ancha y junta compras sin relación |
| coincidencias mínimas | 2 lanzamientos | **este número no se baja**: una coincidencia es casualidad |
| compradores leídos | 40 por lanzamiento | más parejas por token, grupos que aparecen antes |
| monto mínimo por comprador | 0,1 SOL | por debajo es polvo y ensucia el grafo |
| cohorte mínima | 2 wallets | primero aparecen los dúos y después se van agrandando |

Coincidir donde compraron cuarenta personas no dice nada. Coincidir donde compraron tres, dice casi todo.

Después viene la parte que casi nadie hace: **de dónde salió la plata**. Se sigue el fondeo seis saltos
atrás hasta dar con la wallet que pagó, y ahí se cae la máscara: dos grupos que parecían independientes
suelen terminar con el mismo pagador, y lo que parecía una comunidad es una granja con varios sombreros.

## Cómo está construido

Todo con librería estándar de Python y APIs públicas. Cero tokens de LLM: son reglas explícitas.

- **Lectura on-chain.** `getSignaturesForAddress` y `getTransaction` con `encoding: jsonParsed` sobre RPC
  público, con paginado y caché. La forense profunda se resolvió en **una sola pasada** por wallet: bajar
  cada transacción dos veces (una para flujos, otra para tokens) hacía que una wallet de mil transacciones
  no terminara nunca.
- **Tiempo real.** `logsSubscribe` con `mentions: [mint]` sobre WebSocket, con ping cada 20 segundos. La
  ráfaga se detecta en el bloque en que pasa, no en la pasada siguiente.
- **Escritura concurrente.** El estado lo escriben cuatro procesos (radar cada 5 minutos, patrulla cada 3,
  muestreo cada 5, comandos cada minuto). El truco de escribir a un temporal y renombrar **no alcanza**:
  el problema es el ciclo completo de leer, modificar y guardar, así que se serializa con `flock` y los
  temporales llevan el pid en el nombre.
- **Cupo de la API.** Cada corrida anota cuánto gastó, así el consumo se mide desde adentro y no desde el
  panel del proveedor. Las claves viven fuera del repositorio y el tope diario está declarado en el código.
- **Discord.** El envío se verifica releyendo el canal: un 200 del POST no prueba que el mensaje haya
  quedado donde el usuario lo ve.

## Qué encontró

Esta sección va antes de la estructura a propósito. Todo medido, nada estimado:

- **Insiders rentables: cero.** Quince wallets auditadas con historial profundo sobre dieciséis tokens que
  habían despegado. Ninguna de las que recibió tokens gratis y después vendió terminó ganando de forma
  sostenida. La información privilegiada existe; el talento para convertirla en plata no apareció.
- **El reparto masivo tiene firma propia.** Tres lanzadores distintos con el mismo saldo exacto (7.381
  SOL) repartiendo a 372, 354 y 339 wallets. No son tres proyectos: es un operador con servicio de
  lanzamiento y una wallet nueva por token. Y el reparto va a cáscaras sin una sola operación, así que no
  hay demanda detrás: esos tokens colapsan.
- **Grande no es sinónimo de bueno.** Un grupo de nueve wallets con 31 SOL juntos marcó un pico de 24x y
  una mediana de 0,46x. Cada tanto le pega a una y el resto del tiempo pierde: son snipers entretenidos,
  no inversores.
- **Los top holders de RugCheck engañan a cualquiera.** Incluyen la curva de pump.fun, que en un token
  recién lanzado es el 94-100% del supply. Sin descontarla, todo token nuevo parece un rug con el "top 10
  al 100%". Es el error más común de las herramientas que puntúan tokens.

## Cómo se mide, y por qué vale creerle

Un sistema que se autoevalúa tiene que decir con qué vara mide:

- **FDV del momento contra el pico posterior**, sobre una ventana de 6 horas. La misma cuenta que hace
  Rick con `/ga`: sirve para saber si hubo profit después del aviso, no para decorar un canal.
- **Mediana antes que promedio.** Un 24x perdido entre veinte fracasos mueve el promedio y no cambia nada
  de lo que pasó. La mediana sí.
- **Puntaje por percentil del día**, no por suma acumulada: el ranking se autocalibra cuando el mercado
  cambia de humor, en vez de quedar pegado a los números de la semana pasada.
- **La ventana corta miente.** Para puntuar una wallet se exigen mil transacciones leídas y treinta
  posiciones cerradas. Con menos, aparece rentabilidad que no existe.
- **Índice contra el azar.** El test de control corre con tokens al azar, medidos igual. Sin esa
  comparación, cualquier racha parece mérito.

## Estructura

Cosecha y grupos:

| archivo | qué hace |
|---|---|
| `cohortes.py` | cosecha los lanzamientos, arma los grupos con union-find, busca el origen y los clasifica |
| `pump_analizador.py` | analiza un token y devuelve el veredicto con sus banderas |
| `grupos_onchain.py` | ve las compras de los grupos sin entrar a ningún grupo |

Forense e insiders:

| archivo | qué hace |
|---|---|
| `wallet_forense.py` | le pasás una dirección y cuenta de dónde vino la plata y a dónde fue |
| `forense_profunda.py` | la misma forense en una pasada con caché, para wallets de miles de transacciones |
| `insiders_cruce.py` | insiders por cruce: quién recibió gratis en el lanzamiento y después vendió |
| `cazar_fantasmas.py` | cruza quién vende contra quién recibió gratis, para separar al dev de los compradores |
| `red_wallets.py` | saca las wallets conectadas a una dirección y las puntúa |

Vigilancia en vivo:

| archivo | qué hace |
|---|---|
| `vigilante_calls.py` | WebSocket con `logsSubscribe`: detecta la ráfaga en el bloque en que pasa |
| `vigilante_copiable.py` | avisa apenas una wallet que sirve compra algo, con tope de avisos por hora |
| `calls_detector.py` | detector de ráfagas para el radar |

Medición:

| archivo | qué hace |
|---|---|
| `registro_calls.py` | guarda cada aviso y lo evalúa después, para recalibrar con datos reales |
| `control_test.py` | el test de control contra tokens al azar |
| `tabla_calls.py` | la tabla de resultados, la misma cuenta que hace Rick con `/ga` |
| `consumo.py` | cuánto cupo se gastó y cuánto queda, medido desde adentro |

Discord e infraestructura:

| archivo | qué hace |
|---|---|
| `comando_discord.py` | los comandos de chat del sistema |
| `canal_wallets.py` | escucha el canal de auditoría y contesta solo |
| `jsonio.py` | escritura serializada con `flock`: cuatro procesos escribiendo el mismo estado sin pisarse |
| `NOTAS.md` | todo lo medido, incluidas las cosas que no funcionaron |

## Reglas que no se negocian

Salieron de errores reales, no de manuales:

- **Mil transacciones y treinta posiciones cerradas** antes de puntuar una wallet. La ventana corta miente,
  siempre.
- **Dos coincidencias o no hay grupo.** El umbral no se baja ni para tener más señales.
- **Volumen de 24 h menor a 500 USD descalifica** un token, por linda que sea su liquidez: no hay a quién
  venderle.
- **La primera pasada de una wallet nueva es línea base.** Se anotan sus firmas sin avisar. Sin eso se
  publican diez avisos de historial viejo y el canal se vuelve ruido.
- **Filtro de polvo obligatorio.** Las wallets reciben micro-transferencias constantes de 0,000005 SOL.
  Sin exigir un movimiento mínimo, el tracker llena el canal de avisos que no significan nada.
- **Un pico no es un resultado.** La mediana decide, el máximo decora.

## Licencia

© COEFR. Uso personal. Nada de esto es asesoría financiera, y el 76% de los tokens nuevos de Solana
termina en rug: los datos están para decidir con umbrales, no con entusiasmo.
