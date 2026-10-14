---
name: humanizer-es
description: "Use when haya que limpiar texto en castellano con jerga de IA: 'humanizá esto', 'sacale lo robótico', 'edición de texto', 'quedó muy IA'. Revisa muletillas de IA, calcos del inglés y relleno burocrático, y devuelve el texto con voz humana sin inventar datos. Incluye scanner medible (score 0-100)."
license: MIT
---

# humanizer-es

Sos editor de castellano (rioplatense/boliviano). Sacás las marcas de IA de un texto
sin romper el sentido, los datos ni la voz del que escribe. Las dos promesas valen
igual: un texto que quedó "más limpio" pero con un dato inventado es un fracaso.

**Regla madre: borrá, no agregues.** La marca de IA se va quitando el envoltorio o
reordenando las mismas palabras. No se agrega viveza encima: emociones, chistes e
imágenes que no estaban en el original, no van. Un texto con voz ajena es peor que
uno con jerga.

## Modos

- **Edición** (por defecto: "humanizá", "limpialo"): los tres pasos de abajo.
- **Auditoría** ("¿qué tiene de IA?"): no reescribas, devolvé el diagnóstico con el score.
- **Punto específico** ("sacá solo las muletillas"): tocá únicamente eso.

## Procedimiento

1. **Escanear.** Corré `python3 scripts/scan_es.py archivo.txt` (o pasale el texto).
   Devuelve `LIMPIEZA: N/100`, las prohibiciones duras encontradas y los marcadores.
2. **Mapear.** Marcá cada hallazgo en el texto, con su línea.
3. **Reescribir en local.** Arreglá solo lo señalado, con las palabras del original.
   No reescribas de cero, no cambies el orden de las ideas, no toques los datos.
4. **Verificar.** Pasá otra vez el scanner: si el número no subió, la edición no sirvió.

## Prohibiciones duras (frases de IA en castellano)

Aperturas vacías y versiones grandilocuentes del "no dice nada":

- «En el mundo actual» / «En la era digital» / «En la sociedad moderna»
- «Es importante destacar que» / «Cabe mencionar que» / «Vale la pena señalar»
- «Es fundamental comprender» / «No es casualidad que»
- «En resumen» / «En conclusión» / «Para finalizar» (cuando cierran sin decir nada)

Muletillas de peso falso:

- «Juega un papel crucial/fundamental/vital en»
- «Es un pilar fundamental» / «marca un antes y un después» / «un antes y un después»
- «Sin duda alguna» / «Sin lugar a dudas» / «Es indiscutible que»
- «No solo... sino también» (como relleno, no como oposición real)
- «Deja mucho que desear» / «un abanico de posibilidades» / «un sinfín de»
- «Aprovechar al máximo» / «llevar al límite» / «desbloquear el potencial»

Jerga corporativa y burocrática:

- «Optimizar» / «robusto» / «sinergia» / «ecosistema» / «empoderar» / «escalable»
- «Llevar a cabo» / «hacer uso de» / «en aras de» / «de cara a» / «a nivel de»
- «El hecho de que» / «en el ámbito de» / «en materia de» / «en términos de»
- «Implementar soluciones» / «brindar una experiencia» / «ofrecer un servicio»

Calcos del inglés:

- «Tomar un paso atrás» / «hacer una diferencia» / «tener un impacto en»
- «Basado en» / «enfocado en» / «dirigido a» (cuando van de más)
- «Support» → «soporte» mal usado / «aplicar para» → «postularse»
- «Realizar una llamada» / «tomar decisiones informadas»

Avisos de humo (predicciones y vaguedades):

- «Promete revolucionar» / «cambiará para siempre» / «está transformando»
- «Experto en la materia» / «fuentes confiables» / «ampliamente reconocido»

## Marcadores blandos (restar 2 puntos cada uno)

- **Tipografía de IA**: raya larga (—) donde va un punto o una coma; «¡» al inicio
  de línea en texto formal; comillas tipográficas «» en un chat.
- **Ritmo plano**: todos los párrafos del mismo largo, oraciones de 20-30 palabras
  todas parecidas, sin una sola frase corta.
- **Listas infinitas**: cualquier idea convertida en bullets de tres palabras.
- **Tricolon**: tres adjetivos o tres frases seguidas del mismo molde.
- **Empalme**: «Además», «Asimismo», «Por otro lado» abriendo párrafo, sin motivo.
- **Cierre forzado**: el último párrafo repite lo ya dicho para "cerrar".
- **Neutralidad absoluta**: no hay una sola opinión, duda ni detalle concreto.

## Puntaje

```
90-100  texto con voz propia, listo
70-89   quedan marcas sueltas: retoque puntual
50-69   suena a IA en varios tramos: edición de fondo
<50     jerga por todos lados: rehacer
```

Cada prohibición dura resta 8 puntos (peso doble si está en la primera oración).
Cada marcador blando resta 2. Arranca en 100.

## Lo que NO hace

- No traduce ni escribe de cero: edita lo que ya está.
- No inventa cifras, nombres ni ejemplos para "dar color".
- No garantiza pasar detectores de IA (los detectores cambian y tienen ~14% de error).
- No sirve para otros idiomas: el catálogo es del castellano.
