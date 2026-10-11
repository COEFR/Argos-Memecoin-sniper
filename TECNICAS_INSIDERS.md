# TÉCNICAS PARA CAZAR INSIDERS — dossier (2026-10-07)

Recopilado de GitHub, papers y guías. Cada técnica: **señal → cómo se mide → umbral → cómo lo
implemento con Helius**. Fuentes al final.

---

## 1. Compras en el slot de creación (la señal más limpia)

- **Qué**: el bundle de lanzamiento compra en el **mismo slot** que la creación del token
  (`initialize_pool` / creación del mint). Nadie de afuera puede transaccionar ahí.
- **Cómo se mide**: ordenar las tx del mint por slot; contar cuántas **compras distintas** hay en
  el slot de creación y cuántos wallets distintos.
- **Umbral**: **≥3 compras en el slot de creación = bundle, sin discusión** (fuente: Scorp Trader).
  1 sola compra puede ser el dev buy normal.
- **Implementación**: primera página de `getSignaturesForAddress` del mint (las más viejas con
  paginado `before`), `getTransaction` de las del primer slot: dueños con Δtoken > 0 y ΔSOL < 0.

## 2. Supply acaparado en el bloque cero (cuánto duele)

- **Qué**: cuánto % del supply se llevó el bundle. La matemática de la curva (virtual 30 SOL /
  1.073.000.000 tokens al inicio, supply 1.000.000.000) permite traducir SOL gastado en % de supply.
- **Umbrales** (Scorp Trader): **<5%** normal · **5-15%** bloque insider (entrar chico) ·
  **15-25%** un solo actor mueve el precio (saltar) · **>25%** los insiders son dueños, vos sos la salida.
- Referencia: **10 SOL en el bloque cero ≈ 26,6% del supply**, y con ~0,6 SOL de compra orgánica el
  bundler ya está en break-even → puede dumpear casi gratis.
- **Implementación**: sumar el SOL de las compras del punto 1 y convertir con las reservas virtuales.

## 3. Clustering por fuente de fondeo (la señal sybil más fiable)

- **Qué**: varios wallets del mismo token financiados desde **la misma wallet de origen**.
- **Cómo se mide**: para cada holder/comprador, traer las transferencias de SOL **entrantes** y
  agrupar por remitente.
- **Parámetros medidos** (skills de sybil-detection): `MIN_CLUSTER_SIZE = 3` wallets,
  `MIN_SOL_FUNDING = 0.001 SOL`, ventana de fondeo corta.
- **Heurística completa de bubble-map**: *N wallets financiados por la misma wallet upstream, en una
  ventana corta de tiempo, con saldos parecidos, y poca actividad independiente* = bundle coordinado.
- **Ojo — los tres ataques que SÍ rompen el clúster** (pumpsmart): reutilizar wallets entre contextos,
  romper la correlación temporal y diluir con actividad independiente. Un clúster “limpio” puede ser
  falso negativo si el operador hizo eso.
- **Implementación**: en las tx de las compras del punto 1, mirar quién le mandó SOL a cada comprador
  (mismo ΔSOL negativo del otro lado); funder repetido ≥3 → clúster.

## 4. Co-firing / anillos persistentes (la técnica de los papers)

- **Qué**: wallets que **vuelven a aparecer juntas** como compradoras tempranas en muchos
  lanzamientos distintos. No es un clúster de un token: es un **anillo persistente**.
- **Cómo se mide** (arXiv 2607.02795, pipeline de dos etapas): (1) extraer la ventana de
  primeros compradores de cada lanzamiento; (2) **union-find sobre el grafo de co-ocurrencia
  cross-launch** → el paper detectó **1.012 anillos** de 2-12 wallets.
- **Umbral**: aparecer juntas en **≥2-3 lanzamientos** ya es señal; 2-12 wallets por anillo es lo típico.
- **Implementación**: acumular por token el set de primeros compradores, y contar co-ocurrencias
  (intersección de sets) entre tokens. Es barato y poderoso: no necesita datos nuevos.

## 5. Detección de bundle por transacción única

- **Qué**: **una sola transacción** que reparte compras a varios wallets (1 firmante, varias
  transferencias del mismo mint a destinatarios distintos).
- **Cómo se mide**: `meta.postTokenBalances` con ≥2 dueños distintos que **suben** del mismo mint y
  un firmante/pagador común.
- **Implementación**: en las tx del mint, contar dueños con Δtoken>0 por transacción.

## 6. Señales agregadas del proveedor (rápidas, no verificables)

GMGN expone por token (vía Noesis/otros): `insider_count`, `bundler_count`, `sniper_count`,
`fresh_wallet_count`, `top_bundler_trader_percentage`, `top_entrapment_trader_percentage`,
`top_rat_trader_percentage`, `dev_team_hold_rate`, `top_10_holder_rate`.
- Sirven como **prefiltro**; la propia doc avisa que son **agregados del proveedor, no prueba wallet
  por wallet**: nunca presentarlos como relaciones verificadas.
- RugCheck tiene endpoint de grafo de insiders: `GET /v1/tokens/{id}/insiders/graph` (visto en la doc
  de FluxRPC) — **pendiente de probar** contra `api.rugcheck.xyz` (los `/insiders` planos daban 404).

