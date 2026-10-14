# Humanizador de texto (castellano)

Reescritura de textos con jerga de IA. En vez de "limpiar" (sacar relleno), **reescribe
la estructura**, que es lo que de verdad miden los detectores: la variación del largo de
las oraciones (burstiness) y la repetición del vocabulario.

## El pipeline

1. `scripts/scan_es.py` — catálogo de marcas de IA en castellano (20 prohibiciones duras,
   10 marcadores blandos). Detecta solo si el texto es académico y no marca fórmulas
   legítimas de paper.
2. `scripts/reescribir.py` — mide burstiness y repeticiones. **Es la vara**: se le pasa al
   agente como objetivo y se vuelve a medir al final para mostrar el antes → después.
3. El agente reescribe con reglas de forma (alternar frases cortas y largas, partir
   subordinadas, variar arranques) y **reglas de contenido que tienen prioridad**:
   prohibido inventar datos, cambiar referencias o agregar adjetivos.
4. `humanizar2.py` — pulido final por diccionario (equivalencias jerga → forma humana).
5. Se vuelve a medir y se publica el ritmo antes → después.

## Resultados medidos

| texto | burstiness antes | después | objetivo |
|---|---|---|---|
| párrafo con jerga pura | 0,157 | **0,541** | >0,45 |
| académico (Chavarriaga) | 0,530 | **0,540** | >0,45 |
| histórico (Bolivia 1825) | 0,600 | **0,530** | >0,45 |

Los tres conservan todos los datos (cifras, nombres propios, fechas).

## Lo que NO promete

No garantiza pasar Turnitin ni ningún detector: comparan contra su propia base y sus
umbrales cambian. Esto mejora la forma del texto, que es lo único medible y verificable.

## Uso

```bash
python3 scripts/scan_es.py --texto "..."          # qué marcas tiene
python3 scripts/reescribir.py --texto "..."       # cuánto ritmo tiene (la vara)
python3 humanizar2.py --texto "..." --sin-azar    # pulido por diccionario
```
