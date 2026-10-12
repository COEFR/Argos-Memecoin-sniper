#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analizador de memecoins de pump.fun — ¿vale la pena o no?

Junta el contexto de un token desde tres fuentes gratuitas y sin API key:
  · pump.fun  (frontend-api-v3)  -> curva de fondeo, market cap, creador, social, actividad
  · RugCheck  (api.rugcheck.xyz) -> autoridades, holders, insiders, lockers, score
  · DexScreener (api.dexscreener) -> liquidez real, volumen, compras/ventas, boosts

Y devuelve un veredicto en llano:  VALE LA PENA / DUDOSO / NO  + el porqué.

Uso:
  python3 pump_analizador.py --mint <mint>          # analiza un token puntual
  python3 pump_analizador.py --scan 40              # escanea los últimos N lanzamientos
  python3 pump_analizador.py --scan 60 --top 8      # y muestra los mejores
  python3 pump_analizador.py --mint <mint> --json   # salida JSON (para el tracker)
  python3 pump_analizador.py --scan 40 --alerta     # manda por Telegram/Discord lo que pase el umbral

Cero tokens de LLM: todo son reglas explícitas y HTTP a APIs públicas.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import hashlib
from datetime import datetime, timezone

PUMP = "https://frontend-api-v3.pump.fun"
RUG = "https://api.rugcheck.xyz/v1"
DS = "https://api.dexscreener.com"
UA = "hermes-pump-analizador/1.0 (+uso personal)"
GRAD_SOL = 85.0            # la curva se llena a ~85 SOL recaudados (equivale a ~69k USD de mcap)
FEE_LADO = 1.25            # % por lado en la curva (pump.fun/docs/fees: creator 0.30 + protocol 0.95). Ida y vuelta ~2.5%.
SOL_USD_FALLBACK = 200.0   # solo para estimar si el API no da el precio del SOL

# ---------------------------------------------------------------- utilidades

