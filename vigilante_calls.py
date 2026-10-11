#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIGILANTE DE CALLS EN VIVO (WebSocket) — la ráfaga se detecta en segundos, no cada 10 minutos.

Cómo funciona:
  1. Lee la lista de **tokens jóvenes** (`tokens_jovenes.json`, la escribe el radar) con su pool.
  2. Se **suscribe por WebSocket** (Helius, incluido en el plan) a los logs de cada pool:
     cada trade del pool dispara una notificación con su firma.
  3. Guarda las notificaciones en una ventana móvil por token. Cuando en `VENTANA` segundos
     aparecen `DISPARO` trades o más, **baja esas transacciones** (gasto controlado) y cuenta:
       · wallets distintas que COMPRARON, cuánto SOL pusieron
       · si hay compras en el mismo slot (bundle)
       · si las carteras son nuevas o conocidas
  4. Si hay ≥ `MIN_WALLETS` wallets con ≥ `MIN_SOL` → **manda el aviso al canal** con el formato de
     siempre (bloque de contrato, análisis del token, etc.).

Por qué así: suscribirse SOLO al pool de cada token joven cuesta una notificación por trade (gratis)
y únicamente se bajan transacciones cuando hay un pico → unas 10-20 veces más barato que sondear.

Uso:
  python3 vigilante_calls.py                 # servicio en vivo (lo levanta el cron si no corre)
  python3 vigilante_calls.py --probar <mint> --segundos 60   # prueba: escucha ese pool y cuenta
  python3 vigilante_calls.py --una-vuelta    # arranca, revisa la lista y sale (para diagnóstico)
