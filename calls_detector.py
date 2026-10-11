#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Detector de RÁFAGAS (calls) para el radar de memecoins.

Qué detecta: en el POOL de un token, ≥ `MIN_WALLETS` wallets DISTINTAS comprando dentro de una
ventana de `VENTANA` segundos poniendo ≥ `MIN_SOL` SOL cada una. Eso no es compra suelta: es una
compra coordinada (call de un grupo de alpha, o bundle).

Se engancha al radar (`pump_analizador.vigilar`): cuando un token muestra la ráfaga, el aviso sale
al canal con la bandera ⚡ CALL DETECTADA, incluso si el puntaje no llegaba al umbral — porque la
señal es la ráfaga, no el puntaje.

TOPE DE GASTO: cada chequeo cuesta ~10 créditos por transacción leída (Helius). El detector lleva
un presupuesto diario en `presupuesto_calls.json` (default 25.000 créditos/día ≈ 95 chequeos) y se
corta solo cuando lo agota. Así no se come el cupo mensual sin avisar.
"""
import json
import os
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
import wallet_tracker as WT          # noqa: E402
import auditor_wallets as AU         # noqa: E402

PRESUPUESTO = os.path.join(AQUI, "presupuesto_calls.json")
TOPE_DIARIO = 400_000        # techo diario del detector (el usuario pidió maximizar; esto evita
#                              que una corrida loca se lleve el cupo de una sola pasada)
VENTANA = 240                 # ventana de ráfaga más ancha = más ráfagas entran (era 150)
MIN_WALLETS = 2               # con 2 wallets ya se avisa (antes 3): MÁS calls, más ruido
MIN_SOL = 0.1                 # mínimo por wallet más bajo (era 0.3)
# Umbral de AVISO (medido 07/10 sobre 8 calls): la única que ganó (2,5x) tenía 19 wallets y 55 SOL;
# todas las que perdieron tenían 3-7 wallets y ≤13,8 SOL. Se avisa sólo si supera UNO de los dos.
MIN_WALLETS_ALERTA = 8        # wallets distintas mínimas para avisar
SOL_ALERTA = 25               # o este SOL total en la ráfaga
TX_A_LEER = 20                # 20 tx por chequeo: alcanza para ver la ráfaga y permite revisar el
                              # DOBLE de tokens por el mismo crédito (más calls por peso gastado)
TITULOS = "⚡ RÁFAGA"


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def _hoy():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _leer_presupuesto():
    try:
        d = json.load(open(PRESUPUESTO))
        if d.get("dia") == _hoy():
            return d
    except Exception:
        pass
    return {"dia": _hoy(), "creditos": 0, "chequeos": 0}


def disponible():
    d = _leer_presupuesto()
    return max(0, TOPE_DIARIO - int(d.get("creditos", 0)))


def _anotar(creditos, chequeos=0):
    d = _leer_presupuesto()
    d["creditos"] = int(d.get("creditos", 0)) + creditos
    d["chequeos"] = int(d.get("chequeos", 0)) + chequeos
    try:
        json.dump(d, open(PRESUPUESTO, "w"))
    except Exception:
        pass


def pool_de(mint):
    """Dirección del pool (par con más liquidez) según DexScreener. No gasta créditos de RPC."""
    import pump_analizador as PA       # import diferido: evita el ciclo de imports
    try:
        d = PA.dexscreener(mint) or {}
    except Exception:
        return None
    return d.get("pairAddress")


def _usd(x):
    """Formato corto de USD (no existe el de pump_analizador acá; importarlo crearía un ciclo)."""
    try:
        x = float(x or 0)
    except Exception:
        return "?"
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}k"
    return f"${x:.0f}"


# Filtro de CALIDAD (medido el 08/10 sobre las 30 calls con medición del registro):
#   · edad <30 min ............ 0 de 14 llegaron a 1,5×  → se exige ≥25 min
#   · mcap $10-50k ............ 3 de 3 se hundieron      → se exige ≥$50k
#   · liquidez <$30k .......... 80-100 % se hundieron    → se exige ≥$30k
#   · 3-5 wallets / 1-5 SOL ... 0 de 25 llegaron a 1,5×  → se exige ≥5 wallets y ≥5 SOL
#   · la única de 3,18× (ZKDARK) tenía: edad 58 min · mcap $279k · liq $46k · 8 wallets · 11,5 SOL
CALIDAD = {"edad_min": 25, "mcap_min": 50_000, "liq_min": 30_000, "wallets_min": 5, "sol_min": 5,
           "caida_max": 0.5, "vertical_max": 400, "mcap_max": 500_000}
# `mcap_max`: medido el 08/10 — TODAS las calls de FDV alto se murieron (OIL $3,0M, IOF $3,1M,
# Speed $701k, Joker $608k, NASA $879k → todas a 0) y los ganadores del día salieron de $16k-$404k.
# Un token que ya llegó a medio millón no tiene de dónde hacer un múltiplo.


def pasa_calidad(raf, ctx):
    """¿Esta call merece un aviso? Devuelve (pasa, motivos de rechazo).

    Aplica el filtro de CALIDAD medido con datos propios: las calls de tokens recién nacidos, de mcap
    chico, con poca liquidez o con pocas wallets se fueron TODAS al piso. Las rechazadas igual quedan
    en el registro (se miden en silencio), así la regla se puede recalibrar con más datos.
    """
    c = ctx or {}
    falta = []
    edad = c.get("edad_min")
    mc = c.get("mcap_usd") or 0
    # dentro de la curva la liquidez de DexScreener es $0: lo que de verdad se puede sacar es el SOL
    # acumulado en la curva (salida_curva_usd). Sin esto, todos los tokens en curva pasaban el filtro.
    liq = c.get("liquidez_usd") or c.get("salida_curva_usd") or 0
    ath = c.get("ath_mcap_usd") or 0
    n = raf.get("n_wallets") or 0
    sol = raf.get("sol") or 0
    if edad is not None and edad < CALIDAD["edad_min"]:
        falta.append(f"token de {edad:.0f} min (mínimo {CALIDAD['edad_min']}: los de <30 min no dieron ni un 1,5×)")
    if liq and liq < CALIDAD["liq_min"]:
        falta.append(f"liquidez ${liq/1000:.0f}k menor a ${CALIDAD['liq_min']//1000}k")
    if mc and mc < CALIDAD["mcap_min"]:
        falta.append(f"mcap ${mc/1000:.0f}k menor a ${CALIDAD['mcap_min']//1000}k")
    if mc and mc > CALIDAD["mcap_max"]:
        falta.append(f"mcap ${mc/1000:.0f}k mayor a ${CALIDAD['mcap_max']//1000}k: ya corrió, "
                     f"no queda múltiplo (hoy TODAS las de FDV alto terminaron en 0)")
    if n < CALIDAD["wallets_min"]:
        falta.append(f"{n} wallets (mínimo {CALIDAD['wallets_min']}: con 3-5 ninguna pasó de 1,5×)")
    if sol < CALIDAD["sol_min"]:
        falta.append(f"{sol} SOL (mínimo {CALIDAD['sol_min']})")
    if ath and mc and mc < ath * CALIDAD["caida_max"]:
        falta.append(f"ya cayó {(1 - mc / ath) * 100:.0f}% desde su máximo (${ath/1000:.0f}k): sería entrar al rebote")
    ch1 = c.get("cambio_1h")
    if ch1 is not None and ch1 > CALIDAD["vertical_max"]:
        falta.append(f"ya subió {ch1:.0f}% en 1 h: entrar ahora es ser la salida de otro")
    return (not falta), falta


def puntuar(raf, ctx):
    """Puntúa una CALL de 0 a 10 y explica CADA punto. Devuelve (nota, motivos[], etiqueta).

    Base empírica (medida el 07/10 sobre las 8 calls del canal): la única que ganó (TMNP, 2,5x)
    tenía **19 wallets distintas y 55 SOL**; TODAS las que perdieron tenían **3-7 wallets y ≤13,8 SOL**
    — y el mcap NO protegió a nadie (una de $130k se fue a −99 %). Por eso el peso principal está en
    la fuerza de la ráfaga, no en el mcap.
    """
    p, motivos = 0.0, []
    w = raf.get("n_wallets") or 0
    sol = raf.get("sol") or 0
    slots_iguales = raf.get("slots_iguales") or 1
    frescas = raf.get("frescas") or 0
    conocidas = raf.get("conocidas") or 0
    mcap = ctx.get("mcap_usd") or 0
    liq = ctx.get("liquidez_usd") or 0
    curva = ctx.get("curva_pct") or 0
    edad = ctx.get("edad_min") or 0

    # --- 1. FUERZA DE LA RÁFAGA: cuántas wallets DISTINTAS entraron (el predictor que funcionó) ---
    if w >= 15:
        p += 4.0
        motivos.append(f"**+4,0 fuerza** · {w} wallets distintas entraron: eso es demanda real, "
                       f"no un par de amigos")
    elif w >= 10:
        p += 3.5
        motivos.append(f"**+3,5 fuerza** · {w} wallets distintas")
    elif w >= 8:
        p += 3.0
        motivos.append(f"**+3,0 fuerza** · {w} wallets distintas")
    elif w >= 5:
        p += 1.5
        motivos.append(f"**+1,5 fuerza** · {w} wallets (flojo)")
    else:
        p += 0.5
        motivos.append(f"**+0,5 fuerza** · solo {w} wallets: 2-3 wallets no es una call, "
                       f"es un par de manos")

    # --- 2. TAMAÑO DEL DINERO QUE ENTRÓ ---
    if sol >= 50:
        p += 3.0
        motivos.append(f"**+3,0 volumen** · {sol} SOL en la ráfaga (≥50 = plata de verdad)")
    elif sol >= 25:
        p += 2.0
        motivos.append(f"**+2,0 volumen** · {sol} SOL")
    elif sol >= 10:
        p += 1.0
        motivos.append(f"**+1,0 volumen** · {sol} SOL")
    else:
        motivos.append(f"**+0,0 volumen** · {sol} SOL: entrada muy chica")

    # --- 3. CONTEXTO DEL TOKEN ---
    if mcap >= 150_000:
        p += 2.0
        motivos.append(f"**+2,0 token con cuerpo** · mcap {_usd(mcap)} y {ctx.get('holders') or '?'} holders")
    elif mcap >= 100_000:
        p += 1.5
        motivos.append(f"**+1,5 token con cuerpo** · mcap {_usd(mcap)}")
    elif mcap >= 50_000:
        p += 0.5
        motivos.append(f"**+0,5 token mediano** · mcap {_usd(mcap)}")
    else:
        p -= 1.5
        motivos.append(f"**−1,5 token chico** · mcap {_usd(mcap)}: en esta liga (<$50k) TODAS las "
                       f"calls de hoy se hundieron")
    if liq >= 20_000 or curva >= 50:
        p += 1.0
        motivos.append(f"**+1,0 salida disponible** · liquidez {_usd(liq)} · curva {curva}%")
    elif liq < 5_000 and curva < 25:
        p -= 1.0
        motivos.append(f"**−1,0 sin salida** · liquidez {_usd(liq)} y curva {curva}%: si comprás, "
                       f"después no vas a poder vender")

    # --- 4. SEÑALES DE BUNDLE (malas) ---
    if slots_iguales >= 3:
        p -= 2.0
        motivos.append(f"**−2,0 bundle** · {slots_iguales} de las compras cayeron en el MISMO slot "
                       f"(misma fracción de segundo): es un operador con varias wallets, no una call")
    if frescas and frescas >= max(2, w * 0.6):
        p -= 1.5
        motivos.append(f"**−1,5 carteras nuevas** · {frescas} de {w} son wallets sin historial "
                       f"(patrón de bundle)")
    if conocidas:
        p += min(1.5, 0.75 * conocidas)
        motivos.append(f"**+{min(1.5, 0.75 * conocidas):.2f} caras conocidas** · {conocidas} de las "
                       f"wallets ya las tenemos puntuadas")
    if 0 < edad < 10:
        p -= 1.0
        motivos.append(f"**−1,0 token de {edad:.0f} min** · ventana de snipers")

    # --- 5. DISCRIMINADORES PROPIOS (lo que ya construimos en el proyecto) ---
    # 5.a wallets de GRUPOS TRACKEADOS (cohortes que repiten comprando juntas + insiders verificados).
    # Es la señal de "info privilegiada": si alguien que viene acertando está adentro, pesa más que
    # cualquier indicador de precio.
    try:
        _aqui = os.path.dirname(os.path.abspath(__file__))
        tracks = set()
        for c in (json.load(open(os.path.join(_aqui, "cohortes.json"))).get("cohortes") or []):
            tracks |= set(c.get("wallets") or [])
        for f in ("cluster_devs_verificado.json", "devs_insiders.json"):
            try:
                d = json.load(open(os.path.join(_aqui, f)))
                if isinstance(d, dict):
                    for k, v in d.items():
                        if isinstance(v, dict) and v.get("insider"):
                            tracks.add(k)
            except Exception:
                pass
        dentro = [w for w in (raf.get("wallets") or []) if w in tracks]
        if dentro:
            bono = 3.0 if len(dentro) >= 2 else 1.5
            p += bono
            motivos.append(f"**+{bono:.1f} grupo trackeado** · {len(dentro)} wallet(s) que ya venimos "
                           f"siguiendo entraron en esta ráfaga (info privilegiada)")
    except Exception:
        pass
    # 5.b RUGCHECK: score_normalised es RIESGO (más bajo = más sano), no calidad
    rk = ctx.get("rugcheck_score")
    if isinstance(rk, (int, float)):
        if rk >= 80:
            p -= 4.0
            motivos.append(f"**−4,0 RugCheck {rk:.0f}/100 (riesgo)** · marcado como peligroso")
        elif rk >= 50:
            p -= 2.0
            motivos.append(f"**−2,0 RugCheck {rk:.0f}/100 (riesgo)** · riesgo alto")
        elif rk <= 20:
            p += 1.0
            motivos.append(f"**+1,0 RugCheck {rk:.0f}/100 (riesgo)** · limpio")
    # 5.c red de insiders detectada por RugCheck: varias wallets conectadas comprando lo mismo
    ins = ctx.get("insiders_detectados") or 0
    if ins:
        pen = min(3.0, 1.5 * ins)
        p -= pen
        motivos.append(f"**−{pen:.1f} red de insiders** · RugCheck detectó {ins} wallet(s) conectadas "
                       f"alrededor del token")
    # 5.d concentración: si pocas manos tienen casi todo, te barren el precio
    t10 = ctx.get("top10_pct")
    if isinstance(t10, (int, float)):
        if t10 > 50:
            p -= 2.5
            motivos.append(f"**−2,5 concentrado** · top 10 con {t10:.0f}% del supply: un dump coordinado te borra")
        elif t10 > 35:
            p -= 1.5
            motivos.append(f"**−1,5 concentrado** · top 10 con {t10:.0f}%")
        elif t10 < 20:
            p += 1.0
            motivos.append(f"**+1,0 repartido** · top 10 con {t10:.0f}% del supply")
    # 5.e creador que lanza en serie (registro propio, se llena solo con cada token que analizamos)
    try:
        reg = json.load(open(os.path.join(_aqui, "creadores.json")))
        n_cre = len(((reg.get(ctx.get("creador")) or {}).get("lanzamientos")) or [])
        if n_cre >= 5:
            p -= 1.5
            motivos.append(f"**−1,5 lanzador serial** · este creador ya largó {n_cre} tokens (vistos por nosotros)")
        elif n_cre >= 3:
            p -= 0.75
            motivos.append(f"**−0,75 creador repetido** · {n_cre} lanzamientos")
    except Exception:
        pass
    if ctx.get("rugado"):
        p = 0.0
        motivos.append("**RUG** · RugCheck ya lo marcó como rug: descartado")

    p = max(0.0, min(10.0, p))
    if p >= 8:
        etq = "🔥 ALTA"
    elif p >= 6:
        etq = "✅ MEDIA-ALTA"
    elif p >= 4:
        etq = "⚠️ MEDIA"
    else:
        etq = "🚫 BAJA"
    return round(p, 1), motivos, etq


def conocidas():
    """Wallets ya puntuadas por nosotros (seguidas + candidatas verificadas). Para saber si la
    ráfaga la hicieron carteras CONOCIDAS (posible call de un grupo) o carteras FRESCAS (bundle)."""
    ws = set()
    for archivo, campo in (("wallets.json", "wallets"), ("publicas_verificadas.json", None),
                           ("publicas_vivas.json", None)):
        try:
            d = json.load(open(os.path.join(AQUI, archivo)))
        except Exception:
            continue
        if isinstance(d, dict) and campo:
            for x in d.get(campo) or []:
                ws.add(x.get("address") or x.get("wallet"))
        elif isinstance(d, list):
            for x in d:
                ws.add(x.get("wallet") or x.get("wallet_address"))
    return {w for w in ws if w}


def es_fresca(wallet):
    """¿La wallet es nueva? (pocas tx y sin plata). Es la firma de un bundle/call coordinada:
    un grupo real de alpha usa carteras con historia; un bundle usa carteras frescas."""
    try:
        sigs = WT.rpc("getSignaturesForAddress", [wallet, {"limit": 20}]) or []
        saldo = ((WT.rpc("getBalance", [wallet]) or {}).get("value") or 0) / 1e9
    except Exception:
        return None
    return len(sigs) < 20 and saldo < 0.5


def detectar(pool, mint, tx=TX_A_LEER, ventana=VENTANA, min_wallets=MIN_WALLETS, min_sol=MIN_SOL):
    """Busca la ráfaga más grande en las últimas `tx` transacciones del pool.

    Devuelve (ráfaga|None, créditos_gastados). La ráfaga trae: wallets, SOL total, duración y hora.
    """
    sigs = WT.rpc("getSignaturesForAddress", [pool, {"limit": tx}]) or []
    gastado = 10
    compras = []
    for s in sigs:
        if s.get("err"):
            continue
        try:
            txd = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                            "encoding": "jsonParsed"}])
        except Exception:
            txd = None
        gastado += 10
        time.sleep(0.02)
        if not txd or not txd.get("meta") or txd["meta"].get("err"):
            continue
        meta = txd["meta"]
        try:
            keys = WT.cuentas_tx(txd)
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
                compras.append({"wallet": owner, "sol": -d_sol, "ts": s.get("blockTime") or 0,
                                "slot": txd.get("slot")})
    fuertes = [c for c in compras if c["sol"] >= min_sol and c["ts"]]
    fuertes.sort(key=lambda c: c["ts"])
    mejor = None
    i = 0
    while i < len(fuertes):
        j = i
        grupo = []
        while j < len(fuertes) and fuertes[j]["ts"] - fuertes[i]["ts"] <= ventana:
            grupo.append(fuertes[j])
            j += 1
        ws = {g["wallet"] for g in grupo}
        if len(ws) >= min_wallets:
            cand = {"n_wallets": len(ws), "sol": round(sum(g["sol"] for g in grupo), 2),
                    "segundos": fuertes[j - 1]["ts"] - fuertes[i]["ts"],
                    "hora": datetime.fromtimestamp(fuertes[i]["ts"], timezone.utc).strftime("%H:%M") + " UTC",
                    "ts_iso": datetime.fromtimestamp(fuertes[i]["ts"], timezone.utc).isoformat(),
                    "desde_ts": fuertes[i]["ts"], "hasta_ts": fuertes[j - 1]["ts"],
                    "slots": [g.get("slot") for g in grupo],
                    "wallets": sorted(ws)}
            if not mejor or cand["sol"] > mejor["sol"]:
                mejor = cand
            i = j
        else:
            i += 1

    # --- enriquecido del mejor hallazgo (señales que pide la investigación) ---
    if mejor:
        mejor["compras_en_ventana"] = sum(1 for c in compras
                                          if mejor["desde_ts"] <= c["ts"] <= mejor["hasta_ts"])
        cuenta_slots = {}
        for sl in mejor.get("slots") or []:
            cuenta_slots[sl] = cuenta_slots.get(sl, 0) + 1
        mejor["slots_iguales"] = max(cuenta_slots.values()) if cuenta_slots else 1
        cono = conocidas()
        frescas = conocidas_n = 0
        for w in mejor["wallets"]:
            if w in cono:
                conocidas_n += 1
            elif es_fresca(w):
                frescas += 1
            gastado += 20                       # 10 por las firmas + 10 por el saldo
        mejor["frescas"] = frescas
        mejor["conocidas"] = conocidas_n
    return mejor, gastado


def revisar(candidatos, max_por_pasada=2):
    """Chequea hasta `max_por_pasada` candidatos (dicts con 'mint') y devuelve los que tienen ráfaga.

    Respeta el presupuesto diario: si no queda, no hace nada (y lo dice).
    """
    if disponible() <= 300:
        log("  detector de ráfagas: presupuesto diario agotado")
        return []
    con_rafaga = []
    for c in candidatos[:max_por_pasada]:
        pool = pool_de(c.get("mint"))
        if not pool:
            continue
        raf, gastado = detectar(pool, c["mint"])
        _anotar(gastado, 1)
        if raf:
            raf["pool"] = pool
            con_rafaga.append({**c, "call": raf})
            log(f"  ⚡ RÁFAGA en {c.get('simbolo') or c['mint'][:6]}: {raf['n_wallets']} wallets · "
                f"{raf['sol']} SOL · {raf['segundos']}s")
    return con_rafaga


def texto_rafaga(raf):
    """Bloque para el embed/texto del aviso."""
    return (f"⚡ **CALL DETECTADA** — {raf['n_wallets']} wallets entraron en {raf['segundos']} s\n"
            f"› {raf['sol']} SOL en total · arrancó {raf['hora']}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Detector de ráfagas (prueba suelta)")
    ap.add_argument("mint")
    ap.add_argument("--tx", type=int, default=TX_A_LEER)
    a = ap.parse_args()
    pool = pool_de(a.mint)
    print("pool:", pool, "· presupuesto disponible hoy:", disponible())
    if pool:
        raf, gastado = detectar(pool, a.mint, a.tx)
        _anotar(gastado, 1)
        print("gastado:", gastado, "créditos")
        print("RÁFAGA:", json.dumps(raf, ensure_ascii=False) if raf else "ninguna")