def log(msg):
    print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def http_json(url, timeout=20, tries=3, method="GET", data=None):
    """GET/POST JSON con reintentos. Devuelve (dict|list) o None si falla."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"},
                                         method=method, data=data)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                cuerpo = r.read().decode("utf-8", "replace")
            return json.loads(cuerpo)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                log(f"  http {e.code} en {url.split('?')[0]}")
                return None
            time.sleep(1.5 * (i + 1))
        except Exception as e:
            if i == tries - 1:
                log(f"  ! fallo {url.split('?')[0]}: {e}")
            time.sleep(1.5 * (i + 1))
    return None


def edad_min(ts_ms):
    if not ts_ms:
        return None
    return (time.time() - ts_ms / 1000.0) / 60.0


def fmt_edad(m):
    if m is None:
        return "?"
    if m < 60:
        return f"{m:.0f} min"
    if m < 48 * 60:
        return f"{m/60:.1f} h"
    return f"{m/1440:.1f} días"


def usd(x):
    if x is None:
        return "?"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.1f}k"
    return f"${x:.0f}"


# ------------------------------------------------------------------ fuentes

def pump_nuevos(limit=60):
    """Últimos lanzamientos (todos, incluidos los que aún no tienen un solo trade)."""
    d = http_json(f"{PUMP}/coins?limit={limit}&sort=created_timestamp&order=DESC&includeNsfw=true")
    return d if isinstance(d, list) else []


def pump_live(limit=60):
    d = http_json(f"{PUMP}/coins/currently-live?limit={limit}&includeNsfw=true")
    return d if isinstance(d, list) else []


def pump_movidos(limit=60):
    """Los que están moviendo ahora (por último trade) — sirve para descubrir runners."""
    d = http_json(f"{PUMP}/coins?limit={limit}&sort=last_trade_timestamp&order=DESC&includeNsfw=true")
    return d if isinstance(d, list) else []


def rugcheck(mint):
    return http_json(f"{RUG}/tokens/{mint}/report", timeout=25)


def buscar_coin(mint):
    """Busca el token en las listas de pump.fun (en vivo, moviéndose o recién creado).
    Si no aparece, devuelve un stub mínimo: igual se analiza con RugCheck/DexScreener."""
    for fuente in (pump_live, pump_movidos, pump_nuevos):
        try:
            for c in fuente(120):
                if c.get("mint") == mint:
                    return c
        except Exception:
            continue
    return {"mint": mint, "name": mint[:8], "symbol": "?", "usd_market_cap": 0,
            "created_timestamp": None, "reply_count": 0}


def precio_sol():
    """Precio del SOL en USD (DexScreener, sin API key). Fallback fijo si falla."""
    d = http_json(f"{DS}/latest/dex/tokens/So11111111111111111111111111111111111111112", tries=2)
    for p in (d or {}).get("pairs") or []:
        if p.get("chainId") == "solana" and p.get("priceUsd"):
            try:
                return float(p["priceUsd"])
            except Exception:
                pass
    return SOL_USD_FALLBACK


def dexscreener(mint):
    d = http_json(f"{DS}/latest/dex/tokens/{mint}")
    pares = (d or {}).get("pairs") or []
    pares = [p for p in pares if p.get("chainId") == "solana"]
    if not pares:
        return None
    return max(pares, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


# --------------------------------------------------------------- el análisis

def analizar(coin, rc=None, ds=None, pedir=True, SOL_USD=SOL_USD_FALLBACK):
    """Devuelve el dict del análisis con puntaje, banderas y veredicto."""
    mint = coin.get("mint")
    if pedir:
        rc = rc if rc is not None else rugcheck(mint)
        ds = ds if ds is not None else dexscreener(mint)

    verdes, amarillas, rojas, notas = [], [], [], []
    puntos = 50.0

    # --- NOMBRE Y SÍMBOLO: si el token ya salió de las listas de pump.fun, `buscar_coin` devuelve un
    # stub ("?" y el mint cortado) y la alerta salía con "$?" y sin nombre real. DexScreener sí los
    # tiene: se completan desde el par.
    sym, nom = coin.get("symbol"), coin.get("name")
    _base = ((ds or {}).get("baseToken") or {})
    if (not sym or sym == "?" or (mint and str(sym).startswith(str(mint)[:6]))) and _base.get("symbol"):
        sym = _base.get("symbol")
    if (not nom or (mint and str(nom).startswith(str(mint)[:8]))) and _base.get("name"):
        nom = _base.get("name")
    if not sym and mint:
        sym = mint[:6]
    if not nom:
        nom = sym

    mc = coin.get("usd_market_cap") or coin.get("market_cap_usd") or 0
    # Si el token no está en las listas de pump.fun, el mcap y la edad se sacan del par real
    if not mc and ds:
        mc = ds.get("marketCap") or ds.get("fdv") or 0
    if not coin.get("created_timestamp") and ds and ds.get("pairCreatedAt"):
        coin = dict(coin, created_timestamp=ds["pairCreatedAt"])
    sol_curve = (coin.get("real_sol_reserves") or 0) / 1e9
    progreso = min(sol_curve / GRAD_SOL, 1.5)
    completo = bool(coin.get("complete"))
    edad = edad_min(coin.get("created_timestamp"))
    reply = coin.get("reply_count") or 0
    tw = coin.get("twitter") or ""
    tg = coin.get("telegram") or ""
    web = coin.get("website") or ""
    social = sum(1 for x in (tw, tg, web) if x)

    # ---------- infraestructura: la curva, los pools y el creador NO son "holders"
    conocidas = (rc or {}).get("knownAccounts") or {}

    def es_infra(direccion):
        info = conocidas.get(direccion) or {}
        tipo = (info.get("type") or "").upper()
        nombre = (info.get("name") or "").lower()
        return tipo in ("AMM", "LOCKER", "CREATOR", "POOL", "BURN", "FEE") or any(
            k in nombre for k in ("pump fun", "pumpfun", "pool", "curve", "raydium", "burn", "locker", "creator"))

    # ---------- autoridades y contrato (lo que puede hacerte perder TODO)
    if rc:
        ta = rc.get("token") or {}
        if ta.get("mintAuthority"):
            rojas.append(f"mint authority ACTIVA ({ta['mintAuthority'][:8]}…) → pueden acuñar y diluirte")
            puntos -= 40
        else:
            verdes.append("mint authority revocada")
        if ta.get("freezeAuthority"):
            rojas.append("freeze authority ACTIVA → te pueden congelar la wallet y no podés vender")
            puntos -= 50
        else:
            verdes.append("freeze authority revocada")
        if (rc.get("tokenMeta") or {}).get("mutable"):
            amarillas.append("metadata mutable (el dev puede cambiar nombre/imagen)")
            puntos -= 5
        if rc.get("rugged"):
            rojas.append("RugCheck lo marca como RUGGED")
            puntos -= 60
        tt = (rc.get("tokenType") or "").lower()
        if "2022" in tt:
            amarillas.append(f"Token-2022 ({tt}): revisar transfer hook / permanent delegate")
            puntos -= 8
        if (rc.get("transferFee") or {}).get("pct"):
            pct = rc["transferFee"]["pct"]
            amarillas.append(f"transfer fee {pct}%")
            puntos -= min(20, pct * 4)
    else:
        amarillas.append("sin reporte de RugCheck (token muy nuevo o sin indexar)")
        puntos -= 8

    if coin.get("transfer_fee_bps"):
        amarillas.append(f"fee de transferencia {coin['transfer_fee_bps']/100:.2f}% (pump.fun)")
        puntos -= min(15, coin["transfer_fee_bps"] / 100)
    if coin.get("transfer_hook_program"):
        rojas.append("transfer hook activo (puede bloquear transferencias)")
        puntos -= 30
    if coin.get("is_banned"):
        rojas.append("marcado como baneado en pump.fun")
        puntos -= 30

    # ---------- concentración: quién tiene el supply
    top10 = None
    if rc and rc.get("topHolders"):
        todos = rc["topHolders"]
        infra_pct = sum((h.get("pct") or 0) for h in todos if es_infra(h.get("owner")))
        reales = [h for h in todos if not es_infra(h.get("owner")) and (h.get("pct") or 0) > 0.001]
        top10 = sum((h.get("pct") or 0) for h in reales[:10])
        if infra_pct > 1:
            notas.append(f"{infra_pct:.1f}% del supply está en la curva/pool/creador (infraestructura, no cuenta como ballena)")
        if top10 > 35:
            rojas.append(f"top 10 holders con {top10:.1f}% del supply (>35%) → un dump coordinado te borra")
            puntos -= 30
        elif top10 > 20:
            amarillas.append(f"top 10 holders con {top10:.1f}% (20-35%: riesgo medio)")
            puntos -= 12
        else:
            verdes.append(f"top 10 holders con {top10:.1f}% (<20%)")
            puntos += 8
        sup = ((rc.get("token") or {}).get("supply")) or 0
        cb = rc.get("creatorBalance") or 0
        if sup:
            devpct = cb / sup * 100
            if devpct > 20:
                rojas.append(f"el creador todavía tiene {devpct:.1f}% del supply (>20%)")
                puntos -= 25
            elif devpct > 8:
                amarillas.append(f"el creador tiene {devpct:.1f}% del supply")
                puntos -= 8
            else:
                verdes.append(f"el creador tiene {devpct:.1f}% del supply (bajo)")
                puntos += 5

    # ---------- insiders / bundles (el patrón que se rastrea antes de comprar)
    if rc:
        nets = rc.get("insiderNetworks") or []
        gi = rc.get("graphInsidersDetected") or 0
        if nets:
            peor = max((n.get("size") or 0) for n in nets)
            activos = sum((n.get("activeAccounts") or 0) for n in nets)
            amarillas.append(f"red de insiders: {len(nets)} grupo(s), hasta {peor} wallets, {activos} activas")
            puntos -= min(18, peor / 3)
        elif gi:
            amarillas.append(f"RugCheck detectó {gi} insiders")
            puntos -= 8
        ct = rc.get("creatorTokens") or []
        if isinstance(ct, list) and len(ct) > 3:
            amarillas.append(f"el creador ya lanzó {len(ct)} tokens (lanzador serial)")
            puntos -= min(12, len(ct))
        elif isinstance(ct, list) and len(ct) == 1:
            verdes.append("primer token del creador")
            puntos += 4
        lp = rc.get("totalMarketLiquidity")
        salida_curva = sol_curve * SOL_USD
        if completo:
            if lp:
                if lp < 5_000:
                    rojas.append(f"liquidez del pool {usd(lp)} (<5k) → no vas a poder salir")
                    puntos -= 25
                elif lp < 25_000:
                    amarillas.append(f"liquidez del pool {usd(lp)} (ajustada)")
                    puntos -= 6
                elif not ds:
                    verdes.append(f"liquidez del pool {usd(lp)}")
                    puntos += 6
        else:
            # en la curva la "liquidez" es lo que otros metieron: es tu único dinero de salida
            if salida_curva < 500:
                rojas.append(f"en la curva solo hay {usd(salida_curva)} para salir (nadie compró todavía)")
                puntos -= 15
            elif salida_curva < 3_000:
                amarillas.append(f"en la curva hay {usd(salida_curva)} de salida (muy poco)")
                puntos -= 6
            else:
                verdes.append(f"{usd(salida_curva)} dentro de la curva (ya hay compradores)")

    # ---------- mercado real (DexScreener)
    liq = vol24 = buys = sells = None
    cambios = {}
    if ds:
        liq = (ds.get("liquidity") or {}).get("usd")
        vol24 = (ds.get("volume") or {}).get("h24")
        buys = ((ds.get("txns") or {}).get("h24") or {}).get("buys")
        sells = ((ds.get("txns") or {}).get("h24") or {}).get("sells")
        cambios = ds.get("priceChange") or {}
        if liq is not None:
            if liq < 5_000:
                rojas.append(f"liquidez del par {usd(liq)} (<5k)")
                puntos -= 25
            elif liq < 25_000:
                amarillas.append(f"liquidez del par {usd(liq)}")
                puntos -= 6
            else:
                verdes.append(f"liquidez {usd(liq)}")
                puntos += 7
        if vol24 is not None and vol24 < 500:
            rojas.append(f"casi sin operaciones en 24 h ({usd(vol24)}): no hay a quién venderle después")
            puntos -= 30
        elif vol24 is not None and vol24 < 5_000:
            amarillas.append(f"volumen 24 h bajo ({usd(vol24)}): se mueve poco")
            puntos -= 12
        if buys is not None and buys < 5 and edad and edad > 120:
            amarillas.append(f"solo {buys} compras en 24 h: el interés se apagó")
            puntos -= 10
        if vol24 and liq and liq > 0:
            ratio = vol24 / liq
            if ratio > 60:
                amarillas.append(f"volumen/liquidez {ratio:.0f}x → huele a rotación de bots")
                puntos -= 10
            elif ratio > 5:
                verdes.append(f"volumen sano ({ratio:.1f}x la liquidez)")
                puntos += 5
        if vol24 and mc and mc > 0:
            vm = vol24 / mc * 100
            if vm > 300:
                amarillas.append(f"volumen 24h = {vm:.0f}% del mcap (fiebre / posible wash)")
                puntos -= 6
            elif vm > 10:
                verdes.append(f"volumen 24h = {vm:.0f}% del mcap (hay interés real)")
                puntos += 5
        if buys is not None and sells:
            r = buys / max(sells, 1)
            if 0.7 <= r <= 1.6:
                verdes.append(f"compras/ventas equilibradas ({buys}/{sells})")
                puntos += 4
            else:
                amarillas.append(f"compras/ventas desbalanceadas ({buys}/{sells})")
                puntos -= 4
        if ds.get("boosts", {}).get("active"):
            amarillas.append("está pagando promoción (boosts activos) → el volumen puede ser comprado")
            puntos -= 10
    else:
        notas.append("sin par en DexScreener todavía (normal si es recién nacido)")

    # ---------- contexto del lanzamiento
    if completo:
        if edad and edad > 3 * 1440:
            notas.append(f"graduó hace {edad/1440:.0f} días: aquella ventana de pump ya pasó")
            puntos += 2
        else:
            verdes.append("ya graduó (migró a PumpSwap/Raydium): la liquidez no la puede retirar el dev")
            puntos += 10
    else:
        if progreso >= 0.8:
            amarillas.append(f"curva al {progreso*100:.0f}% — si no gradúa, se apaga")
        puntos -= 0
    if edad is not None:
        if edad < 3:
            rojas.append(f"tiene {fmt_edad(edad)}: estás en plena ventana de snipers (los primeros bloques se compran con bots)")
            puntos -= 20
        elif edad < 15:
            amarillas.append(f"{fmt_edad(edad)} de vida: la primera vela aún no está formada")
            puntos -= 8
    if reply >= 50:
        verdes.append(f"{reply} comentarios: hay comunidad alrededor")
        puntos += 8
    elif reply >= 10:
        puntos += 3
    else:
        amarillas.append(f"apenas {reply} comentarios (sin comunidad todavía)")
        puntos -= 5
    if social == 0:
        amarillas.append("sin redes sociales declaradas")
        puntos -= 8
    elif social >= 2:
        verdes.append(f"{social} redes declaradas")
        puntos += 4
    if coin.get("boost_mode") and coin["boost_mode"] != "NONE":
        amarillas.append(f"boost_mode={coin['boost_mode']}")
        puntos -= 4
    if coin.get("verified"):
        verdes.append("verificado en pump.fun")
        puntos += 3

    # ---------- ¿llegué tarde? (contexto de precio, no de indicadores)
    ath = coin.get("ath_market_cap") or 0
    if ath and mc and ath > 0:
        caida = (1 - mc / ath) * 100
        if caida > 85:
            amarillas.append(f"está {-caida:.0f}% por debajo de su máximo ({usd(ath)}) → puede ser cuchillo cayendo")
            puntos -= 10
        elif caida < 5:
            notas.append(f"cerca de su máximo ({usd(ath)}) → entrás en la parte alta")
    if mc:
        if mc > 1_000_000:
            verdes.append(f"mcap {usd(mc)}: ya es grande para memecoin nueva")
        elif mc < 15_000:
            amarillas.append(f"mcap {usd(mc)}: micro, o explota o muere en minutos")

    puntos = max(0.0, min(100.0, puntos))
    if rojas:
        veredicto, etiqueta = "NO", "🚫 NO VALE LA PENA"
    elif puntos >= 70:
        veredicto, etiqueta = "SI", "✅ VALE LA PENA (con tamaño chico)"
    elif puntos >= 45:
        veredicto, etiqueta = "DUDOSO", "⚠️ DUDOSO — solo con reglas estrictas"
    else:
        veredicto, etiqueta = "NO", "🚫 NO VALE LA PENA"

    anotar_creador(mint, coin.get("creator"))
    return {
        "mint": mint, "nombre": nom, "simbolo": sym,
        "imagen": imagen_de(mint, ds, coin),
        "imagenes": [imagen_de(mint, ds, coin)] + _variantes_ipfs(imagen_de(mint, ds, coin)),
        "puntaje": round(puntos, 1), "veredicto": veredicto, "etiqueta": etiqueta,
        "rojas": rojas, "amarillas": amarillas, "verdes": verdes, "notas": notas,
        "contexto": {
            "mcap_usd": round(mc, 2), "ath_mcap_usd": round(ath, 2),
            "curva_sol": round(sol_curve, 2), "curva_pct": round(progreso * 100, 1),
            "sol_usd": round(SOL_USD, 2), "salida_curva_usd": round(sol_curve * SOL_USD, 2),
            "graduado": completo, "edad_min": None if edad is None else round(edad, 1),
            "comentarios": reply, "redes": social, "creador": coin.get("creator"),
            "holders": (rc or {}).get("totalHolders"), "top10_pct": None if top10 is None else round(top10, 1),
            "liquidez_usd": liq, "vol24_usd": vol24, "compras24": buys, "ventas24": sells,
            "cambio_1h": cambios.get("h1"), "cambio_24h": cambios.get("h24"),
            "rugcheck_score": (rc or {}).get("score_normalised"),   # es RIESGO: más bajo = más sano
            "insiders_detectados": (rc or {}).get("graphInsidersDetected") or 0,
            "rugado": bool((rc or {}).get("rugged")),
            "creador_holding": (rc or {}).get("creatorBalance"),
        },
    }


def texto(ev, con_controles=False):
    c = ev["contexto"]
    l = []
    l.append(f"🔎 {ev['nombre']} (${ev['simbolo']}) — {ev['etiqueta']} · puntaje {ev['puntaje']}/100")
    l.append(f"   mint: {ev['mint']}")
    l.append("")
    l.append("📊 CONTEXTO")
    l.append(f"   · mcap {usd(c['mcap_usd'])} (máx histórico {usd(c['ath_mcap_usd'])})")
    if c["graduado"]:
        l.append("   · ya graduó → liquidez en PumpSwap/Raydium")
    else:
        l.append(f"   · curva al {c['curva_pct']}% ({c['curva_sol']} SOL de {GRAD_SOL:.0f}) "
                 f"· dentro de la curva hay {usd(c['salida_curva_usd'])} para salir")
    l.append(f"   · edad {fmt_edad(c['edad_min'])} · {c['comentarios']} comentarios · {c['redes']} redes")
    if c["holders"]:
        l.append(f"   · {c['holders']} holders · top10 {c['top10_pct']}% del supply")
    if c["liquidez_usd"]:
        l.append(f"   · liquidez {usd(c['liquidez_usd'])} · volumen 24h {usd(c['vol24_usd'])} "
                 f"· compras/ventas {c['compras24']}/{c['ventas24']}")
    if c["cambio_1h"] is not None:
        l.append(f"   · precio 1h {c['cambio_1h']:+.1f}% · 24h {c['cambio_24h']:+.1f}%")
    l.append(f"   · creador {c['creador']}")
    if c["rugcheck_score"] is not None:
        l.append(f"   · RugCheck score {c['rugcheck_score']}/100")
    if ev["rojas"]:
        l.append("")
        l.append("🚫 DESCALIFICANTES")
        for x in ev["rojas"]:
            l.append(f"   · {x}")
    if ev["amarillas"]:
        l.append("")
        l.append("⚠️ A VIGILAR")
        for x in ev["amarillas"]:
            l.append(f"   · {x}")
    if ev["verdes"]:
        l.append("")
        l.append("✅ A FAVOR")
        for x in ev["verdes"]:
            l.append(f"   · {x}")
    if ev["notas"]:
        l.append("")
        for x in ev["notas"]:
            l.append(f"ℹ️ {x}")
    l.append("")
    l.append("💡 Costo de operar: 1,25% por lado en la curva (ida y vuelta ~2,5%, oficial de pump.fun)")
    l.append("   + priority fee/bribe. Con eso, un +2,5% apenas empata. Los pumps duran 4-8 h, no días.")
    if con_controles:
        l.append("🧪 Chequeos que este análisis NO hace: simulación de venta (honeypot), historial "
                 "completo del dev, y si los 'smart money' están entrando o saliendo.")
    return "\n".join(l)


# ------------------------------------------------- embeds lindos para Discord

COLOR = {"SI": 0x2ECC71, "DUDOSO": 0xF1C40F, "NO": 0xE74C3C}


# Medido desde este equipo: ipfs.io / dweb.link / w3s.link / nftstorage dan 403 (bloquean IP de datacenter);
# pinata, filebase y 4everland responden 200 con la misma imagen.
GATEWAYS = ["https://gateway.pinata.cloud/ipfs/", "https://ipfs.filebase.io/ipfs/",
            "https://4everland.io/ipfs/", "https://ipfs.io/ipfs/", "https://dweb.link/ipfs/"]


def _tipo_imagen(datos):
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if datos[:2] == b"\xff\xd8":
        return "image/jpeg", "jpg"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp", "webp"
    if datos[:3] == b"GIF":
        return "image/gif", "gif"
    return "image/png", "png"


# images.pump.fun devuelve esta imagen genérica cuando el token no tiene arte: hay que descartarla
PLACEHOLDER_MD5 = {"0f11c2dbe7ed6b02c2d982d9871d5910",
                   "abc95adbdc1fc5436cc608e41cf3d51d"}   # CDN coin-image y tarjeta og


def imagen_de(mint, ds=None, coin=None):
    """Mejor URL de imagen disponible: CDN de DexScreener → imagen de pump.fun → CDN de pump.fun."""
    url = ((ds or {}).get("info") or {}).get("imageUrl") or (coin or {}).get("image_uri")
    if url:
        return url
    if mint:
        return f"https://images.pump.fun/coin-image/{mint}?variant=600x600"
    return None


# Gateways de IPFS que dan 403 desde este equipo (bloquean IPs de datacenter). Si la imagen viene
# de uno de estos, se reescribe la MISMA imagen por uno de los que sí responden — sin esto, la señal
# salía sin imagen cada vez que DexScreener no tenía la foto y pump.fun la servía por ipfs.io.
GATEWAYS_BLOQUEADOS = ("ipfs.io", "dweb.link", "w3s.link", "nftstorage.link", "cloudflare-ipfs.com")


def _variantes_ipfs(url):
    """Misma imagen por los gateways que sí responden (devuelve [] si no es de IPFS/bloqueado)."""
    if not url:
        return []
    if url.startswith("ipfs://"):
        return [g + url[7:] for g in GATEWAYS]
    m = re.search(r"/(ipfs/[A-Za-z0-9]+)", url)
    if m and any(b in url for b in GATEWAYS_BLOQUEADOS):
        return [g + m.group(1) for g in GATEWAYS]
    return []


def descargar_imagen(url, base, max_mb=8):
    """Baja la imagen del token. Acepta UNA url o una LISTA de candidatas (mejor primero) y prueba
    todas, incluidas las variantes por gateways que no estén bloqueados. Devuelve la ruta o None."""
    if not url:
        return None
    urls = list(url) if isinstance(url, (list, tuple)) else [url]
    candidatos, vistos = [], set()
    for u in urls:
        for x in [u] + _variantes_ipfs(u):
            if x and x not in vistos:
                vistos.add(x)
                candidatos.append(x)
    for u in candidatos:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "image/*,*/*"})
            # Timeout CORTO a propósito: los gateways IPFS públicos bloquean IPs de datacenter y se
            # quedan colgados hasta agotarlo (medido: una pasada de vigilancia tardaba más de 5 min por
            # esto, y las calls llegaban tarde). Los que responden bien lo hacen en menos de 2 s.
            with urllib.request.urlopen(req, timeout=4) as r:
                datos = r.read(max_mb * 1024 * 1024)
            if len(datos) < 200:
                continue
            if hashlib.md5(datos).hexdigest() in PLACEHOLDER_MD5:
                continue                      # es la imagen genérica de pump.fun: sin arte real
            tipo, ext = _tipo_imagen(datos)
            destino = f"{base}.{ext}"
            with open(destino, "wb") as fh:
                fh.write(datos)
            return destino
        except Exception:
            continue
    log("  ! no pude bajar la imagen en ningún gateway")
    return None


def embed_de(ev, con_imagen=True):
    """Alerta de memecoin: veredicto arriba, métricas en columnas, banderas ordenadas.

    Si el token trae una RÁFAGA (compra coordinada), el aviso cambia de piel: encabezado gigante,
    columnas con los números de la ráfaga primero y color ámbar. La ráfaga es la noticia.
    """
    c = ev["contexto"]
    sep = "\u2500" * 20
    raf = ev.get("call")
    es_bundle = bool(raf) and (raf.get("slots_iguales") or 1) >= 3
    if raf and raf.get("es_cohorte"):
        # Aviso de Argos: el protagonista es el GRUPO trackeado, no la ráfaga. Antes salía con el
        # formato viejo ("CALL x/10 · N wallets") y se confundía con el detector de ráfagas.
        # AVISO COMPACTO (pedido: "resumí y dejá lo importante"). Arriba va lo que decide —quién entró
        # con su PnL de copia, cuánta plata y el token en 3 datos— y se van el encabezado gigante, el
        # perfil, el origen y las repeticiones (eso sigue en el registro, no hace falta en el celular).
        _desc_compacta = [
            (f"🏆 **CALIDAD {raf['nota_calidad']}/10**"
             + (f" · top {100 - raf['percentil_calidad']:.0f}% del día" if raf.get("percentil_calidad") is not None else "")
             if raf.get("nota_calidad") is not None else ""),
            f"**{raf['n_wallets']} wallet{'s' if raf['n_wallets'] != 1 else ''}** de "
            f"**{raf.get('nombre_grupo') or 'grupo'}** · **{raf['sol']} SOL**"
            + (f" en {max(1, raf['segundos'] // 60)} min" if raf.get("segundos") else ""),
            raf.get("linea_wallets") or ("👤 " + " · ".join(f"`{str(w)[:8]}…`" for w in (raf.get("wallets") or [])[:4])),
            f"FDV {usd(c['mcap_usd'])} · {fmt_edad(c['edad_min'])} · {c['holders'] or '?'} holders · "
            f"liq {usd(c['liquidez_usd'])}",
        ]
        desc = _desc_compacta + [f"`{ev['mint']}`"]
    elif raf:
        titulo_call = ("# \U0001F6A8\U0001F6A8 BUNDLE EN EL MISMO SLOT \U0001F6A8\U0001F6A8" if es_bundle
                       else "# \u26A1\u26A1 CALL DETECTADA \u26A1\u26A1")
        desc = [
            titulo_call,
            f"**FUERZA DE LA CALL: {raf.get('nota', '?')}/10 {raf.get('etq', '')}**",
            f"**{raf['n_wallets']} wallets distintas entraron en {raf['segundos']} s "
            f"poniendo {raf['sol']} SOL**",
        ]
        if raf.get("frescas") or raf.get("conocidas"):
            partes = []
            if raf.get("frescas"):
                partes.append(f"**{raf['frescas']} NUEVAS** (sin historial)")
            if raf.get("conocidas"):
                partes.append(f"{raf['conocidas']} ya conocidas por nosotros")
            desc.append(f"\u203A carteras: {' · '.join(partes)}")
        if raf.get("slots_iguales", 1) >= 2:
            desc.append(f"\u203A \U0001F6A8 {raf['slots_iguales']} de esas compras cayeron en el "
                        f"**mismo slot** (misma fracción de segundo)")
        if raf.get("compras_en_ventana"):
            desc.append(f"\u203A en la misma ventana hubo {raf['compras_en_ventana']} compras en total")
        desc += [
            f"\u203A arranc\u00f3 {raf['hora']} · pool `{str(raf.get('pool',''))[:8]}…`",
            f"**\U0001F449 ¿Qu\u00e9 es esto?** Varios wallets distintos comprando el MISMO token en el "
            f"mismo momento no es casualidad: es una compra coordinada (call de un grupo o un bundle).",
            sep,
            f"### {ev['etiqueta']}",
            f"**CALIDAD DEL TOKEN: {ev['puntaje']}/100**", sep,
        ]
    else:
        desc = [f"### {ev['etiqueta']}",
                f"**CALIDAD DEL TOKEN: {ev['puntaje']}/100**", sep]
    if ev["rojas"]:
        desc.append("**\U0001F6AB Descalificantes**")
        desc += [f"\u203A {x}" for x in ev["rojas"][:4]]
    if ev["amarillas"]:
        desc.append("**\u26A0\uFE0F A vigilar**")
        desc += [f"\u203A {x}" for x in ev["amarillas"][:4]]
    if ev["verdes"]:
        desc.append("**\u2705 A favor**")
        desc += [f"\u203A {x}" for x in ev["verdes"][:5]]
    campos = []
    if raf and raf.get("es_cohorte"):
        # AVISO COMPACTO: lo que importa ya está en el texto de arriba; acá quedan sólo los links
        # (el contrato va aparte, en texto plano, para poder copiarlo en el celular).
        campos = [{"name": "\U0001F517 Links", "inline": False,
                   "value": f"[GMGN](https://gmgn.ai/sol/token/{ev['mint']}) · "
                            f"[DexScreener](https://dexscreener.com/solana/{ev['mint']}) · "
                            f"[Pump.fun](https://pump.fun/coin/{ev['mint']}) · "
                            f"[Solscan](https://solscan.io/token/{ev['mint']})"}]
    elif raf:
        etq1 = "\u26A1 Wallets"
        campos += [
            {"name": etq1, "inline": True,
             "value": (f"`{raf['n_wallets']} de {raf.get('tamano_grupo') or raf['n_wallets']}`"
                       if raf.get("es_cohorte") else f"`{raf['n_wallets']}`")},
            {"name": "\U0001F4B8 SOL puestos", "inline": True, "value": f"`{raf['sol']}`"},
            {"name": "\u23F1\uFE0F Ventana", "inline": True,
             "value": f"`{max(1, raf['segundos'] // 60)} min`" if raf.get("es_cohorte") else f"`{raf['segundos']} s`"},
            {"name": "\U0001F4CB CONTRATO", "inline": False,
             "value": f"`{ev['mint']}`\n"
                      f"[GMGN](https://gmgn.ai/sol/token/{ev['mint']}) · "
                      f"[DexScreener](https://dexscreener.com/solana/{ev['mint']}) · "
                      f"[Pump.fun](https://pump.fun/coin/{ev['mint']}) · "
                      f"[Solscan](https://solscan.io/token/{ev['mint']})"},
        ]
        if raf.get("motivos"):
            campos.append({"name": "\U0001F9EE POR QUÉ ESE PUNTAJE DE FUERZA",
                           "inline": False,
                           "value": "\n".join(f"\u203A {m}" for m in raf["motivos"])[:1024]})
    if not (raf and raf.get("es_cohorte")):      # en el aviso compacto estos datos ya van en el texto
        campos += [
          {"name": "\U0001F4B0 Market cap", "inline": True,
           "value": f"`{usd(c['mcap_usd'])}`\nmáx `{usd(c['ath_mcap_usd'])}`"},
        {"name": "\U0001F4C8 Curva", "inline": True,
         "value": "`graduado` \u2705" if c["graduado"] else f"`{c['curva_pct']}%`"},
        {"name": "\U000023F1\uFE0F Edad", "inline": True, "value": f"`{fmt_edad(c['edad_min'])}`"},
        {"name": "\U0001F465 Holders", "inline": True,
         "value": f"`{c['holders'] or '?'}`\ntop10 `{c['top10_pct'] if c['top10_pct'] is not None else '?'}%`"},
        {"name": "\U0001F4A7 Liquidez", "inline": True,
         "value": f"`{usd(c['liquidez_usd'])}`\nvol `{usd(c['vol24_usd'])}`"},
        {"name": "\U0001F9EA Compras/ventas", "inline": True,
         "value": f"`{c['compras24'] or '?'}` / `{c['ventas24'] or '?'}`"},
    ]
    em = {
        "author": {"name": ("\U0001F578\uFE0F Detector de grupos" if raf and raf.get("es_cohorte")
                            else ("\u26A1 Detector de ráfagas" if raf else "Radar de pump.fun"))},
        "title": ((f"🏆 {raf.get('nota_calidad')}/10 · " if raf.get("nota_calidad") is not None and raf.get("nota_calidad") >= 7 else "")
                  + (f"\U0001F578\uFE0F GRUPO {raf['n_wallets']}/{raf.get('tamano_grupo') or raf['n_wallets']} "
                  f"wallets \u00b7 \U0001FA99 token {ev['puntaje']}/100 — {ev['nombre']} · ${ev['simbolo']}"
                  if (raf and raf.get("es_cohorte"))
                  else (f"\u26A1 CALL {raf.get('nota', '?')}/10 \u00b7 \U0001FA99 token "
                        f"{ev['puntaje']}/100 — {ev['nombre']} · ${ev['simbolo']}" if raf
                        else f"{ev['nombre']} · ${ev['simbolo']}"))),
        "description": "\n".join(desc)[:4000],
        # COLOR POR NIVEL (pedido): de un vistazo se ve qué clase de aviso es.
        #   🏆 dorado  = fue al canal de las mejores   🟢 verde = grupo que seguimos
        #   🟠 naranja = lotería                       🟣 violeta = el resto de Argos
        "color": (0xFFD700 if (raf and raf.get("en_mejores"))
                  else (0x2ECC71 if (raf and (raf.get("tier") or "").startswith("🟢"))
                        else (0xE67E22 if (raf and (raf.get("tier") or "").startswith("🟠"))
                              else (0x8E44AD if (raf and raf.get("es_cohorte"))
                                    else (0xFFB300 if raf else COLOR.get(ev["veredicto"], 0x95A5A6)))))),
        "fields": campos,
        "footer": {"text": f"{ev['mint']} · solo informa, no opera"},
        "url": f"https://pump.fun/coin/{ev['mint']}",
    }
    if raf and raf.get("ts_iso"):
        em["timestamp"] = raf["ts_iso"]      # Discord lo muestra como "hace X minutos"
    if con_imagen:
        em["image"] = {"url": "attachment://token.png"}
    return em


def _multipart(campos, archivos):
    borde = "----hermes" + uuid.uuid4().hex
    partes = []
    for k, v in campos.items():
        partes.append(f"--{borde}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (nombre, datos, tipo) in archivos.items():
        partes.append(f"--{borde}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{nombre}\"\r\n"
                      f"Content-Type: {tipo}\r\n\r\n".encode() + datos + b"\r\n")
    partes.append(f"--{borde}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={borde}"


def enviar_embed(embed, contenido=None, imagen=None, canal=None):
    """Manda un embed a Discord (bot o webhook). Si hay imagen, va adjunta y el embed la usa."""
    env = _env()
    hook = env.get("DISCORD_WALLETS_WEBHOOK") or env.get("DISCORD_WEBHOOK_URL")
    tok = env.get("DISCORD_BOT_TOKEN")
    canal = (canal or env.get("DISCORD_PUMP_CHANNEL") or env.get("DISCORD_WALLETS_CHANNEL")
             or env.get("DISCORD_SIGNALS_CHANNEL") or env.get("DISCORD_HOME_CHANNEL"))
    if not _permiso_envio(canal or "defecto"):
        log("  (embed salteado por ritmo de Discord: se manda en el próximo minuto)")
        return False
    hook = env.get("DISCORD_WALLETS_WEBHOOK") or env.get("DISCORD_WEBHOOK_URL")
    tok = env.get("DISCORD_BOT_TOKEN")
    if not hook and not (tok and canal):
        return "sin canal de Discord configurado"
    url = hook or f"https://discord.com/api/v10/channels/{canal}/messages"
    if imagen and os.path.exists(imagen):
        # OJO: el embed tiene que apuntar al nombre REAL del adjunto ANTES de armar el payload,
        # si no Discord no encuentra la imagen y la descarta en silencio.
        embed = dict(embed, image={"url": f"attachment://{os.path.basename(imagen)}"})
    payload = {"embeds": [embed]}
    if contenido:
        payload["content"] = contenido
    if hook:
        payload["username"] = "Radar pump.fun"
    datos = None
    headers = {"User-Agent": "hermes-pump-radar/1.0"}
    if imagen and os.path.exists(imagen):
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif"}.get(
                    os.path.splitext(imagen)[1].lstrip(".").lower(), "image/png")
        with open(imagen, "rb") as fh:
            datos, ctype = _multipart({"payload_json": json.dumps(payload)},
                                      {"files[0]": (os.path.basename(imagen), fh.read(), mime)})
        headers["Content-Type"] = ctype
    else:
        datos = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if not hook:
        headers["Authorization"] = f"Bot {tok}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=datos, headers=headers,
                                                           method="POST"), timeout=40) as r:
            return r.status in (200, 204)
    except Exception as e:
        log(f"  ! fallo el embed: {e}")
        return False


RITMO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_ritmo.json")
LIMITE_MSG_MINUTO = 15        # por canal y por minuto (Discord corta cerca de 30: se deja margen)


def _permiso_envio(canal):
    """¿Se puede mandar otro mensaje a ESTE canal en este minuto?

    El contador va a un ARCHIVO, no a memoria: cada corrida del cron es un proceso nuevo y con un
    contador propio entre todos igual se pasaba el límite (medido: seguían llegando 429 después de
    frenarlo en memoria). Se saltea el envío pero la call queda registrada para medir el resultado.
    """
    minuto = int(time.time() // 60)
    try:
        d = json.load(open(RITMO_FILE))
    except Exception:
        d = {}
    if d.get("minuto") != minuto:
        d = {"minuto": minuto, "canales": {}}
    n = (d.get("canales") or {}).get(canal, 0)
    if n >= LIMITE_MSG_MINUTO:
        return False
    d.setdefault("canales", {})[canal] = n + 1
    try:
        tmp = RITMO_FILE + ".%d.tmp" % os.getpid()
        json.dump(d, open(tmp, "w"))
        os.replace(tmp, RITMO_FILE)
    except Exception:
        pass
    return True


def enviar_en_canal(mensaje, canal=None):
    """Manda un mensaje de TEXTO PLANO (sin embed) al canal.

    Sirve para el contrato suelto: un mensaje que contiene **solo** la dirección es lo único que el
    "copiar texto" del celular copia limpio (los bloques de código dentro de embeds no se pueden
    copiar en todos los clientes).
    """
    env = _env()
    hook = env.get("DISCORD_WALLETS_WEBHOOK") or env.get("DISCORD_WEBHOOK_URL")
    tok = env.get("DISCORD_BOT_TOKEN")
    canal = (canal or env.get("DISCORD_PUMP_CHANNEL") or env.get("DISCORD_WALLETS_CHANNEL")
             or env.get("DISCORD_SIGNALS_CHANNEL") or env.get("DISCORD_HOME_CHANNEL"))
    if not _permiso_envio(canal or "defecto"):
        log("  (mensaje suelto salteado por ritmo de Discord)")
        return False
    headers = {"Content-Type": "application/json", "User-Agent": "hermes-pump-radar/1.0"}
    if hook:
        url = hook
        payload = {"content": mensaje, "username": "Radar pump.fun"}
    elif tok and canal:
        url = f"https://discord.com/api/v10/channels/{canal}/messages"
        headers["Authorization"] = f"Bot {tok}"
        payload = {"content": mensaje}
    else:
        return False
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(payload).encode(),
                                                          headers=headers, method="POST"),
                                    timeout=25) as r:
            return r.status in (200, 204)
    except Exception as e:
        log(f"  ! no pude mandar el mensaje suelto: {e}")
        return False


def alertar_token(ev, titulo="\U0001F6A8 NUEVA MEMECOIN QUE PASA EL FILTRO", canal=None):
    """Manda el embed del token (con su imagen) y, si la imagen no se pudo bajar, manda solo el texto.

    Si el aviso es por RÁFAGA:
      · el texto que va arriba del embed lo grita con los números a la vista;
      · y **después del embed va un mensaje aparte con SOLO la dirección** (texto plano, sin formato):
        así el "copiar texto" del celular copia exactamente el contrato, sin nada más.
    """
    raf = ev.get("call")
    if raf:
        # -------- MODO CALIDAD: si no pasa los mínimos medidos, se registra pero NO se avisa.
        # (las "calls" de 2 wallets y 0,1 SOL eran ruido: la mitad terminó en 0,6× y sólo 3 % pasó 1,5×)
        if not raf.get("es_cohorte"):
            import calls_detector as CD
            pasa, falta = CD.pasa_calidad(raf, ev.get("contexto") or {})
            if not pasa:
                raf["silenciosa"] = True      # frenada por el filtro de calidad (se mide igual)
                try:
                    import registro_calls as RC
                    RC.registrar(raf, ev)
                except Exception:
                    pass
                log(f"  (sin aviso, calidad insuficiente: {'; '.join(falta[:3])})")
                return False
        tipo = "BUNDLE" if (raf.get("slots_iguales") or 1) >= 3 else "CALL"
        extra = f" · {raf['frescas']} de ellas NUEVAS" if raf.get("frescas") else ""
        contenido = (f"\u26A1\u26A1\u26A1 **FUERZA {raf.get('nota', '?')}/10 {raf.get('etq','')}** "
                     f"\u26A1\u26A1\u26A1 \u00b7 **{raf['n_wallets']} wallets** entraron en "
                     f"**{raf['segundos']} s** con **{raf['sol']} SOL**{extra} en **${ev['simbolo']}**")
    else:
        contenido = f"{titulo} \u00b7 **{ev['puntaje']}/100**"
    base = os.path.join(os.path.expanduser("~/.hermes/cache/scratch"), f"token_{ev['mint'][:8]}")
    # Candidatas de imagen: la que trajo el análisis + el CDN de pump.fun por mint (funciona para casi
    # cualquier token de pump.fun y es el respaldo que faltaba cuando la primera fallaba).
    cands = list(ev.get("imagenes") or [])
    if ev.get("imagen"):
        cands.append(ev["imagen"])
    if (ev.get("contexto") or {}).get("imagen"):
        cands.append(ev["contexto"]["imagen"])
    if ev.get("mint"):
        cands.append(f"https://images.pump.fun/coin-image/{ev['mint']}?variant=600x600")
        cands.append(f"https://pump.fun/api/og/coin/{ev['mint']}")
    img = descargar_imagen(cands, base)
    ok = enviar_embed(embed_de(ev), contenido=contenido, imagen=img, canal=canal)
    if not ok:
        ok = enviar(texto(ev), canales=("discord",))
    if raf:
        # Mensaje aparte SOLO con la dirección (texto plano, sin etiqueta ni formato): así el
        # "copiar texto" del celular copia el contrato exacto, sin nada más.
        enviar_en_canal(ev["mint"], canal=canal)
        # Registro para recalibrar el puntaje con datos reales (mide el resultado a 1/6/24 h).
        try:
            import registro_calls as RC
            RC.registrar(raf, ev)
        except Exception as ex:
            log(f"  ! no pude registrar la call: {ex}")
    return ok


def anotar_creador(mint, creador):
    """Registro propio de lanzadores: cuántos tokens largó cada creador.

    RugCheck no da el historial del creador y la API de pump.fun que lo daba devuelve 404/530, así que
    lo llevamos nosotros: cada token que analizamos suma su creador. Con unos días de datos, un creador
    que larga en serie queda marcado solo.
    """
    if not creador:
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creadores.json")
    d = _leer_json(path, {})
    e = d.setdefault(creador, {"lanzamientos": [], "visto": None})
    if mint not in e["lanzamientos"]:
        e["lanzamientos"] = (e["lanzamientos"] + [mint])[-60:]
    e["visto"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with open(path, "w") as fh:
            json.dump(d, fh, ensure_ascii=False)
    except Exception:
        pass


def _leer_json(path, por_defecto):
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except Exception:
            pass
    return por_defecto


def _guardar_json(path, datos):
    _t = f"{path}.{os.getpid()}.tmp"
    with open(_t, "w") as fh:
        json.dump(datos, fh, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)


def tamano_ok(ev, min_mcap=100_000, min_liq=5_000, min_curva=30):
    """¿El token tiene tamaño suficiente para merecer un aviso?

    El puntaje solo no alcanza: hay decenas de tokens de $3-4k de mcap que puntúan alto y llenan el
    canal de ruido. Para avisar se exige mcap decente y liquidez real **o** curva avanzada (los que
    siguen en la curva tienen liquidez 0 en DexScreener, así que se mide la curva).

    OJO con el mínimo: medido sobre las 8 calls del 07/10, las 4 de tokens de **$3-30k** se hundieron
    (0,1x a 0,3x, −79 % a −95 %) y las 4 de **$130-240k** salieron todas positivas (0,7x a 3,2x).
    Por eso el mínimo subió de 25k a **100k**: con eso hoy solo habrían llegado las 4 buenas.
    """
    c = ev.get("contexto") or {}
    mcap = c.get("mcap_usd") or 0
    liq = c.get("liquidez_usd") or 0
    curva = c.get("curva_pct") or 0
    return mcap >= min_mcap and (liq >= min_liq or curva >= min_curva)


def vigilar(umbral=70.0, max_alertas=1, min_edad=10.0, max_edad_h=24.0, reanalizar_h=6.0,
            estado="~/.hermes/data/memecoins/alertas_state.json", dry=False, max_analisis=60,
            solo_rafagas=False):
    """Busca memecoins NUEVAS que valgan la pena y avisa (una sola vez por token).

    `solo_rafagas=True`: se analiza igual (hace falta para evaluar las candidatas a ráfaga) pero
    **no se avisa ningún token normal** — al canal solo van las ⚡ CALL. Así el canal queda limpio.

    Prefiltro barato con los datos de pump.fun (sin gastar llamadas): solo tokens con plata
    real encima (>=20k de mcap o >=5 SOL en la curva). Después analiza y alerta las que pasan
    el umbral; cada token se alerta una sola vez y no se re-analiza por reanalizar_h horas."""
    estado = os.path.expanduser(estado)
    est = _leer_json(estado, {"vistos": {}})
    vistos = est.setdefault("vistos", {})
    ahora = time.time()

    unicos = {}
    for fuente in (pump_nuevos, lambda n=150: pump_live(n), lambda n=150: pump_movidos(n)):
        try:
            for c in fuente(150):
                if c.get("mint"):
                    unicos.setdefault(c["mint"], c)
        except Exception:
            continue
    log(f"vigilando: {len(unicos)} tokens únicos en las listas")

    cand = []
    for m, c in unicos.items():
        e = edad_min(c.get("created_timestamp"))
        if e is None or e < min_edad or e > max_edad_h * 60:
            continue
        v = vistos.get(m)
        if v and (v.get("alertado") or (ahora - v.get("ts", 0)) < reanalizar_h * 3600):
            continue
        mc = c.get("usd_market_cap") or 0
        curva_sol = (c.get("real_sol_reserves") or 0) / 1e9
        if mc < 20_000 and curva_sol < 5:          # prefiltro: sin plata real encima, no se analiza
            continue
        cand.append(c)
    cand.sort(key=lambda c: -((c.get("usd_market_cap") or 0)))
    cand = cand[:max_analisis]
    log(f"  {len(cand)} pasaron el prefiltro (edad {min_edad:g} min - {max_edad_h:g} h, mcap ≥20k o curva ≥5 SOL)")

    sol_usd = precio_sol()
    listas = []
    for c in cand:
        try:
            ev = analizar(c, pedir=True, SOL_USD=sol_usd)
        except Exception as ex:
            log(f"  ! error con {c.get('mint','?')[:8]}: {ex}")
            continue
        previo = vistos.get(c["mint"]) or {}
        alertar = (not solo_rafagas and ev["puntaje"] >= umbral
                   and not previo.get("alertado") and tamano_ok(ev))
        if solo_rafagas and ev["puntaje"] >= umbral and not previo.get("alertado"):
            log(f"  silenciado (solo ráfagas): {ev['simbolo']} {ev['puntaje']}")
        elif ev["puntaje"] >= umbral and not previo.get("alertado") and not tamano_ok(ev):
            ctx = ev.get("contexto") or {}
            log(f"  sin tamaño para avisar: {ev['simbolo']} (mcap {usd(ctx.get('mcap_usd') or 0)} · "
                f"liq {usd(ctx.get('liquidez_usd') or 0)} · curva {ctx.get('curva_pct')}%)")
        vistos[c["mint"]] = {"ts": ahora, "puntaje": ev["puntaje"],
                             "alertado": bool(previo.get("alertado") or alertar),
                             "creado": (c.get("created_timestamp") or 0) / 1000,
                             "mcap": c.get("usd_market_cap") or 0,
                             "simbolo": c.get("symbol")}
        if alertar:
            listas.append(ev)
    listas.sort(key=lambda x: -x["puntaje"])
    listas = listas[:max_alertas]

    # --- ⚡ RÁFAGAS: compras coordinadas (calls) ---
    # La ráfaga es señal por sí misma: si aparece, el token se avisa aunque su puntaje no llegue al
    # umbral (porque lo que importa es que alguien con información entró en masa). El chequeo tiene
    # tope de gasto diario (calls_detector.TOPE_DIARIO) para no comerse el cupo del RPC.
    try:
        import calls_detector as CD
        ya = {ev["mint"] for ev in listas}
        frescos = []
        for c in cand:
            e = edad_min(c.get("created_timestamp"))
            if e is None or e > 240:                 # hasta 4 h de vida (antes 2 h): más candidatos
                continue
            if c["mint"] in ya or (c.get("usd_market_cap") or 0) < 10_000:
                continue
            frescos.append(c)
        frescos.sort(key=lambda c: -(c.get("usd_market_cap") or 0))
        # RE-CHEQUEO: los tokens JÓVENES que ya vimos se vuelven a chequear en cada pasada, porque la
        # call puede pasar varios minutos DESPUÉS de que el radar los analizó. Antes se chequeaban una
        # sola vez y todo lo que pasaba después se perdía (era la razón principal de que llegaran pocas).
        en_lista = {c["mint"] for c in frescos}
        for m, v in list(vistos.items()):
            if m in ya or m in en_lista:
                continue
            creado = v.get("creado")
            if not creado or (ahora - creado) > 240 * 60:      # hasta 4 h de vida
                continue
            if (v.get("mcap") or 0) < 10_000:
                continue
            if ahora - (v.get("rafaga_ts") or 0) < 480:        # no re-chequear antes de 8 min
                continue
            v["rafaga_ts"] = ahora
            frescos.append({"mint": m, "simbolo": v.get("simbolo") or m[:6],
                            "usd_market_cap": v.get("mcap") or 0,
                            "created_timestamp": (creado or 0) * 1000, "_revisto": True})
        frescos.sort(key=lambda c: (-(c.get("usd_market_cap") or 0), c["mint"] in ya))
        for c in CD.revisar(frescos, max_por_pasada=12):  # 12 por pasada (pedido del usuario: maximizar)
            try:
                ev = analizar(c, pedir=True, SOL_USD=sol_usd)
            except Exception as ex:
                log(f"  ! error analizando la ráfaga: {ex}")
                continue
            ctx = ev.get("contexto") or {}
            # Se avisa SIEMPRE (pedido del usuario 07/10): el puntaje hace de filtro, no el candado.
            # Sólo se descartan tokens de menos de 10 min: ahí es ventana de snipers pura y no hay
            # nada accionable (el precio todavía es de los bots).
            edad = ctx.get("edad_min") or 0
            if edad < 10:
                log(f"  ráfaga descartada en {ev['simbolo']}: token de {edad:.0f} min "
                    f"(ventana de snipers)")
                continue
            # Un mcap de decenas de millones no es un lanzamiento de memecoin: son activos
            # tokenizados (stablecoins, xStocks) que entran a las listas y no son "calls".
            if (ctx.get("mcap_usd") or 0) > 50_000_000:
                log(f"  ráfaga descartada en {ev['simbolo']}: mcap {usd(ctx.get('mcap_usd'))} "
                    f"(no es un lanzamiento de memecoin)")
                continue
            nota, motivos, etq = CD.puntuar(c["call"], ctx)
            c["call"]["nota"] = nota
            c["call"]["motivos"] = motivos
            c["call"]["etq"] = etq
            log(f"  → puntaje de la call: {nota}/10 {etq}")
            ev["call"] = c["call"]
            ev["por_rafaga"] = True
            listas.append(ev)
            log(f"  ⚡ ráfaga ACEPTADA en {ev['simbolo']} ({nota}/10 {etq}, "
                f"mcap {usd(ctx.get('mcap_usd') or 0)} · curva {ctx.get('curva_pct')}%) → al canal")
            v = vistos.setdefault(c["mint"], {})
            v["ts"] = ahora
            v["puntaje"] = ev["puntaje"]
            v["alertado"] = True
    except Exception as ex:
        log(f"  ! detector de ráfagas: {ex}")

    if not listas:
        log("  nada nuevo pasó el umbral (silencio = no hay nada bueno, no que esté roto)")
    for ev in listas:
        titulo = ("⚡ RÁFAGA · COMPRA COORDINADA DETECTADA" if ev.get("por_rafaga")
                  else "\U0001F6A8 NUEVA MEMECOIN QUE PASA EL FILTRO")
        log(f"  ALERTA {ev['simbolo']} ({ev['puntaje']}) {ev['mint']}"
            f"{' · RÁFAGA ' + str(ev['call']['n_wallets']) + ' wallets' if ev.get('call') else ''}")
        if dry:
            print(json.dumps(embed_de(ev), ensure_ascii=False)[:400])
        else:
            log(f"    envío: {alertar_token(ev, titulo)}")
    if not dry:
        # poda: olvidar lo que tiene más de 30 días
        for m in [m for m, v in vistos.items() if ahora - v.get("ts", 0) > 30 * 86400]:
            vistos.pop(m, None)
        _guardar_json(estado, est)
        # --- lista para el VIGILANTE EN VIVO (WebSocket): tokens jóvenes con su pool ---
        try:
            import calls_detector as CD
            jovenes = []
            for m, v in vistos.items():
                creado = v.get("creado")
                if not creado or (ahora - creado) > 2 * 3600:
                    continue
                if (v.get("mcap") or 0) < 20_000:
                    continue
                pool = CD.pool_de(m)          # DexScreener: no gasta créditos de RPC
                if pool:
                    jovenes.append({"mint": m, "pool": pool, "simbolo": v.get("simbolo"),
                                    "creado": creado})
            ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens_jovenes.json")
            _r = f"{ruta}.{os.getpid()}.tmp"
            with open(_r, "w") as fh:
                json.dump(jovenes, fh, ensure_ascii=False)
            os.replace(_r, ruta)
            log(f"  tokens jóvenes para el vigilante en vivo: {len(jovenes)}")
        except Exception as ex:
            log(f"  ! no pude escribir tokens_jovenes.json: {ex}")
    return listas


def _env(path="~/.hermes/.env"):
    out = {}
    p = os.path.expanduser(path)
    if os.path.exists(p):
        for linea in open(p):
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def enviar(texto_msg, canales=("discord",)):   # el usuario pidió que NO vaya a Telegram
    """Manda el análisis sin gastar tokens (Bot API / webhook). Devuelve qué salió."""
    import uuid
    env = _env()
    res = {}
    if "telegram" in canales:
        tok = env.get("TELEGRAM_BOT_TOKEN")
        chat = env.get("TELEGRAM_HOME_CHANNEL")
        if tok and chat:
            try:
                cuerpo = urllib.parse.urlencode({"chat_id": chat, "text": texto_msg[:4000],
                                                 "disable_web_page_preview": "true"}).encode()
                with urllib.request.urlopen(urllib.request.Request(
                        f"https://api.telegram.org/bot{tok}/sendMessage", data=cuerpo), timeout=30) as r:
                    res["telegram"] = json.loads(r.read().decode()).get("ok", False)
            except Exception as e:
                res["telegram"] = f"error: {e}"
        else:
            res["telegram"] = "sin token/chat"
    if "discord" in canales:
        hook = env.get("DISCORD_WEBHOOK_URL")
        tok, canal = env.get("DISCORD_BOT_TOKEN"), (env.get("DISCORD_PUMP_CHANNEL")
                                                    or env.get("DISCORD_SIGNALS_CHANNEL")
                                                    or env.get("DISCORD_HOME_CHANNEL"))
        partes, cur = [], ""
        for bloque in texto_msg.split("\n\n"):
            if cur and len(cur) + len(bloque) + 2 > 1900:
                partes.append(cur)
                cur = bloque
            else:
                cur = f"{cur}\n\n{bloque}" if cur else bloque
        if cur:
            partes.append(cur)
        url = hook or (f"https://discord.com/api/v10/channels/{canal}/messages" if (tok and canal) else None)
        if not url:
            res["discord"] = "sin webhook/canal"
        else:
            oks = []
            for p in partes:
                try:
                    headers = {"Content-Type": "application/json", "User-Agent": "hermes-pump-radar/1.0"}
                    if hook:
                        cuerpo = json.dumps({"content": p, "username": "pump.fun radar"}).encode()
                    else:
                        cuerpo = json.dumps({"content": p}).encode()
                        headers["Authorization"] = f"Bot {tok}"
                    with urllib.request.urlopen(urllib.request.Request(url, data=cuerpo, headers=headers,
                                                                       method="POST"), timeout=30) as r:
                        oks.append(r.status in (200, 204))
                except Exception as e:
                    oks.append(f"error: {e}")
            res["discord"] = all(x is True for x in oks)
    return res


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Analizador de memecoins de pump.fun")
    ap.add_argument("--mint", help="mint a analizar")
    ap.add_argument("--scan", type=int, help="escanea los últimos N lanzamientos")
    ap.add_argument("--movidos", type=int, help="escanea los N que están operando ahora")
    ap.add_argument("--top", type=int, default=5, help="cuántos mostrar del escaneo")
    ap.add_argument("--min-puntaje", type=float, default=0, help="solo mostrar desde este puntaje")
    ap.add_argument("--max-edad", type=float, default=24, help="ignorar tokens con más de N horas (0 = sin filtro)")
    ap.add_argument("--min-edad", type=float, default=0,
                    help="ignorar tokens con menos de N minutos (0 = sin filtro; 15 evita la ventana de snipers)")
    ap.add_argument("--json", action="store_true", help="salida JSON")
    ap.add_argument("--alerta", action="store_true", help="manda lo que pase --min-puntaje por Telegram/Discord")
    ap.add_argument("--vigilar", action="store_true",
                    help="modo alertador: busca memecoins nuevas que pasen --umbral y avisa (una vez por token)")
    ap.add_argument("--umbral", type=float, default=70.0, help="puntaje mínimo para alertar (default 70)")
    ap.add_argument("--max-alertas", type=int, default=3, help="tope de alertas por corrida")
    ap.add_argument("--solo-rafagas", action="store_true",
                    help="no avisa tokens normales: al canal solo van las ⚡ CALL detectadas")
    ap.add_argument("--reanalizar", type=float, default=6.0,
                    help="horas antes de volver a analizar un token que NO pasó el umbral")
    ap.add_argument("--enviar-top", type=int, default=0, metavar="N",
                    help="manda siempre el resumen + los N mejores del escaneo (para el parte diario)")
    ap.add_argument("--controles", action="store_true", help="incluye los chequeos que faltan")
    a = ap.parse_args()
    if a.vigilar:
        vigilar(umbral=a.umbral, max_alertas=a.max_alertas, min_edad=max(a.min_edad, 0) or 10,
                max_edad_h=a.max_edad, reanalizar_h=a.reanalizar, dry=False,
                max_analisis=60, solo_rafagas=a.solo_rafagas)
        return 0
    sol_usd = precio_sol()
    log(f"SOL = ${sol_usd:.2f}")

    if a.mint:
        coin = buscar_coin(a.mint)
        if coin.get("symbol") == "?":
            log("aviso: ese mint no está en las listas de pump.fun; analizo solo con RugCheck/DexScreener")
        ev = analizar(coin, pedir=True, SOL_USD=sol_usd)
        print(json.dumps(ev, ensure_ascii=False, indent=2) if a.json else texto(ev, a.controles))
        if a.alerta and ev["puntaje"] >= a.min_puntaje:
            log(f"envío: {enviar(texto(ev))}")
        return 0

    n = a.scan or a.movidos or 40
    coins = pump_live(n) if a.movidos else pump_nuevos(n)
    if not coins:
        log("no pude traer lanzamientos de pump.fun")
        return 1
    if a.min_edad:
        antes = len(coins)
        coins = [c for c in coins if (edad_min(c.get("created_timestamp")) or 0) >= a.min_edad]
        if antes != len(coins):
            log(f"  {antes - len(coins)} descartados por tener menos de {a.min_edad:g} min")
    if a.max_edad:
        antes = len(coins)
        coins = [c for c in coins if (edad_min(c.get("created_timestamp")) or 0) <= a.max_edad * 60]
        if antes != len(coins):
            log(f"  {antes - len(coins)} descartados por tener más de {a.max_edad:g} h")
    log(f"analizando {len(coins)} tokens…")
    res = []
    for i, c in enumerate(coins, 1):
        try:
            ev = analizar(c, pedir=True, SOL_USD=sol_usd)
            res.append(ev)
            if i % 10 == 0:
                log(f"  {i}/{len(coins)}")
        except Exception as e:
            log(f"  ! error con {c.get('mint','?')[:8]}: {e}")
    res.sort(key=lambda x: -x["puntaje"])
    buenos = [e for e in res if e["puntaje"] >= a.min_puntaje]

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== {len(res)} analizados · {len(buenos)} con puntaje ≥ {a.min_puntaje} ===")
        print(f"{'puntaje':>8} {'veredicto':<10} {'mcap':>8} {'curva':>6} {'edad':>8} {'top10':>6} {'liq':>8}  token")
        for e in res[:max(a.top, 15)]:
            c = e["contexto"]
            print(f"{e['puntaje']:>8.1f} {e['veredicto']:<10} {usd(c['mcap_usd']):>8} "
                  f"{(str(c['curva_pct'])+'%'):>6} {fmt_edad(c['edad_min']):>8} "
                  f"{(str(c['top10_pct'])+'%' if c['top10_pct'] else '?'):>6} {usd(c['liquidez_usd']):>8}  "
                  f"{e['nombre']} (${e['simbolo']})")
        for e in res[:3]:
            print("\n" + texto(e, a.controles))

    if a.enviar_top:
        top = res[:a.enviar_top]
        cab = {
            "title": f"\U0001F4E1 Radar pump.fun \u00b7 {len(res)} tokens analizados",
            "description": ("Los **%d** mejor puntuados de las últimas %.0f h.\n\n" % (len(top), a.max_edad)
                            + "\n".join(f"`{e['puntaje']:>5.1f}` {e['etiqueta']} \u2014 **{e['simbolo']}** "
                                         f"({usd(e['contexto']['mcap_usd'])})" for e in top)
                            + "\n\n-# Recordá: ~76% de los tokens nuevos de Solana son rug y solo el 4,55% "
                              "de los de pump.fun sobrevive 90 días."),
            "color": 0x5865F2,
            "footer": {"text": "solo informa, no opera"},
        }
        _res = enviar_embed(cab, contenido="\U0001F4E1 **Parte diario de memecoins**")
        log(f"resumen: {_res}")
        for e in top:
            _ok = alertar_token(e, titulo="\U0001F4CA Del parte diario")
            log(f"detalle {e['simbolo']}: {_ok}")
    if a.alerta:
        for e in buenos[:5]:
            log(f"alerta {e['simbolo']} ({e['puntaje']}): {enviar(texto(e))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
