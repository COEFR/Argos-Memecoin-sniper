# Argos — inteligencia on-chain de memecoins (Solana)

Detecta grupos de wallets que compran juntas en Solana, sin depender de grupos de pago ni de listas de
KOLs. Todo sale de la blockchain: lee los lanzamientos de pump.fun, mira quién compró cada uno y a qué
segundo, y con esas coincidencias arma los grupos.

1. **cosecha** lanzamientos de pump.fun y lee quién compró cada uno: wallets, montos y a qué segundo.
2. **arma grupos** con las wallets que coinciden comprando en dos o más lanzamientos distintos (union-find).
3. **busca el origen** de cada grupo, su perfil (personas o granjas de bots) y le pone nombre.
4. **vigila en vivo** por WebSocket las wallets que dan plata: el aviso sale en segundos, no cada 10 minutos.
5. **mide** cada aviso, desde el FDV del momento hasta el pico posterior, y clasifica los grupos por
   resultado real, no por lo que prometen.
6. corre un **test de control** con tokens frescos al azar, medidos igual, para saber si el sistema le gana
   al azar en vez de creerle a la intuición.

Solo informa: no opera, no mueve fondos y no pide claves privadas. Los datos y las claves quedan fuera del
repositorio.

## Cómo arma los grupos

El corazón es una clase chica: cada wallet que compró un lanzamiento se une con las demás que coincidieron
en ese mismo token y en otro distinto. Con eso solo, sin listas externas, aparecen los grupos repetidos
que compran juntos.

<img src="img/codigo.png" alt="la clase Unión, el union-find que arma los grupos, en cohortes.py" width="100%">

Alrededor de eso hay una decisión de diseño que vale contar: **una coincidencia no alcanza**. Dos wallets
que compran el mismo token pueden ser casualidad. Se piden dos o más lanzamientos distintos, y cuanto más
raro es el token (poca gente comprando), más pesa la coincidencia.

## Qué encontró

Esta es la parte incómoda y por eso está arriba. Medido, no estimado:

- **Insiders rentables: cero.** Se auditaron 15 wallets con historial profundo sobre 16 tokens que
  habían despegado. Ninguna de las que recibió tokens gratis y vendió terminó ganando de forma sostenida.
- **El lanzamiento de reparto masivo tiene firma propia.** Tres lanzadores distintos aparecieron con el
  mismo saldo exacto (7.381 SOL) repartiendo a 372, 354 y 339 wallets. No son tres proyectos: es un mismo
  operador con servicio de lanzamiento. El reparto va a cáscaras sin operaciones, así que no hay demanda
  orgánica detrás: esos tokens colapsan.
- **Los grupos grandes no son buenos por ser grandes.** Un grupo de 9 wallets con 31 SOL juntos tenía un
  pico de 24x pero mediana de 0,46x: son snipers, no inversores.
- **Los top holders de RugCheck engañan.** Incluyen la curva de pump.fun, que en un token nuevo es el
  94-100% del supply. Sin descontarla, todo token recién lanzado parece un rug con "top 10 al 100%".

## Estructura

Cosecha y grupos:

| archivo | qué hace |
|---|---|
| `cohortes.py` | cosecha los lanzamientos, arma los grupos con union-find, busca el origen y los clasifica |
| `pump_analizador.py` | analiza un token de pump.fun y devuelve el veredicto con sus banderas |
| `grupos_onchain.py` | ve las compras de los grupos sin entrar a ningún grupo |

Forense e insiders:

| archivo | qué hace |
|---|---|
| `wallet_forense.py` | le pasás una dirección y cuenta de dónde vino la plata y a dónde fue |
| `forense_profunda.py` | la misma forense pero en una pasada, con caché, para wallets de miles de transacciones |
| `insiders_cruce.py` | insiders por cruce: quién recibió gratis en el lanzamiento y después vendió |
| `cazar_fantasmas.py` | cruza quién vende contra quién recibió gratis, para separar al dev de los compradores |
| `red_wallets.py` | saca las wallets conectadas a una dirección y las puntúa |

Vigilancia en vivo:

| archivo | qué hace |
|---|---|
| `vigilante_calls.py` | WebSocket: detecta la ráfaga de compras en segundos |
| `vigilante_copiable.py` | avisa apenas una wallet que sirve compra algo |
| `calls_detector.py` | detector de ráfagas para el radar |

Medición:

| archivo | qué hace |
|---|---|
| `registro_calls.py` | guarda cada call y la evalúa después, para recalibrar con datos reales |
| `control_test.py` | test de control contra tokens al azar |
| `tabla_calls.py` | tabla de resultados, la misma cuenta que hace Rick con `/ga` |
| `consumo.py` | cuánto cupo de Helius se gastó y cuánto queda |

Discord e infraestructura:

| archivo | qué hace |
|---|---|
| `comando_discord.py` | los comandos de chat del sistema de calls |
| `canal_wallets.py` | escucha el canal de auditoría y contesta solo |
| `jsonio.py` | escritura atómica de JSON, segura entre procesos |
| `NOTAS.md` | todo lo medido, incluidas las cosas que no funcionaron |

## Reglas que no se negocian

Estas salieron de errores reales, no de manuales:

- **Las primeras 1.000 transacciones y 30 posiciones cerradas son el mínimo** para puntuar una wallet. Con
  ventanas cortas el resultado miente.
- **Un volumen de 24 h menor a 500 USD descalifica** un token, por linda que sea su liquidez: no hay a
  quién venderle.
- **La primera pasada de una wallet nueva es línea base**: se anotan sus firmas sin avisar. Si no, se
  publican diez avisos de historial viejo y el canal se vuelve ruido.
- **Filtro de polvo obligatorio**: las wallets reciben micro-transferencias constantes. Sin exigir un
  movimiento mínimo de SOL, el tracker llena el canal de avisos sin valor.
- **El índice de un test de control decide si el sistema sirve.** Sin comparar contra el azar, cualquier
  racha parece mérito.

## Licencia

© COEFR. Uso personal. Nada de esto es asesoría financiera, y el 76% de los tokens nuevos de Solana
termina en rug: los datos están para decidir con umbrales, no con entusiasmo.