## 7. Anti-patrones y límites (para no vender humo)

- **El patrón “15 wallets frescas fondeadas desde un master, 25-40% del supply” NO se encontró** al
  trazarlo en 323 lanzamientos de pump.fun (repo solana-safe-sniper-mcp-template). No repetirlo como
  si fuera la regla.
- **“Smart money” pública puede ser carnada**: hay wallets etiquetadas a propósito para atraer
  copiadores y venderles (GMGN).
- **Muchas estrategias de los que ganan son irreplicables**: scalping de alta frecuencia, acceso al
  dev, moonshots raros (GMGN). Coincide con lo medido en este proyecto: los que ganan son bots de
  1-9 minutos.
- **Corrección metodológica del paper**: los registros de “buyers” incluían ventas y perdían compras
  → todas sus cifras quedaron retiradas. **La clasificación compra/venta hay que validarla siempre**
  (es el mismo error que evitamos al mirar Δ SOL vs Δ token, y al usar tx v1).

## 8. Tasa base para no engañarse

- **98,6%** de los tokens de pump.fun terminaron en colapso/rug y sólo **1,4%** mantuvo ≥1.000 USD de
  liquidez (Solidus Labs 2025, vía OAK).
- El cohorte “Rug Republic” (12 clústeres) orquestó ~**82%** de los drenajes de liquidez de su cohorte:
  pocos operadores, mucho daño → vale la pena cazar clústeres.

---

## Estado de implementación (2026-10-07)

`insider_token.py <mint>` implementa las técnicas 1, 2 y 3 (compras del slot de creación, % del supply
con el supply crudo convertido por decimales, y clúster de fondeo del bloque cero), más el veredicto
`bundle` y la lectura por umbral. **Probado: detecta el dev buy en lanzamientos frescos y marca “sin
bundle” cuando hay una sola compra** (~0,2-0,5 SOL = <1 % del supply en los 4 tokens del radar que se
pudieron leer).

**Dos quiebres de datos que hay que resolver antes de confiar en él para todo token:**

1. **En tokens ya graduados, el historial del MINT no llega al lanzamiento.** Las primeras tx del mint
   son de PumpSwap (post-graduación): las compras de la curva no listan el mint como cuenta. La ventana
   del arranque hay que leerla de la **cuenta de la curva** (PDA de `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`
   con seeds `[b"bonding-curve", mint]`), y eso requiere derivar PDAs → instalar `solders`/`solana-py`.
   Alternativa: usar el campo `bonding_curve` del API de pump.fun cuando el token sigue en las listas
   del día (`PA.buscar_coin`), que hoy devuelve stub si ya salió de ellas.
2. **Algunas tx devuelven los balances de token sin `uiTokenAmount` útil** (`uiAmount: null`,
   `amount: "0"`). Hay que sacar el monto de la compra de las **instrucciones/logs** (el `BuyExactQuoteIn`
   de PumpSwap o el `buy` de la curva traen el monto) en vez de los balances.

**Pendiente además**: el endpoint de grafo de insiders de RugCheck (`/v1/tokens/{id}/insiders/graph`,
visto en la doc de FluxRPC) sin probar contra `api.rugcheck.xyz` — puede dar las wallets del clúster
directamente y ahorrar todo el trabajo de fondeo.

**Siguiente paso operativo**: engancharlo al radar para que **cada token alertado traiga el veredicto
“¿bundle/insider?” automático** (ahí el valor es inmediato: saber si el lanzamiento está cargado antes
de mirar el gráfico).


## Fuentes

- GitHub `agiprolabs/claude-trading-skills` → `skills/sybil-detection` (SKILL.md,
  `references/clustering_methods.md`, `references/bundler_detection.md`, `scripts/detect_sybils.py`):
  parámetros y algoritmos de clustering por fondeo, bundles por slot y por tx única.
- arXiv **2607.02795** — “Coordinated Sniper Cohorts on Pump.fun” (union-find cross-launch, 1.012 anillos).
- arXiv **2609.10246** — “Meme Coin Factories”; arXiv **2602.13480** — MELT (trazas de lanzamientos de riesgo).
- GitHub `onchainattack/oak` → taxonomía **OAK-T3.001 “Sybil-Bundled Launch”** (Jito + 16-25 wallets
  del deployer en el mismo slot que `initialize_pool`) y el cohorte “Rug Republic”.
- `trader.scorplabs.online/blog/pump-fun-bundle-detection` — umbrales de supply acaparado y la
  matemática de la curva.
- `pumpsmart.org/articles/bubble-maps-wallet-clustering-explained.html` — heurística de bubble maps y
  los tres ataques que rompen el clúster.
- `noesisapi.dev/guides/detect-bundles-solana` — agregados de GMGN y sus límites.
- GitHub `paulf280-ui/solana-safe-sniper-mcp-template` (anti-patrón desmentido),
  `NerdHerderDani/flurry` (forense de lanzamientos), `cipherzeroprotocol/solana-guard`,
  `GMGNAI/gmgn-skills` (track de smart money por API con key).
