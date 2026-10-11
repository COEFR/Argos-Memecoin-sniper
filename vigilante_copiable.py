#!/usr/bin/env python3
"""VIGÍA EN VIVO — avisa apenas una wallet COPIABLE compra (segundos, no minutos).

Por qué existe: medido el 09/10, los avisos llegaban ~4 minutos después de la compra del grupo (la
patrulla corre cada 3 min y el análisis suma ~1 min más). Acá se ESCUCHA la cadena en vivo (WebSocket
de Helius) sobre las wallets que dan plata: la compra aparece en el bloque y el aviso sale en segundos.

Cómo gasta poco:
  · se suscribe SÓLO a las wallets copiables (~20), no a las 300 del sistema;
  · filtra los mensajes mirando el log de la transacción (pump.fun escribe "Instruction: Buy"),
    así no gasta una consulta por cada movimiento que no es compra;
  · deduplica por token y respeta los topes por canal del sistema.

La patrulla de cada 3 min queda como red de seguridad (si el WebSocket se cae, igual avisa).

Uso:  python3 vigilante_copiable.py           (corre hasta que lo maten)
      python3 vigilante_copiable.py --probar  (15 s en seco: muestra lo que llega, sin avisar)
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import cohortes as CO        # noqa: E402
import pump_analizador as PA  # noqa: E402

VISTO = os.path.join(AQUI, "vigia_visto.json")
LOG = os.path.join(AQUI, "logs", "vigilante_copiable.log")
# Programas de exchange: si la transacción toca alguno, vale la pena parsearla (para saber si compró).
# El texto "Instruction: Buy" de pump.fun no alcanza: muchos bots compran por PumpSwap/Jupiter/Raydium.
PROGRAMAS_DEX = (
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",     # pump.fun (curva)
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",     # PumpSwap (AMM de pump.fun)
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",     # Jupiter
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",     # Raydium v4
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",     # Raydium CLMM
    "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",     # bot Flash (medido hoy)
)
PATRON_COMPRA = ("Instruction: Buy", "Instruction: buy")
PATRON_VENTA = ("Instruction: Sell", "Instruction: sell")


def log(m):
    linea = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {m}"
    print(linea, flush=True)


def _sol_salido(meta, tx, wallet):
    """Cuánto SOL salió de esa wallet en esa transacción (para distinguir compra de regalo)."""
    try:
        claves = [k.get("pubkey") if isinstance(k, dict) else k
                  for k in (((tx.get("message") or {}).get("accountKeys")) or [])]
        i = claves.index(wallet)
        return (meta["preBalances"][i] - meta["postBalances"][i]) / 1e9
    except Exception:
        return 0.0


def copiables():
    """Las wallets con PnL de copia positivo (las que dan plata)."""
    try:
        d = json.load(open(os.path.join(AQUI, "wallets_puntaje.json")))
    except Exception:
        return {}
    return {w: v for w, v in (d.get("wallets") or {}).items()
            if v.get("tier_wallet") == "🟢 COPIABLE"}


def _keys():
    """Las claves de Helius (del .env), rotando."""
    out = []
    for l in open(os.path.expanduser("~/.hermes/.env")):
        if l.startswith("HELIUS_API_KEY"):
            v = l.split("=", 1)[1].strip().strip('"').strip("'")
            if v:
                out.append(v)
    return out


def _vistos():
    try:
        return json.load(open(VISTO))
    except Exception:
        return {}


def _guardar_vistos(d):
    import jsonio
    jsonio.guardar(VISTO, {k: v for k, v in d.items() if time.time() - v < 24 * 3600})


MIN_SOL = 0.1            # por debajo de esto es polvo: no vale un aviso (medido: muchas compras de 0.01)
COOLDOWN_WALLET = 180    # segundos entre avisos de la MISMA wallet (hay wallets que compran 50 veces/min)
TOPE_HORA = 10           # avisos por hora en el canal de seguir (mismo espíritu que la patrulla)
_ultimo = {}             # wallet -> ts del último aviso


_enviados_hora = []


def _freno(w, canal=None):
    """(se puede, motivo): cooldown por wallet + tope de avisos por hora.

    Hace falta: medido el 09/10, una sola wallet copiable compró 50 veces en 55 segundos — sin frenos,
    el canal sería un río y un aviso dejaría de significar algo.
    """
    ahora = time.time()
    if ahora - _ultimo.get(w, 0) < COOLDOWN_WALLET:
        return False, f"esa wallet ya avisó hace {int(ahora - _ultimo.get(w, 0))} s"
    while _enviados_hora and ahora - _enviados_hora[0] > 3600:
        _enviados_hora.pop(0)
    if len(_enviados_hora) >= TOPE_HORA:
        return False, f"tope de {TOPE_HORA} avisos/hora alcanzado"
    _enviados_hora.append(ahora)
    return True, ""


def avisar(w, sig, perfil, token_mint, sol, pedido_ts):
    """Manda el aviso de compra de una wallet copiable (formato compacto + contrato aparte)."""
    _ultimo[w] = time.time()
    ev = PA.analizar(PA.buscar_coin(token_mint), pedir=True)
    ctx = ev.get("contexto") or {}
    # la línea de wallets: sólo ésta compró (es un aviso de wallet, no de grupo)
    linea = (f"👤 🟢`{w[:8]}…` **{perfil.get('pnl_copia', 0):+.1f}** "
             f"({perfil.get('hits', 0)} ganadoras/{perfil.get('n', 0)} entradas)")
    n = 1
    raf = {
        "nota": perfil.get("nota") or 0, "etq": "🟢 COPIABLE", "n_wallets": n, "sol": round(sol, 2),
        "segundos": 0, "slots_iguales": 1, "frescas": 0, "conocidas": 1, "compras_en_ventana": 1,
        "cluster": True, "wallets": [w], "es_cohorte": True,
        "nombre_grupo": f"wallet copiable {w[:6]}…", "tamano_grupo": 1,
        "linea_wallets": linea,
        "roster": [{"wallet": w, "sol": round(sol, 2), "nivel": "origen",
                    "nota": perfil.get("nota"), "medidas": perfil.get("n")}],
        "motivos": [f"**⚡ aviso EN VIVO** · detectado en segundos (no esperó la patrulla de 3 min)",
                    f"wallet con PnL de copia **+{perfil.get('pnl_copia', 0):.1f}** y "
                    f"{perfil.get('hits', 0)} ganadoras cazadas de {perfil.get('n', 0)} entradas"],
        "hora": datetime.now(timezone.utc).strftime("%H:%M:%S") + " UTC",
        "ts_iso": datetime.now(timezone.utc).isoformat(),
    }
    nota_call, motivos_call = CO.calidad_call(raf, ctx)
    raf["nota_calidad"] = nota_call
    raf["motivos_calidad"] = motivos_call
    raf["percentil_calidad"] = 99          # en vivo va al canal de las mejores si la calidad lo permite
    ev["call"] = raf
    ev["por_rafaga"] = True
    canal = None
    mejores = CO.canal_mejores_id()
    if nota_call >= 9 and mejores:
        canal = mejores
    else:
        canal = CO.canal_para("🟢 SEGUIR") or CO.canal_argos()
    log(f"  ⚡ {ev.get('simbolo')} · wallet {w[:10]}… · {sol:.2f} SOL · calidad {nota_call}/10 "
        f"· tardó {time.time() - pedido_ts:.1f} s desde la compra")
    try:
        import registro_calls as RC
        RC.registrar(raf, ev)
    except Exception:
        pass
    PA.alertar_token(ev, "⚡ WALLET COPIABLE COMPRANDO", canal=canal)


async def servicio(probar=False, segundos=0):
    import websockets
    claves = _keys()
    if not claves:
        log("! no hay claves de Helius en el .env")
        return 1
    perfiles = copiables()
    if not perfiles:
        log("! no hay wallets copiables todavía")
        return 1
    wallets = list(perfiles)
    log(f"vigía en vivo: {len(wallets)} wallets copiables · claves: {len(claves)}")
    visto = _vistos()
    cont = {"msgs": 0, "dex": 0, "no_compra": 0, "compras": 0}
    ultimo_guardado = time.time()
    fin = time.time() + segundos if segundos else None
    i = 0
    while True:
        clave = claves[i % len(claves)]
        i += 1
        url = f"wss://mainnet.helius-rpc.com/?api-key={clave}"
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                          max_size=2 ** 22) as ws:
                # SUSCRIPCIÓN CON LA TRANSACCIÓN INCLUIDA (Helius, transactionSubscribe): el aviso trae
                # la transacción ya parseada, así no hay que pedirla aparte — y sobre todo no se pierde
                # por el retraso del nodo (con logsSubscribe la lectura inmediata devolvía vacío: se midió
                # 269 avisos de exchange y CERO compras detectadas por eso). Una sola suscripción cubre
                # todas las wallets con `accountInclude`.
                # Una suscripción por wallet (logsSubscribe con mentions) y se guarda el mapa
                # suscripción → wallet: en los avisos siguientes NO viene la dirección, sólo el id.
                suscripciones = {}
                for j, w in enumerate(wallets, 1):
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": j, "method": "logsSubscribe",
                        "params": [{"mentions": [w]}, {"commitment": "confirmed"}]}))
                    try:
                        conf = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                        if conf.get("result"):
                            suscripciones[conf["result"]] = w
                    except Exception:
                        pass
                log(f"  {len(suscripciones)} suscripciones activas")
                log(f"  suscrito a {len(wallets)} wallets")
                while True:
                    if fin and time.time() > fin:
                        _guardar_vistos(visto)
                        log(f"  (prueba terminada) · mensajes {cont['msgs']} · tocaron exchange "
                            f"{cont['dex']} · no eran compra {cont['no_compra']} · COMPRAS {cont['compras']}")
                        return 0
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        try:
                            await ws.ping()
                        except Exception:
                            break
                        continue
                    msg = json.loads(raw)
                    params = (msg.get("params") or {})
                    res = params.get("result") or {}
                    valor = res.get("value") or {}       # los avisos de logsSubscribe vienen así
                    sig = valor.get("signature")
                    if not sig:
                        continue
                    cont["msgs"] += 1
                    logs = " ".join(valor.get("logs") or [])
                    # filtro barato por log: SÓLO se consulta la transacción si parece una compra
                    if not any(p in logs for p in PATRON_COMPRA):
                        continue
                    if any(p in logs for p in PATRON_VENTA):
                        continue
                    w = suscripciones.get(params.get("subscription"))
                    if not w or visto.get(sig):
                        continue
                    pedido_ts = time.time()
                    visto[sig] = pedido_ts
                    cont["dex"] += 1
                    compra = CO.compra_de_wallet(sig, w)
                    if not compra:                    # reintento: cubre el 429 del límite de pedidos
                        time.sleep(0.4)
                        compra = CO.compra_de_wallet(sig, w)
                    if not compra:
                        cont["no_compra"] += 1
                        continue
                    cont["compras"] += 1
                    mint, sol = compra
                    if sol < MIN_SOL:
                        cont["polvo"] = cont.get("polvo", 0) + 1
                        continue
                    _puede, _motivo = _freno(w, None)
                    if not _puede:
                        cont["frenadas"] = cont.get("frenadas", 0) + 1
                        continue
                    if probar:
                        log(f"  [PRUEBA] compra detectada: {w[:10]}… → {mint[:12]}… ({sol} SOL) "
                            f"en {time.time() - pedido_ts:.1f} s")
                        continue
                    try:
                        avisar(w, sig, perfiles.get(w) or {}, mint, sol, pedido_ts)
                    except Exception as e:
                        log(f"  ! error avisando: {e}")
                    if time.time() - ultimo_guardado > 20:
                        _guardar_vistos(visto)
                        ultimo_guardado = time.time()
        except Exception as e:
            log(f"  ! conexión caída ({str(e)[:70]}) — reintento en 5 s")
        if fin and time.time() > fin:
            return 0
        await asyncio.sleep(5)


def main():
    ap = argparse.ArgumentParser(description="Vigía en vivo de las wallets copiables")
    ap.add_argument("--probar", action="store_true", help="15 s en seco, sin avisar")
    ap.add_argument("--segundos", type=int, default=0, help="corta a los N segundos (0 = infinito)")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    async def correr(_):
        return await servicio(probar=a.probar, segundos=a.segundos or (15 if a.probar else 0))
    return asyncio.run(correr(None))


if __name__ == "__main__":
    sys.exit(main())