"""
import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT          # noqa: E402
import auditor_wallets as AU         # noqa: E402
import calls_detector as CD          # noqa: E402
import pump_analizador as PA         # noqa: E402

LISTA = os.path.join(AQUI, "tokens_jovenes.json")
ESTADO = os.path.join(AQUI, "vigilante_estado.json")
LOG = os.path.join(AQUI, "logs", "vigilante_calls.log")
VENTANA = 150          # segundos de la ventana de ráfaga
DISPARO = 4            # trades en los últimos 60 s que disparan la evaluación (era 8: más sensible)
MIN_WALLETS = 2        # 2 wallets alcanzan para avisar (era 3)
MIN_SOL = 0.1          # mínimo por wallet más bajo (era 0.3)
MAX_POOLS = 80         # hasta 80 tokens vigilados a la vez (era 40)
REFRESCO_LISTA = 30    # releo la lista cada 30 s (era 60)
ENFRIAMIENTO = 900     # tras avisar de un token, no volver a avisarlo por 15 min
TOPE_DIARIO = 300_000      # techo diario del vigilante. Con el disparo en 4 trades y 80 tokens
#                           evalúa cientos de veces por hora: sin techo se lleva el cupo en horas
TOPE_EVAL_HORA = 10_000    # SIN tope práctico de evaluaciones por hora (era 8)
EVAL_POR_TOKEN = 120   # segundos mínimos entre evaluaciones del mismo token (era 300)
TX_POR_EVAL = 20       # transacciones a bajar por evaluación. Se miran las del ARRANQUE de la
                       # ráfaga (las últimas suelen ser ventas del desagote y hacían que nunca se
                       # vieran 3 compradores: medido, 314 evaluaciones y 0 avisos con TX=6)


def log(m):
    """Escribe en stdout (el servicio y el cron redirigen a logs/vigilante_calls.log).
    OJO: no escribir también al archivo desde acá — con la redirección activa cada línea salía doble.
    """
    print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {m}", flush=True)


def clave_helius():
    for l in open(os.path.expanduser("~/.hermes/.env")):
        if l.startswith("HELIUS_API_KEY="):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _estado_leer():
    try:
        return json.load(open(ESTADO))
    except Exception:
        return {}


def _estado_guardar(d):
    try:
        tmp = ESTADO + ".tmp"
        json.dump(d, open(tmp, "w"))
        os.replace(tmp, ESTADO)
    except Exception:
        pass


def gasto_hoy():
    d = _estado_leer()
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if d.get("dia") != hoy:
        return 0
    return int(d.get("creditos", 0))


def anotar_gasto(n):
    d = _estado_leer()
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if d.get("dia") != hoy:
        d = {"dia": hoy, "creditos": 0}
    d["creditos"] = int(d.get("creditos", 0)) + n
    _estado_guardar(d)


def tokens_jovenes():
    """Lee la lista que mantiene el radar: [{mint, pool, simbolo, creado}]."""
    try:
        d = json.load(open(LISTA))
        return [t for t in d if t.get("pool") and t.get("mint")][:MAX_POOLS]
    except Exception:
        return []


async def bajar_y_evaluar(pool, mint, firmas, contexto):
    """Baja las transacciones del pico y decide si es una call. Devuelve la ráfaga o None."""
    if gasto_hoy() > TOPE_DIARIO:
        log("  presupuesto diario agotado: no bajo transacciones")
        return None
    compras, slots = [], []
    for sig in firmas[:TX_POR_EVAL]:      # el ARRANQUE de la ráfaga (ahí están las compras)
        try:
            tx = await asyncio.to_thread(WT.rpc, "getTransaction",
                                         [sig, {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                "encoding": "jsonParsed"}])
        except Exception:
            tx = None
        anotar_gasto(10)
        if not tx or not tx.get("meta") or tx["meta"].get("err"):
            continue
        meta = tx["meta"]
        try:
            keys = await asyncio.to_thread(WT.cuentas_tx, tx)
        except Exception:
            continue
        pre_b, post_b = meta.get("preBalances") or [], meta.get("postBalances") or []
        pre_t, post_t = {}, {}
        for b in meta.get("preTokenBalances") or []:
            if b.get("mint") == mint and b.get("owner"):
                pre_t[b["owner"]] = pre_t.get(b["owner"], 0.0) + float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        for b in meta.get("postTokenBalances") or []:
            if b.get("mint") == mint and b.get("owner"):
                post_t[b["owner"]] = post_t.get(b["owner"], 0.0) + float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        for owner in set(pre_t) | set(post_t):
            if owner in AU.AU_IGNORAR or owner not in keys:
                continue
            d_tok = post_t.get(owner, 0.0) - pre_t.get(owner, 0.0)
            if d_tok <= 0:
                continue
            i = keys.index(owner)
            d_sol = (post_b[i] - pre_b[i]) / 1e9 if (i < len(pre_b) and i < len(post_b)) else 0.0
            if d_sol <= -0.2:
                compras.append({"wallet": owner, "sol": -d_sol, "ts": tx.get("blockTime") or 0})
                slots.append(tx.get("slot"))
    fuertes = [c for c in compras if c["sol"] >= MIN_SOL]
    wallets = {c["wallet"] for c in fuertes}
    if len(wallets) < MIN_WALLETS:
        return None
    # Sin candado de fuerza (pedido del usuario): se avisa siempre y el PUNTAJE hace de filtro.
    sol_total = sum(c["sol"] for c in fuertes)
    tss = [c["ts"] for c in fuertes if c["ts"]]
    cuenta_slots = {}
    for sl in slots:
        cuenta_slots[sl] = cuenta_slots.get(sl, 0) + 1
    raf = {"n_wallets": len(wallets), "sol": round(sum(c["sol"] for c in fuertes), 2),
           "segundos": (max(tss) - min(tss)) if len(tss) > 1 else 0,
           "hora": datetime.fromtimestamp(min(tss), timezone.utc).strftime("%H:%M") + " UTC" if tss else "?",
           "ts_iso": datetime.fromtimestamp(min(tss), timezone.utc).isoformat() if tss else None,
           "slots_iguales": max(cuenta_slots.values()) if cuenta_slots else 1,
           "compras_en_ventana": len(compras), "pool": pool, "wallets": sorted(wallets),
           "en_vivo": True}
    cono = CD.conocidas()
    frescas = conocidas = 0
    for w in wallets:
        if w in cono:
            conocidas += 1
        elif await asyncio.to_thread(CD.es_fresca, w):
            frescas += 1
        anotar_gasto(20)
    raf["frescas"] = frescas
    raf["conocidas"] = conocidas
    return raf


def puntuar_rafaga(raf, ctx):
    """Puntaje 0-10 de la ráfaga usando los mismos criterios que el detector."""
    return CD.puntuar(raf, ctx)


async def avisar(mint, simbolo, raf):
    """Analiza el token y manda el aviso con el mismo formato del radar."""
    try:
        coin = await asyncio.to_thread(PA.buscar_coin, mint)
        ev = await asyncio.to_thread(PA.analizar, coin, pedir=True)
    except Exception as e:
        log(f"  ! no pude analizar {simbolo}: {e}")
        return False
    ctx = ev.get("contexto") or {}
    edad = ctx.get("edad_min") or 0
    if edad < 10:
        log(f"  ráfaga descartada en {simbolo}: token de {edad:.0f} min (ventana de snipers)")
        return False
    nota, motivos, etq = CD.puntuar(raf, ctx)
    raf["nota"] = nota
    raf["motivos"] = motivos
    raf["etq"] = etq
    log(f"  → puntaje: {nota}/10 {etq}")
    ev["call"] = raf
    ev["por_rafaga"] = True
    ok = await asyncio.to_thread(PA.alertar_token, ev)
    log(f"  {'ENVIADA' if ok else 'FALLÓ'} la call de {simbolo} ({raf['n_wallets']} wallets, "
        f"{raf['sol']} SOL, {raf['segundos']}s, mismo slot {raf['slots_iguales']})")
    return ok


async def servicio(segundos=None):
    import websockets
    clave = clave_helius()
    if not clave:
        log("sin HELIUS_API_KEY en el .env")
        return 1
    url = f"wss://mainnet.helius-rpc.com/?api-key={clave}"
    buffers = defaultdict(lambda: deque(maxlen=400))
    pendiente, sub_a_pool, suscritos = {}, {}, {}     # id_de_peticion → mint · id_de_suscripción → mint
    prox_refresco, id_msg = 0.0, 1000
    enfriados, ultima_eval, evals_hora = {}, {}, []
    log(f"vigilante de calls EN VIVO · ventana {VENTANA}s · disparo {DISPARO} trades · "
        f"mínimo {MIN_WALLETS} wallets con {MIN_SOL} SOL")
    arranque = time.time()
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                          max_size=2 ** 22) as ws:
                log("  conectado al WebSocket de Helius")
                while True:
                    if time.time() >= prox_refresco or not suscritos:
                        prox_refresco = time.time() + REFRESCO_LISTA
                        tokens = tokens_jovenes()
                        for t in tokens:
                            # Se suscribe AL MINT (no al pool): el mint aparece en las tx tanto de la
                            # curva como del pool, así que cubre las dos etapas del token.
                            if t["mint"] in suscritos or len(suscritos) >= MAX_POOLS:
                                continue
                            id_msg += 1
                            pendiente[id_msg] = t["mint"]
                            await ws.send(json.dumps({"jsonrpc": "2.0", "id": id_msg,
                                                      "method": "logsSubscribe",
                                                      "params": [{"mentions": [t["mint"]]},
                                                                 {"commitment": "confirmed"}]}))
                            suscritos[t["mint"]] = {"mint": t["mint"], "pool": t.get("pool"),
                                                    "simbolo": t.get("simbolo") or "?"}
                        log(f"  suscripciones activas: {len(suscritos)} · tokens jóvenes en la lista: {len(tokens)}")
                    try:
                        mensaje = await asyncio.wait_for(ws.recv(), timeout=REFRESCO_LISTA)
                    except asyncio.TimeoutError:
                        continue
                    d = json.loads(mensaje)
                    # 1) respuesta a la suscripción: el servidor devuelve el id de suscripción
                    if "id" in d and d["id"] in pendiente:
                        pool = pendiente.pop(d["id"])
                        if isinstance(d.get("result"), int):
                            sub_a_pool[d["result"]] = pool
                        continue
                    # 2) notificación de trade: me dice el id de suscripción → de ahí saco el pool
                    if d.get("method") == "logsNotification":
                        params = d.get("params") or {}
                        pool = sub_a_pool.get(params.get("subscription"))
                        val = (params.get("result") or {}).get("value") or {}
                        sig, err = val.get("signature"), val.get("err")
                        if not pool or not sig or err:
                            continue
                        buffers[pool].append((time.time(), sig))
                        ahora = time.time()
                        while buffers[pool] and ahora - buffers[pool][0][0] > VENTANA:
                            buffers[pool].popleft()
                        info = suscritos.get(pool) or {}
                        mint, simbolo = info.get("mint"), info.get("simbolo")
                        if not mint:
                            continue
                        # Ritmo reciente: una call de verdad es una ráfaga SOSTENIDA de trades.
                        ritmo = sum(1 for t, _ in buffers[pool] if ahora - t <= 60)
                        if ritmo < DISPARO:
                            continue
                        if (ahora - enfriados.get(pool, 0) < ENFRIAMIENTO
                                or ahora - ultima_eval.get(pool, 0) < EVAL_POR_TOKEN):
                            continue
                        # Freno de gasto GLOBAL: como mucho TOPE_EVAL_HORA evaluaciones por hora en todo
                        # el sistema (cada una cuesta ~60-80 créditos). Sin esto, un token caliente se
                        # evalúa en cada trade y se come el cupo en minutos.
                        evals_hora[:] = [t for t in evals_hora if ahora - t < 3600]
                        if len(evals_hora) >= TOPE_EVAL_HORA:
                            continue
                        evals_hora.append(ahora)
                        ultima_eval[pool] = ahora
                        firmas = [s for _, s in buffers[pool]]
                        log(f"  pico en {simbolo}: {ritmo} trades en 60s ({len(firmas)} en {VENTANA}s) "
                            f"→ evaluando… (eval {len(evals_hora)}/{TOPE_EVAL_HORA} de esta hora)")
                        raf = await bajar_y_evaluar(pool, mint, firmas, None)
                        if raf:
                            await avisar(mint, simbolo, raf)
                            enfriados[pool] = ahora
                            buffers[pool].clear()
                    if segundos and (time.time() - arranque) > segundos:
                        return 0
        except Exception as e:
            log(f"  ! conexión caída ({str(e)[:80]}): reintento en 5s")
            await asyncio.sleep(5)


def main():
    ap = argparse.ArgumentParser(description="Vigilante de calls en vivo (WebSocket Helius)")
    ap.add_argument("--probar", help="escuchar este pool (mint) y contar notificaciones")
    ap.add_argument("--segundos", type=int, default=0, help="cortar después de N segundos")
    ap.add_argument("--una-vuelta", action="store_true", help="revisar la lista y salir")
    a = ap.parse_args()
    if a.una_vuelta:
        t = tokens_jovenes()
        log(f"tokens jóvenes en la lista: {len(t)}")
        for x in t[:10]:
            log(f"   · {x.get('simbolo')} · pool {str(x.get('pool'))[:12]} · {x.get('mint')[:12]}")
        return 0
    if a.probar:
        pool = a.probar
        import websockets

        async def escuchar():
            clave = clave_helius()
            url = f"wss://mainnet.helius-rpc.com/?api-key={clave}"
            cnt = 0
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                          "params": [{"mentions": [pool]}, {"commitment": "confirmed"}]}))
                log("escuchando el pool (prueba)…")
                t0 = time.time()
                while a.segundos == 0 or time.time() - t0 < a.segundos:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        log("  (sin actividad en 30s)")
                        continue
                    d = json.loads(msg)
                    if d.get("method") == "logsNotification":
                        val = (d.get("params") or {}).get("result", {}).get("value") or {}
                        cnt += 1
                        if cnt <= 8:
                            logs = val.get("logs") or []
                            marca = next((l for l in logs if "Instruction" in l), "")
                            log(f"  trade #{cnt} · {str(val.get('signature'))[:16]} · {marca[:60]}")
                log(f"total de notificaciones en la prueba: {cnt}")
        asyncio.run(escuchar())
        return 0
    asyncio.run(servicio(a.segundos or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
