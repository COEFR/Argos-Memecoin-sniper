#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
COHORTES PERSISTENTES — ver las calls de los grupos SIN entrar a los grupos y SIN listas de KOLs.

Por qué así (y no siguiendo wallets conocidas): si copiás wallets que todo el mundo ya tiene en su
lista, comprás donde ellos venden — sos la liquidez de salida. La única forma que no depende de
etiquetas es **identificar a los miembros por su propio comportamiento en la cadena**:

  1. **Cosecha**: de cada token recién lanzado se guardan las compras de los primeros minutos
     (wallet, SOL, segundo, slot).
  2. **Co-compras**: wallets que compran el MISMO token casi al mismo tiempo (misma ventana/slot).
     Un par que coincide una vez puede ser casualidad; **el par que coincide en varios lanzamientos
     distintos es un grupo**.
  3. **Union-find** sobre el grafo de co-compras → **cohortes** (el método de arXiv 2607.02795, que
     así encontró 1.012 anillos persistentes).
  4. Para cada cohorte se identifica el **ORIGEN**: la wallet que entra primero (el líder), y se mide
     cómo terminaron los tokens que tocaron. Eso es lo que sirve: seguir al que entra ANTES, no a la
     cola que copia.

Uso:
  python3 cohortes.py --cosechar 12         # guarda las compras de hasta 12 tokens jóvenes (cron 15 min)
  python3 cohortes.py --armar               # rehace los cohortes con todo lo cosechado
  python3 cohortes.py --informe             # qué cohortes hay, quién es el origen y cómo les fue
  python3 cohortes.py --vigilar             # ¿entró algún cohorte a un token joven AHORA?
"""
import argparse
import json
import statistics
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT          # noqa: E402
import auditor_wallets as AU         # noqa: E402
import pump_analizador as PA         # noqa: E402
import calls_detector as CD          # noqa: E402
import insider_token as IT           # noqa: E402

COSECHA = os.path.join(AQUI, "lanzamientos.json")
COHORTES = os.path.join(AQUI, "cohortes.json")
MIN_SOL = 0.1             # mínimo por comprador (era 0.2): entran más wallets por lanzamiento
VENTANA_CO = 300          # ventana de co-compra más ancha (era 180 s): más parejas detectadas
MIN_PAREJA = 2            # veces que un par tiene que coincidir para unirlos (esto NO se baja:
                          # con 1 coincidencia el grafo se llena de casualidades)
MIN_COHORTE = 2           # desde 2 wallets (era 3): primero aparecen "dúos" y se van agrandando


CREDITOS = os.path.join(AQUI, "cohortes_creditos.json")


def anotar_creditos(n):
    """Lleva el gasto del sistema nuevo (para avisar antes de quedarnos sin cupo)."""
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        d = json.load(open(CREDITOS))
        if d.get("dia") != hoy:
            d = {"dia": hoy, "creditos": 0}
    except Exception:
        d = {"dia": hoy, "creditos": 0}
    d["creditos"] = int(d.get("creditos", 0)) + n
    import jsonio
    jsonio.guardar(CREDITOS, d)
    return d["creditos"]


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def canal_argos():
    """Canal dedicado #argos (sistema de cohortes). Si la clave no está, se usa el canal de siempre."""
    try:
        for l in open(os.path.expanduser("~/.hermes/.env")):
            if l.startswith("DISCORD_ARGOS_CHANNEL="):
                v = l.split("=", 1)[1].strip().strip('"').strip("'")
                return v or None
    except Exception:
        pass
    return None


def canal_mejores_id():
    """ID del canal de las MEJORES calls (puntaje de calidad ≥7)."""
    try:
        for l in open(os.path.expanduser("~/.hermes/.env")):
            if l.startswith("DISCORD_ARGOS_MEJORES="):
                v = l.split("=", 1)[1].strip().strip('"').strip("'")
                return v or None
    except Exception:
        pass
    return None


def calidad_call(raf, ctx, punt=None):
    """Puntaje 0-10 de una CALL (para el canal de las mejores). Devuelve (nota, motivos).

    Combinación medida el 08/10 (lo que separó a los ganadores de los perdedores):
      · PnL de copia de las wallets que entran .... el factor más fuerte (dan plata o no)
      · convicción: SOL que pusieron .............. ≤0,3 SOL es polvo, no una call
      · salud del token: liquidez, holders, top-10, franja de edad 15-90 min
      · no haber YA hecho +200% ................... si ya corrió, entrar es ser la salida
      · nivel del grupo (tier) .................... como contexto, no como excusa
    """
    c = ctx or {}
    punt = punt if punt is not None else (puntajes_wallets() or {})
    p, mot = 5.0, []
    # 1) las wallets que entran
    ws = [punt.get(w) or {} for w in (raf.get("wallets") or [])]
    mejores = [v.get("pnl_copia") or 0 for v in ws if v.get("n")]
    top_pnl = max(mejores) if mejores else 0
    if top_pnl >= 10:
        p += 2.5
        mot.append(f"+2,5 wallets copiables dentro (mejor PnL +{top_pnl:.0f})")
    elif top_pnl >= 3:
        p += 1.5
        mot.append(f"+1,5 wallets con historial positivo (mejor +{top_pnl:.0f})")
    elif top_pnl >= 0 and mejores:
        p += 0.5
        mot.append("+0,5 wallets sin historial negativo")
    else:
        p -= 2.0
        mot.append("−2,0 ninguna wallet con historial positivo")
    # 2) convicción
    sol = raf.get("sol") or 0
    if sol >= 3:
        p += 1.5
        mot.append(f"+1,5 convicción ({sol} SOL)")
    elif sol >= 1:
        p += 1.0
        mot.append(f"+1,0 entrada seria ({sol} SOL)")
    elif sol >= 0.5:
        p += 0.5
        mot.append(f"+0,5 entrada chica ({sol} SOL)")
    else:
        p -= 2.0
        mot.append(f"−2,0 polvo ({sol} SOL): no es una call")
    # 3) salud del token
    liq = c.get("liquidez_usd") or c.get("salida_curva_usd") or 0
    if liq >= 30_000:
        p += 1.0
        mot.append("+1,0 liquidez sólida")
    elif liq >= 8_000:
        p += 0.5
        mot.append("+0,5 liquidez aceptable")
    elif liq:
        p -= 1.5
        mot.append(f"−1,5 liquidez baja (${liq/1000:.0f}k): no vas a poder salir")
    h = c.get("holders") or 0
    if h >= 100:
        p += 0.5
    elif 0 < h < 40:
        p -= 0.5
        mot.append(f"−0,5 pocos holders ({h})")
    t10 = c.get("top10_pct")
    if isinstance(t10, (int, float)):
        if t10 > 50:
            p -= 1.5
            mot.append(f"−1,5 concentrado (top10 {t10:.0f}%)")
        elif t10 < 25:
            p += 0.5
    # 4) franja de edad (los ganadores del día: 15-90 min)
    ed = c.get("edad_min")
    if isinstance(ed, (int, float)):
        if 15 <= ed <= 90:
            p += 0.5
            mot.append("+0,5 está en la franja de edad de los ganadores (15-90 min)")
        elif ed < 10:
            p -= 0.5
            mot.append("−0,5 recién nacido (ventana de snipers)")
        elif ed > 240:
            p -= 1.0
            mot.append("−1,0 token viejo (la ventana ya pasó)")
    # 5) ¿ya corrió?
    ch1 = c.get("cambio_1h")
    if isinstance(ch1, (int, float)) and ch1 > 200:
        p -= 2.0
        mot.append(f"−2,0 ya subió {ch1:.0f}% en 1 h: entrar ahora es ser la salida")
    # 6) nivel del grupo (contexto)
    t = (raf.get("tier") or "")
    if t.startswith("🟢"):
        p += 1.0
        mot.append("+1,0 grupo que seguimos")
    elif t.startswith("🔴"):
        p -= 1.0
        mot.append("−1,0 grupo descartado")
    return max(0.0, min(10.0, round(p, 1))), mot


def canal_para(tier=None):
    """El canal que le toca al aviso según la calidad del grupo (los tres sub-canales de Argos).

    🟢 seguir → #🟢-seguir · 🟠 lotería → #🟠-lotería · 🟡/🔴 → #🟡-observar-·-🔴-descartar.
    Si la clave no está en .env, cae al #argos general (así nunca se pierde un aviso).
    """
    t = (tier or "").lower()
    if not t:
        # sin clasificar todavía: va al general, NUNCA a un canal de nivel (si no, todo cae en "observar")
        return canal_argos()
    clave = ("DISCORD_ARGOS_SEGUIR" if "seguir" in t else
             "DISCORD_ARGOS_LOTERIA" if "loter" in t else "DISCORD_ARGOS_OBSERVAR")
    try:
        for l in open(os.path.expanduser("~/.hermes/.env")):
            if l.startswith(clave + "="):
                v = l.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except Exception:
        pass
    return canal_argos()


def _leer(path, dflt):
    try:
        return json.load(open(path))
    except Exception:
        return dflt


def _guardar(path, datos):
    """Escribe el JSON atómico y SIN pisarse entre procesos.

    Trampa medida (08/10): con un temporal de nombre fijo (`path + ".tmp"`), el cron de la patrulla
    (cada 3 min) y el de la cosecha (cada 15) escribían a la vez: uno renombraba el .tmp y el otro
    moría con FileNotFoundError. Como el proceso muere ANTES de clasificar, los grupos quedaban sin
    tier y los avisos sin canal (todos cayendo al general). El temporal lleva el PID y hay reintentos.
    """
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(datos, fh)
    except Exception:
        return
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except Exception:
            time.sleep(0.08)
    try:
        os.remove(tmp)
    except Exception:
        pass


TX_POR_LANZAMIENTO = 40       # hasta 40 compradores por lanzamiento: más parejas → cohortes antes
RE_COSECHA_MIN = 30           # a los 30 min de la primera lectura se vuelve a leer (la compra sigue)
RE_COSECHA_EDAD_H = 2         # sólo se re-cosechan lanzamientos de menos de 2 h


def compras_de(mint, limite=TX_POR_LANZAMIENTO):
    """Compras de las últimas `limite` tx del token: {wallet: {sol, ts, slot}}."""
    sigs = WT.rpc("getSignaturesForAddress", [mint, {"limit": limite}]) or []
    anotar_creditos(10 * (1 + len(sigs)))      # cada getTransaction cuesta 10 créditos
    out = {}
    # Las transacciones se bajan EN PARALELO. En serie era una llamada por transacción (hasta 40,
    # ~0,3 s cada una) y una pasada de 8 tokens tardaba más de 5 minutos, más que el intervalo del
    # cron: por eso las calls de cohorte llegaban tarde. Medido el 12/10.
    from concurrent.futures import ThreadPoolExecutor

    def _traer(sig):
        if sig.get("err"):
            return None
        try:
            return WT.rpc("getTransaction", [sig["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                                "encoding": "jsonParsed"}])
        except Exception:
            return None

    # 5 hilos (no 10) por decisión conservadora: el pico pasa de 80 a 40 pedidos simultáneos y
    # sigue siendo varias veces más rápido que en serie.
    with ThreadPoolExecutor(max_workers=5) as ex:
        txs = list(ex.map(_traer, sigs))
    # zip para conservar la firma original junto a su transacción: el cuerpo de abajo usa los dos
    # (el "s" es la firma, de donde salen la hora y el slot). Sin esto fallaba con NameError.
    for s, tx in zip(sigs, txs):
        if not tx or not tx.get("meta") or tx["meta"].get("err"):
            continue
        meta = tx["meta"]
        try:
            keys = WT.cuentas_tx(tx)
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
            if d_sol <= -MIN_SOL:
                # El PAGADOR de la comisión (primera cuenta de la tx) suele ser el OPERADOR: si varias
                # wallets compran con el mismo pagador, son la misma mano. Es la heurística estándar de
                # clustering en Solana (así agrupa Bubblemaps) y no la estábamos guardando.
                pagador = None
                try:
                    ak = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
                    primero = ak[0]
                    pagador = primero.get("pubkey") if isinstance(primero, dict) else primero
                except Exception:
                    pass
                e = out.setdefault(owner, {"sol": 0.0, "ts": s.get("blockTime") or 0, "slot": tx.get("slot"),
                                           "pagador": pagador})
                if pagador and not e.get("pagador"):
                    e["pagador"] = pagador
                e["sol"] += -d_sol
                e["ts"] = min(e["ts"] or 9e18, s.get("blockTime") or 0) if e["ts"] else (s.get("blockTime") or 0)
    return out


def cosechar(max_tokens=12):
    """Guarda las compras de los tokens jóvenes que todavía no cosechamos."""
    datos = _leer(COSECHA, {"tokens": {}})
    tokens = datos.setdefault("tokens", {})
    # La lista del radar SÓLO se usa si está fresca: desde que el radar está apagado (solo Argos),
    # leerla sería trabajar con tokens de hace horas. Si está vieja, la cosecha arma la suya.
    jovenes = []
    try:
        _p = os.path.join(AQUI, "tokens_jovenes.json")
        if time.time() - os.path.getmtime(_p) < 1800:
            jovenes = list(json.load(open(_p)))
    except Exception:
        jovenes = []
    # SI LA LISTA DEL RADAR SE QUEDA CORTA, la cosecha arma la suya con los lanzamientos más nuevos
    # de pump.fun. Medido: con sólo la lista del radar entraba ~1 token nuevo por pasada (el ritmo
    # real era 4/día, no 1.920) — el freno no era el cupo sino la oferta de tokens.
    faltan = max_tokens - len([t for t in jovenes if t.get("mint") not in tokens])
    if faltan > 0:
        ahora_ms = time.time() * 1000
        for fuente in (PA.pump_nuevos, PA.pump_live, PA.pump_movidos):
            try:
                for c in (fuente(120) or []):
                    if faltan <= 0:
                        break
                    m = c.get("mint")
                    edad_h = (ahora_ms - (c.get("created_timestamp") or 0)) / 3600000
                    if (not m or m in tokens or edad_h > 4 or edad_h < 0
                            or (c.get("usd_market_cap") or 0) < 5_000):
                        continue
                    tiene_pool = False
                    try:
                        tiene_pool = bool(PA.dexscreener(m))
                    except Exception:
                        pass
                    if not tiene_pool:
                        continue
                    jovenes.append({"mint": m, "simbolo": c.get("symbol"), "creado": ahora_ms / 1000,
                                    "fuente": "pump_lists"})
                    faltan -= 1
            except Exception:
                continue
    vistos = 0
    for t in jovenes:
        m = t.get("mint")
        if not m or m in tokens:
            continue
        compras = compras_de(m)
        if not compras:
            tokens[m] = {"simbolo": t.get("simbolo"), "compras": {}, "sin_datos": True}
            continue
        mc = None
        try:
            p = PA.dexscreener(m) or {}
            mc = p.get("marketCap") or p.get("fdv")
        except Exception:
            pass
        tokens[m] = {"simbolo": t.get("simbolo"), "mcap_cosecha": mc,
                     "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "ultima_lectura": time.time(), "compras": compras}
        vistos += 1
        log(f"  cosechado {t.get('simbolo')}: {len(compras)} compradores"
            + (f" · mcap {CD._usd(mc)}" if mc else ""))
        if vistos >= max_tokens:
            break
    # --- re-cosecha: la compra del lanzamiento SIGUE después de la primera lectura. Se vuelve a leer
    # a los ~30 min y se SUMAN los compradores nuevos (es lo que hace que el grafo tenga más parejas y
    # los cohortes aparezcan antes).
    ahora = time.time()
    recientes = [(m, d) for m, d in tokens.items()
                 if (d.get("compras") or {}) and d.get("fecha")
                 and (ahora - datetime.fromisoformat(d["fecha"]).timestamp()) < RE_COSECHA_EDAD_H * 3600
                 and (ahora - d.get("ultima_lectura", 0)) > RE_COSECHA_MIN * 60]
    # TOPE A LA RE-COSECHA (medido el 09/10): re-leer 90 tokens por pasada, 4 veces por hora, era el
    # mayor gasto del sistema (~1,8 M de créditos estimados por día, más que todo lo demás junto). Se
    # re-cosechan sólo los 20 más recientes: el resto ya tiene sus compradores y volver a leerlos agrega
    # poco.
    recientes = sorted(recientes, key=lambda x: x[1].get("ultima_lectura") or 0)[:20]
    for m, d in recientes:
        antes = len(d["compras"])
        nuevas = compras_de(m)
        d["compras"].update(nuevas)
        d["ultima_lectura"] = ahora
        if len(d["compras"]) > antes:
            log(f"  re-cosechado {d.get('simbolo')}: {antes} → {len(d['compras'])} compradores")
    _guardar(COSECHA, datos)
    log(f"  lanzamientos en la cosecha: {len(tokens)} · re-cosechados ahora: {len(recientes)}")
    return tokens


class Unión:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def unir(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra

    def grupos(self, minimo=MIN_COHORTE):
        g = defaultdict(list)
        for x in list(self.p):
            g[self.find(x)].append(x)
        return [sorted(v) for v in g.values() if len(v) >= minimo]


def funder_original(w, cuantas=6, max_paginas=12):
    """Quién le mandó los PRIMEROS SOL a la wallet (el fondeo de origen).

    Por qué no se usa funder_de(): ese mira las ÚLTIMAS tx, así que en una wallet que ya operó
    encuentra un pagador reciente (una pool, un bot) y nunca el origen. Para decir "misma granja" lo
    que importa es quién la financió al nacer: por eso se leen las tx MÁS VIEJAS.
    """
    try:
        viejas, paginas, total = IT.sigs_mas_viejas(w, cuantas, max_paginas=max_paginas)
    except Exception:
        return None                      # None = no se pudo comprobar (no es lo mismo que "sin fondeo")
    if not viejas:
        return None
    if total >= max_paginas * 1000:
        # El historial es más largo de lo que se paginó → no se llegó al origen: se dice "sin
        # determinar" en vez de inventar un fondeador leyendo un tramo del medio del historial.
        return None
    fonders = []
    for s in (viejas if isinstance(viejas, list) else []):
        sig = s.get("signature") if isinstance(s, dict) else s
        if not sig:
            continue
        try:
            tx = WT.rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                 "encoding": "jsonParsed"}])
        except Exception:
            continue
        anotar_creditos(10)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        try:
            cuentas = WT.cuentas_tx(tx)
            i = cuentas.index(w)
        except Exception:
            continue
        if (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9 > 0.05:      # recibió SOL
            try:
                j = max(range(len(meta["preBalances"])), key=lambda k: meta["preBalances"][k])
                if j != i and meta["postBalances"][j] < meta["preBalances"][j]:
                    fonders.append(cuentas[j])
            except Exception:
                pass
    return fonders


TIERS = os.path.join(AQUI, "grupos_tier.json")
MCAP_CACHE = os.path.join(AQUI, "mcap_cache.json")


def _medir_entradas(c):
    """Registro medido de un grupo: cuántas entradas, mediana, % ≥1,5x, mejor y qué FDV compra.

    Usa primero los picos ya medidos del registro de calls (gratis) y, si no hay, el FDV de ahora
    (cacheado 30 min) contra el de la cosecha. Sin esto, "filtrar grupos" sería a ojo.
    """
    ws = set(c["wallets"])
    entradas = []
    for m, d in ({} if not os.path.exists(COSECHA) else _leer(COSECHA, {}).get("tokens", {})).items():
        compr = [w for w in (d.get("compras") or {}) if w in ws]
        if len(compr) >= 2 and (d.get("mcap_cosecha") or 0) > 0:
            entradas.append((m, d))
    try:
        cache = _leer(MCAP_CACHE, {})
    except Exception:
        cache = {}
    vals, vals_por_entrada, fdv, cambió = [], [], [], False
    mejor_valor = 0
    for m, d in entradas:
        fdv.append(d["mcap_cosecha"])
        pr = None
        try:
            import registro_calls as RC
            for x in (RC._leer() or []):
                if x.get("mint") == m:
                    seg = [s for s in (x.get("seguimiento") or []) if s.get("mcap")]
                    if len(seg) >= 2 and x.get("mcap_call"):
                        pr = max(s["mcap"] for s in seg) / x["mcap_call"]
                    break
        except Exception:
            pass
        if pr is None:
            e = cache.get(m)
            if not e or (time.time() - (e.get("t") or 0)) > 1800:
                mc = None
                try:
                    p = PA.dexscreener(m)
                    mc = (p.get("marketCap") or p.get("fdv")) if p else None
                except Exception:
                    mc = None
                e = {"mc": mc, "t": time.time()}
                cache[m] = e
                cambió = True
                time.sleep(0.12)
            if e.get("mc"):
                pr = e["mc"] / d["mcap_cosecha"]
        vals_por_entrada.append(pr)
        if pr:
            vals.append(pr)
    if cambió:
        _guardar(MCAP_CACHE, cache)
    # el token del mejor caso: es con el que el grupo se hace memorable ("los de Mario64")
    mejor_tok, mejor_mint = None, None
    for (m, d), v in zip(entradas, vals_por_entrada):
        if v and (mejor_tok is None or v > mejor_valor):
            mejor_tok, mejor_mint, mejor_valor = d.get("simbolo"), m, v
    return {"n": len(vals), "mediana": statistics.median(vals) if vals else None,
            "mejor_token": mejor_tok, "mejor_mint": mejor_mint, "mejor_valor": mejor_valor,
            "hit": (sum(1 for v in vals if v >= 1.5) / len(vals)) if vals else None,
            "mejor": max(vals) if vals else None, "peor": min(vals) if vals else None,
            "fdv_compra": statistics.median(fdv) if fdv else None}


def clasificar():
    """Ordena los grupos en tiers según lo MEDIDO, no según el tamaño.

    Criterios (sacados de los datos del 08/10):
      🔴 DESCARTAR  · compra tokens de FDV alto (>$500k: se midieron TODAS en 0), o mediana <0,6x con
                      ≥3 entradas (pierde sistemáticamente), o es granja de bots.
      🟠 LOTERÍA    · mediana baja pero con golpes (hit ≥10% o mejor ≥3x): avisa, pero marcado — la
                      mediana dice que se pierde y el que paga es el golpe aislado.
      🟢 SEGUIR     · ≥2 entradas, mediana ≥1,05x y (hit ≥15% o mejor ≥1,5x), comprando micro/mid.
      🟡 OBSERVAR   · menos de 2 entradas medidas: todavía no hay con qué juzgarlo.
    """
    d = _leer(COHORTES, None)
    if not d or not d.get("cohortes"):
        log("  (sin cohortes para clasificar)")
        return {}
    resumen = {}
    for c in d["cohortes"]:
        try:
            r = _medir_entradas(c)
        except Exception as e:
            c["tier"], c["tier_motivos"] = "🟡 OBSERVAR", [f"no se pudo medir todavía ({e})"]
            continue
        fdv = r.get("fdv_compra") or 0
        med, hit, n, mejor = r.get("mediana"), r.get("hit"), r["n"], r.get("mejor")
        motivos = []
        if "granja" in (c.get("perfil") or ""):
            tier, motivos = "🔴 DESCARTAR", ["es granja de bots (máquinas)"]
        elif fdv > 500_000:
            tier, motivos = "🔴 DESCARTAR", [f"compra tokens de FDV ${fdv/1e6:.1f}M: los de FDV alto se "
                                             f"midieron todos en cero"]
        elif n >= 2 and (med or 0) < 0.5 and (hit or 0) < 0.10:
            tier, motivos = "🔴 DESCARTAR", [f"{n} entradas con mediana {med:.2f}x: pierde sistemáticamente"]
        elif n >= 3 and (med or 0) < 0.6 and (hit or 0) < 0.10:
            tier, motivos = "🔴 DESCARTAR", [f"{n} entradas con mediana {med:.2f}x: pierde sistemáticamente"]
        elif n >= 2 and (med or 0) < 0.9 and ((hit or 0) >= 0.10 or (mejor or 0) >= 3):
            # OJO con el orden: esto va ANTES de 🟢. Un grupo que PIERDE en promedio (mediana <0,9x) no
            # puede ir a "seguir" aunque tenga un golpe grande — es lotería (se midió: el grupo grande
            # tiene un 24x pero su mediana es 0,46x).
            tier, motivos = "🟠 LOTERÍA", [f"{n} entradas · mediana {med:.2f}x pero mejor {mejor:.2f}x: "
                                           f"el que paga es el golpe, no el promedio"]
        elif n >= 2 and ((med or 0) >= 1.0 or ((med or 0) >= 0.9 and (mejor or 0) >= 1.5)):
            # Corte calibrado con los datos del 08/10: exigir "mediana ≥1,05x Y ≥15% de aciertos" dejaba
            # 1 solo grupo de 42 (el canal "seguir" quedaba muerto). Entra el que promedia ≥1,00x, o el
            # que promedia ≥0,90x y mostró una entrada de ≥1,5x.
            tier, motivos = "🟢 SEGUIR", [f"{n} entradas · mediana {med:.2f}x · {100*(hit or 0):.0f}% ≥1,5x · "
                                          f"mejor {mejor:.2f}x · compra ~${fdv/1000:.0f}k"]
        else:
            tier, motivos = "🟡 OBSERVAR", [f"sólo {n} entrada(s) medida(s): sin muestra para juzgar"]
        nombre, como = _nombrar(c, r, tier)
        c["tier"], c["record"], c["tier_motivos"] = tier, r, motivos
        c["nombre"], c["nombre_motivo"] = nombre, como
        resumen.setdefault(tier, []).append({"nombre": nombre, "origen": ((c.get("origen") or [{}])[0].get("wallet")),
                                             "tamano": c["tamano"], "n": n,
                                             "mediana": med, "hit": hit, "mejor": mejor,
                                             "fdv_compra": fdv, "wallets": c["wallets"]})
    # Nombres únicos: si dos grupos comparten nombre (p.ej. los varios "Dúo sin caso bueno"), se les
    # agrega una etiqueta corta de la wallet origen para poder distinguirlos.
    por_nombre = defaultdict(list)
    for c in d["cohortes"]:
        por_nombre[c.get("nombre")].append(c)
    for nombre, gs in por_nombre.items():
        if len(gs) > 1:
            for c in gs:
                w = ((c.get("origen") or [{}])[0].get("wallet")) or (c["wallets"][0] if c["wallets"] else "????")
                c["nombre"] = f"{nombre} · {w[:4]}"
    for t in resumen:
        for g in resumen[t]:
            g["nombre"] = next((c.get("nombre") for c in d["cohortes"]
                                if ((c.get("origen") or [{}])[0].get("wallet")) == g["origen"]),
                               g.get("nombre"))
        resumen[t].sort(key=lambda x: (-(x["mediana"] or 0), -(x["mejor"] or 0)))
    _guardar(COHORTES, d)
    avisar_nuevos(d["cohortes"])
    _guardar(TIERS, {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "resumen": resumen})
    log("  clasificados: " + " · ".join(f"{t} {len(v)}" for t, v in resumen.items()))
    return resumen


def _nombrar(c, r, tier):
    """Le pone NOMBRE al grupo, sacado de los datos (así se puede hablar de "los de Mario64").

    Forma + token que lo identifica:
      Dúo / Trío / Grupo de N / Red de N (N>10)  +  "de <token del mejor caso>"
    Y cuando no hay caso bueno, se dice lo que es: megacap (compra tokens de millones), bots, o
    "sin caso bueno".
    """
    n = c["tamano"]
    forma = ("Dúo" if n == 2 else "Trío" if n == 3 else
             (f"Red de {n}" if n > 10 else f"Grupo de {n}"))
    fdv = r.get("fdv_compra") or 0
    tok = (r.get("mejor_token") or "").strip()
    best = r.get("mejor") or 0
    if "granja" in (c.get("perfil") or ""):
        return f"{forma} de bots", "el fondeo viene del mismo origen: son máquinas"
    if fdv > 1_000_000:
        return f"{forma} megacap", f"entra a tokens de ${fdv/1e6:.1f}M (no queda múltiplo)"
    if tok and best >= 1.5:
        etq = " (lotería)" if (tier or "").startswith("🟠") else ""
        return f"{forma} de {tok}{etq}", f"su mejor entrada medida fue {tok} con {best:.2f}x"
    if best and best >= 1.05:
        return f"{forma} de {tok or 'varios'}", f"entradas parejas (mediana {(r.get('mediana') or 0):.2f}x)"
    return f"{forma} sin caso bueno", f"nunca pasó de {(best or 0):.2f}x en lo medido"


VISTOS = os.path.join(AQUI, "grupos_vistos.json")


def avisar_nuevos(cohortes):
    """Avisa al canal general cuando aparece un GRUPO NUEVO (wallets que no habíamos visto juntas).

    Antes sólo se avisaba cuando un grupo ya conocido volvía a entrar: si el sistema descubría un grupo
    nuevo, quedaba en silencio hasta su primera compra. Ahora se cuenta en el momento en que aparece.
    """
    vistos = _leer(VISTOS, {}) or {}
    nuevos = []
    for c in cohortes:
        firma = ",".join(sorted(c["wallets"]))
        if firma in vistos:
            continue
        vistos[firma] = {"desde": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "origen": ((c.get("origen") or [{}])[0].get("wallet")),
                         "tamano": c["tamano"]}
        nuevos.append(c)
    _guardar(VISTOS, vistos)
    for c in nuevos[:3]:                    # sin inundar: hasta 3 por pasada
        nom = c.get("nombre") or f"Grupo de {c['tamano']}"
        o = ((c.get("origen") or [{}])[0].get("wallet")) or ""
        txt = (f"🆕 **GRUPO NUEVO DETECTADO** — **{nom}**\n"
               f"· {c['tamano']} wallets comprando juntas en {c['n_lanzamientos']} lanzamiento(s) · "
               f"{c['sol_total']} SOL\n"
               f"· el que entra primero: `{o[:14]}…`\n"
               f"· queda en 🟡 OBSERVAR hasta que junte registro (ahí se decide si pasa a 🟢 o 🔴)")
        try:
            PA.enviar_en_canal(txt, canal=canal_argos())
        except Exception as e:
            log(f"  ! no pude avisar el grupo nuevo: {e}")
        log(f"  🆕 grupo nuevo: {nom} ({c['tamano']} wallets)")
    return len(nuevos)


WALLETS_SCORE = os.path.join(AQUI, "wallets_puntaje.json")


def puntajes_wallets():
    """Nota medida de cada wallet (0-10 y cuántas entradas tiene), para mostrarla en el aviso."""
    d = _leer(WALLETS_SCORE, {}) or {}
    return d.get("wallets") or {}


def puntuar_wallets():
    """Puntúa CADA wallet (0-10) con la misma evidencia que usamos para los grupos: sus entradas medidas.

    Criterios (no inventados, salen de lo que ya validamos):
      · mediana de sus entradas · % que llegó a 1,5x · cuántas veces entró PRIMERO en su grupo
      · y cuántas entradas medidas tiene: con menos de 3 el puntaje se marca "sin muestra" (una wallet
        con 1 compra no se puede juzgar, por buena que haya salido).
    Se recalcula con la clasificación (cada 30 min, con la caché de precios, sin gasto extra de RPC).
    """
    d = _leer(COHORTES, None)
    if not d or not d.get("cohortes"):
        log("  (sin cohortes para puntuar wallets)")
        return {}
    tokens = (_leer(COSECHA, {}) or {}).get("tokens", {})
    reg = {}
    try:
        import registro_calls as RC
        for x in (RC._leer() or []):
            reg[x.get("mint")] = x
    except Exception:
        pass
    cache = _leer(MCAP_CACHE, {}) or {}
    cambió = False

    def multiple(mint, mcap_cosecha):
        x = reg.get(mint)
        if x:
            seg = [s for s in (x.get("seguimiento") or []) if s.get("mcap")]
            if len(seg) >= 2 and x.get("mcap_call"):
                return max(s["mcap"] for s in seg) / x["mcap_call"]
        e = cache.get(mint)
        if not e or (time.time() - (e.get("t") or 0)) > 1800:
            mc = None
            try:
                p = PA.dexscreener(mint)
                mc = (p.get("marketCap") or p.get("fdv")) if p else None
            except Exception:
                mc = None
            cache[mint] = {"mc": mc, "t": time.time()}
            cambió = True
            time.sleep(0.12)
            e = cache[mint]
        return (e["mc"] / mcap_cosecha) if (e.get("mc") and mcap_cosecha) else None

    # índice wallet -> grupo
    de_grupo = {}
    for c in d["cohortes"]:
        for w in c["wallets"]:
            de_grupo[w] = c

    # sus compras (token, sol, ts) desde la cosecha
    compras = defaultdict(list)
    for m, t in tokens.items():
        for w, v in (t.get("compras") or {}).items():
            if w in de_grupo:
                compras[w].append({"mint": m, "sol": v.get("sol") or 0, "ts": v.get("ts") or 0,
                                   "mcap": t.get("mcap_cosecha") or 0})

    filas = {}
    for w, cs in compras.items():
        vals, primeros = [], 0
        for comp in cs:
            mv = multiple(comp["mint"], comp["mcap"])
            if mv is None:
                continue
            comp["mult"] = mv
            vals.append(mv)
            grupo_tok = [x for x in (tokens.get(comp["mint"], {}).get("compras") or {}).items()
                         if x[0] in de_grupo]
            if grupo_tok and min(grupo_tok, key=lambda kv: (kv[1].get("ts") or 0))[0] == w:
                primeros += 1
        if not vals:
            continue
        med = statistics.median(vals)
        hit = sum(1 for v in vals if v >= 1.5) / len(vals)
        n = len(vals)
        # LO QUE DE VERDAD IMPORTA: si copiar a esta wallet con tamaño fijo ($1 por entrada) gana o
        # pierde plata. La mediana ENGAÑA: las wallets que cazan los monstruos entran a muchísimo y
        # tienen mediana baja (0,42-0,67x) pero su cola paga. Medido el 08/10: 33 de 120 wallets dan
        # PnL de copia positivo (+146 en total) y las otras 87 pierden (-168).
        pnl = sum(v - 1 for v in vals)
        hits = sum(1 for v in vals if v >= 1.5)
        p = 5.0
        p += 3 if med >= 2 else 2 if med >= 1.5 else 1 if med >= 1.2 else -2 if med < 0.8 else -3 if med < 0.5 else 0
        p += 2 if hit >= 0.30 else 1 if hit >= 0.15 else (-1 if hit == 0 else 0)
        p += 1 if n >= 5 else (-2 if n < 2 else 0)
        p += 1 if (primeros and primeros / max(1, len(cs)) >= 0.5) else 0
        p = max(0.0, min(10.0, p))
        g = de_grupo[w]
        # tier de la wallet POR RESULTADO DE COPIARLA (no por mediana)
        if n >= 3 and pnl > 2 and hits >= 1:
            wtier = "🟢 COPIABLE"
        elif n >= 3 and pnl <= 0 and hits == 0:
            wtier = "🔴 NUNCA"
        else:
            wtier = "🟡 OJO"
        filas[w] = {"nota": round(p, 1), "n": n, "mediana": round(med, 2), "hit": round(hit, 2),
                    "pnl_copia": round(pnl, 1), "hits": hits, "tier_wallet": wtier,
                    "mejor": round(max(vals), 2), "primeros": primeros, "compras": len(cs),
                    "grupo": g.get("nombre"), "grupo_tier": g.get("tier"),
                    "sol_total": round(sum(c["sol"] for c in cs), 2),
                    "confiable": n >= 3}
    if cambió:
        _guardar(MCAP_CACHE, cache)
    _guardar(WALLETS_SCORE, {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "wallets": filas})
    con = [w for w in filas.values() if w["confiable"]]
    log(f"  wallets puntuadas: {len(filas)} (con muestra suficiente: {len(con)})")
    return filas


def informe_wallets(n=15):
    """Los wallets con mejor puntaje (y aclarando cuáles tienen muestra suficiente)."""
    d = _leer(WALLETS_SCORE, None)
    if not d:
        log("  (todavía sin puntajes: corré --puntuar-wallets)")
        return
    ws = d.get("wallets") or {}
    con = sorted([(w, v) for w, v in ws.items() if v["confiable"]], key=lambda kv: -kv[1]["nota"])
    print(f"=== WALLETS PUNTUADAS ({len(ws)} medidas · {len(con)} con muestra suficiente) ===")
    print(f"{'wallet':<14} {'nota':>4} {'entr':>4} {'mediana':>8} {'≥1,5x':>6} {'mejor':>6} {'primero':>8}  grupo")
    for w, v in con[:n]:
        print("%-14s %4.1f %4d %7.2fx %5.0f%% %5.2fx %8s  %s"
              % (w[:12] + "…", v["nota"], v["n"], v["mediana"], 100 * v["hit"], v["mejor"],
                 f"{v['primeros']}/{v['compras']}", str(v["grupo"])[:26]))
    print("\n  (las que tienen menos de 3 entradas medidas no se listan: no hay con qué juzgarlas)")


def familias_por_pagador(min_wallets=2):
    """Familias por PAGADOR de la comisión: wallets distintas que compran pagando con la misma cuenta.

    Es la heurística de clustering estándar en Solana (Bubblemaps agrupa así): si varias wallets compran
    con el mismo pagador, son la misma mano — un operador con muchas wallets. Sirve para separar
    "grupo de personas" de "un operador con un bundle", que es lo que hoy digo a ojo.
    """
    tokens = (_leer(COSECHA, {}) or {}).get("tokens", {})
    por_pagador = defaultdict(set)
    for m, t in tokens.items():
        for w, v in (t.get("compras") or {}).items():
            p = (v or {}).get("pagador")
            if p and p != w:                     # si el pagador es la propia wallet, no dice nada
                por_pagador[p].add(w)
    fam = {p: sorted(ws) for p, ws in por_pagador.items() if len(ws) >= min_wallets}
    print(f"=== FAMILIAS POR PAGADOR (mismo que paga la comisión) — {len(fam)} con {min_wallets}+ wallets ===")
    for p, ws in sorted(fam.items(), key=lambda kv: -len(kv[1]))[:12]:
        print("  pagador %s…  → %2d wallets  %s" % (p[:12], len(ws), ", ".join(w[:8] + "…" for w in ws[:5])))
    sin = sum(1 for m, t in tokens.items() for w, v in (t.get("compras") or {}).items() if not (v or {}).get("pagador"))
    tot = sum(1 for m, t in tokens.items() for w in (t.get("compras") or {}))
    print(f"\n  (compras con pagador distinto a la wallet: {tot - sin} de {tot} — las viejas no lo tienen, "
          f"se va llenando con las cosechas nuevas)")
    return fam


def informe_grupos():
    """Muestra los grupos ORGANIZADOS por tier, con el porqué de cada uno."""
    d = _leer(TIERS, None)
    if not d:
        log("  (todavía sin clasificar: corré --clasificar)")
        return
    print(f"=== GRUPOS DE WALLETS (clasificado {str(d.get('generado'))[5:16]} UTC) ===")
    orden = ["🟢 SEGUIR", "🟠 LOTERÍA", "🟡 OBSERVAR", "🔴 DESCARTAR"]
    for t in orden:
        gs = (d.get("resumen") or {}).get(t) or []
        if not gs:
            continue
        print(f"\n{t}  ({len(gs)} grupo{'s' if len(gs) != 1 else ''})")
        for g in gs[:12]:
            rec = (f"{g['n']} entradas · mediana {g['mediana']:.2f}x · {100*(g['hit'] or 0):.0f}% ≥1,5x · "
                   f"mejor {g['mejor']:.2f}x") if g["n"] else "sin entradas medidas"
            print(f"   · {str(g.get('nombre') or '?')[:34]:<34} {g['tamano']} wallets  ({rec})")
        if len(gs) > 12:
            print(f"   … y {len(gs)-12} más")
    total = sum(len(v) for v in (d.get("resumen") or {}).values())
    print(f"\n   TOTAL: {total} grupos")


def perfil_wallet(w):
    """Perfil de una wallet, GUARDADO (7 días). ¿Por qué?: el perfil se recalcula en cada armado, y si
    el chequeo de fondeo falla por red una granja ya marcada volvía a "grupo de personas" y se colaba
    en los avisos. Se guarda el resultado y, si no se pudo comprobar, no se inventa: queda "sin determinar".
    """
    d = _leer(PERFILES, {}) or {}
    e = d.get(w)
    if e and (time.time() - (e.get("ts") or 0)) < 7 * 86400:
        return e
    fresca = None
    try:
        fresca = bool(CD.es_fresca(w))
    except Exception:
        pass
    fonders = funder_original(w)          # el fondeo de origen (no el pagador reciente)
    e = {"fresca": fresca, "fonders": fonders, "ts": time.time()}
    if fresca is not None or fonders is not None:      # sólo se guarda si algo se pudo comprobar
        d[w] = e
        _guardar(PERFILES, d)
    return e


def armar():
    """Co-compras entre lanzamientos → cohortes por union-find."""
    datos = _leer(COSECHA, {"tokens": {}})
    tokens = {m: d for m, d in (datos.get("tokens") or {}).items() if (d.get("compras") or {})}
    log(f"  armando cohortes con {len(tokens)} lanzamientos cosechados")
    parejas = defaultdict(int)
    for m, d in tokens.items():
        compras = [(w, c) for w, c in d["compras"].items() if (c.get("sol") or 0) >= MIN_SOL]
        compras.sort(key=lambda x: x[1].get("ts") or 0)
        for i in range(len(compras)):
            for j in range(i + 1, len(compras)):
                w1, c1 = compras[i]
                w2, c2 = compras[j]
                if c1.get("slot") and c2.get("slot") and c1["slot"] == c2["slot"]:
                    parejas[(w1, w2)] += 1
                elif abs((c1.get("ts") or 0) - (c2.get("ts") or 0)) <= VENTANA_CO:
                    parejas[(w1, w2)] += 1
                else:
                    break                     # ya están fuera de la ventana (lista ordenada)
    u = Unión()
    fuertes = {k: v for k, v in parejas.items() if v >= MIN_PAREJA}
    for (w1, w2) in fuertes:
        u.unir(w1, w2)
    cohortes = []
    for g in u.grupos():
        lanzamientos = [m for m, d in tokens.items() if len(set(g) & set(d["compras"])) >= 2]
        # el ORIGEN: quién entra primero más seguido entre los miembros
        veces_primero = defaultdict(int)
        for m in lanzamientos:
            compras = d = tokens[m]["compras"]
            dentro = [(w, compras[w]) for w in g if w in compras]
            if dentro:
                dentro.sort(key=lambda x: (x[1].get("ts") or 0, x[1].get("slot") or 0))
                veces_primero[dentro[0][0]] += 1
        origen = sorted(veces_primero.items(), key=lambda kv: -kv[1])[:3]
        sol_total = 0.0
        for m in lanzamientos:
            for w in g:
                if w in tokens[m]["compras"]:
                    sol_total += tokens[m]["compras"][w].get("sol") or 0
        # --- perfil: ¿grupo de personas o granja de bots? (con caché: ver perfil_wallet)
        frescas, fonders, desconocidas = 0, defaultdict(int), 0
        for w in g[:6]:
            e = perfil_wallet(w)
            if e.get("fresca") is True:
                frescas += 1
            elif e.get("fresca") is None:
                desconocidas += 1
            for f in (e.get("fonders") or []):
                fonders[f] += 1
        compartido = [f for f, n in fonders.items() if n >= 2]
        granja = bool(compartido) or frescas >= max(2, len(g) * 0.6)
        perfil = ("🤖 granja de bots" if granja
                  else ("🧑 grupo de personas" if desconocidas < len(g[:6]) else "❔ sin determinar"))
        cohortes.append({"wallets": g, "tamano": len(g), "lanzamientos": lanzamientos[:20],
                         "perfil": perfil, "frescas": frescas,
                         "fonder_compartido": compartido[:2],
                         "n_lanzamientos": len(lanzamientos), "sol_total": round(sol_total, 2),
                         "origen": [{"wallet": w, "veces_primero": n} for w, n in origen],
                         "parejas_fuertes": len([1 for (a, b) in fuertes if a in g and b in g])})
    cohortes.sort(key=lambda c: (-c["n_lanzamientos"], -c["tamano"]))
    _guardar(COHORTES, {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "lanzamientos": len(tokens), "cohortes": cohortes})
    log(f"  cohortes encontrados: {len(cohortes)} "
        f"({len([c for c in cohortes if c['n_lanzamientos'] >= 2])} repiten en 2+ lanzamientos)")
    return cohortes


def informe():
    d = _leer(COHORTES, None)
    if not d:
        print("Todavía no hay cohortes armados. Corré --cosechar y después --armar.")
        return
    print(f"=== COHORTES ({d.get('lanzamientos')} lanzamientos cosechados · {len(d['cohortes'])} cohortes) ===\n")
    for c in d["cohortes"][:12]:
        tipo = "🕸️ cohorte" if c["tamano"] >= 3 else "👥 dúo"
        print(f"  {tipo} de {c['tamano']} wallets · {c['n_lanzamientos']} lanzamientos · "
              f"{c['sol_total']} SOL · {c.get('perfil','?')} "
              f"({c.get('frescas', 0)} wallet(s) nueva(s)"
              + (f", fondeo compartido: {c['fonder_compartido'][0][:10]}…" if c.get('fonder_compartido') else "")
              + ")")
        for o in c["origen"]:
            print(f"      ⭐ origen: {o['wallet']} (entró primero {o['veces_primero']}×)")
        print(f"      wallets: {', '.join(w[:10] for w in c['wallets'][:6])}")
        if c["lanzamientos"]:
            syms = [(_leer(COSECHA, {}).get("tokens", {}).get(m, {}) or {}).get("simbolo") or m[:6]
                    for m in c["lanzamientos"][:6]]
            print(f"      tokens: {', '.join(syms)}")
    if not d["cohortes"]:
        print("  (ninguno todavía: con pocos lanzamientos cosechados el grafo está vacío — hace falta")
        print("   juntar más días de cosecha para que aparezcan los que repiten)")


def _preparar_token(t):
    """Lo lento de un token joven: sus compras y el análisis. Pensado para correr en paralelo."""
    try:
        compras = compras_de(t["mint"])
    except Exception:
        return t, None, None
    try:
        ev = PA.analizar(PA.buscar_coin(t["mint"]), pedir=True)
    except Exception:
        ev = {}
    return t, compras, ev


def vigilar(max_tokens=8):
    """¿Entró algún cohorte conocido a un token joven? (el aviso con el ORIGEN adelante)"""
    d = _leer(COHORTES, None)
    if not d or not d.get("cohortes"):
        log("  sin cohortes armados todavía")
        return []
    try:
        jovenes = json.load(open(os.path.join(AQUI, "tokens_jovenes.json")))
    except Exception:
        jovenes = []
    hallados = []
    # Las llamadas caras (compras de la curva y análisis del token) se hacen EN PARALELO: medido el
    # 12/10, la pasada en serie tardaba más de 5 minutos y el cron corre cada 6, así que se encimaban
    # y la call llegaba tarde. Acá se bajan todos los tokens juntos y después se resuelve en serie
    # (el orden importa para el aviso, y así no se saturan Discord ni la API).
    from concurrent.futures import ThreadPoolExecutor
    jovenes = jovenes[:max_tokens]
    with ThreadPoolExecutor(max_workers=8) as ex:
        preparados = list(ex.map(_preparar_token, jovenes))
    for t, compras, ev in preparados:
        if compras is None or ev is None:
            continue
        for c in d["cohortes"]:
            dentro = sorted([w for w in c["wallets"] if w in compras],
                            key=lambda w: compras[w].get("ts") or 0)
            if len(dentro) < min(2, MIN_COHORTE):
                continue
            origen = dentro[0]                      # el que entró primero de ese cohorte
            nota, motivos, etq = CD.puntuar({"n_wallets": len(dentro), "sol": sum(compras[w]["sol"] for w in dentro),
                                            "segundos": 0, "slots_iguales": 1, "frescas": 0,
                                            "conocidas": len(dentro), "compras_en_ventana": len(dentro)},
                                           ev.get("contexto") or {})
            ev["call"] = {"nota": nota, "etq": etq, "n_wallets": len(dentro), "sol": round(sum(compras[w]["sol"] for w in dentro), 2),
                          "segundos": 0, "slots_iguales": 1, "frescas": 0, "conocidas": len(dentro),
                          "compras_en_ventana": len(dentro), "wallets": dentro, "cluster": True,
                          "roster": [{"wallet": w, "sol": round(compras[w]["sol"], 2),
                                      "nivel": "origen" if w == origen else "miembro"} for w in dentro[:8]],
                          "motivos": motivos + [f"**cohorte de {c['tamano']} wallets** que repite en "
                                                f"{c['n_lanzamientos']} lanzamientos · el que entró primero "
                                                f"fue `{origen[:10]}…`"],
                          "hora": datetime.now(timezone.utc).strftime("%H:%M") + " UTC",
                          "ts_iso": datetime.now(timezone.utc).isoformat()}
            ev["por_rafaga"] = True
            hallados.append(ev)
            log(f"  🕸️ cohorte en {ev['simbolo']}: {len(dentro)} miembros · origen {origen[:10]}…")
            try:
                import registro_calls as RC
                RC.registrar(ev["call"], ev)
            except Exception:
                pass
            break
        time.sleep(0.2)

    # Los avisos se mandan EN PARALELO (4 a la vez, con una pausa cada uno para no saturar Discord:
    # los 429 de Discord son el otro límite y ya nos mordieron el 08/10). Antes se mandaban uno por
    # uno dentro del bucle y, como cada aviso baja la imagen del token, la pasada se pasaba del tope
    # de 9 minutos del lanzador y el workflow fallaba (medido el 13/10).
    if hallados:
        from concurrent.futures import ThreadPoolExecutor

        def _avisar(e):
            time.sleep(0.9)
            try:
                PA.alertar_token(e, "🕸️ COHORTE CONOCIDO ENTRANDO", canal=canal_argos())
            except Exception as ex:                              # noqa: BLE001
                log(f"  ! no pude avisar {e.get('simbolo')}: {str(ex)[:60]}")

        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(_avisar, hallados))
    return hallados


PERFILES = os.path.join(AQUI, "perfiles_wallet.json")
EST_CARTERA = os.path.join(AQUI, "cartera_vista.json")
BUFFER_COMPRAS = os.path.join(AQUI, "cartera_compras.json")
AVISO_HECHOS = os.path.join(AQUI, "cartera_avisos.json")
IGNORAR_MINT = {"So11111111111111111111111111111111111111112",     # wSOL
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",     # USDC
                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}     # USDT


def delta_tokens(meta, wallet):
    """{mint: cuánto ENTRÓ} a esa wallet en esa tx (lo que recibió)."""
    pre, post = {}, {}
    for b in (meta.get("preTokenBalances") or []):
        if b.get("owner") == wallet:
            pre[b.get("mint")] = (b.get("uiTokenAmount") or {}).get("uiAmount") or 0
    for b in (meta.get("postTokenBalances") or []):
        if b.get("owner") == wallet:
            post[b.get("mint")] = (b.get("uiTokenAmount") or {}).get("uiAmount") or 0
    return {m: (post.get(m, 0) - pre.get(m, 0)) for m in set(pre) | set(post)
            if (post.get(m, 0) - pre.get(m, 0)) > 0 and m not in IGNORAR_MINT}


def compra_de_wallet(sig, wallet):
    """Si esa wallet COMPRÓ en esa tx → (mint, SOL gastados). None si no fue compra."""
    try:
        # OJO: commitment "confirmed". El aviso del WebSocket llega cuando la transacción está
        # confirmada pero TODAVÍA no finalizada (~13-30 s después): consultando con el valor por
        # defecto (finalized) el nodo devuelve null y se pierde la compra. Se midió el 09/10.
        tx = WT.rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                            "encoding": "jsonParsed", "commitment": "confirmed"}])
    except Exception:
        return None
    anotar_creditos(10)
    if not tx or not tx.get("meta") or tx["meta"].get("err"):
        return None
    meta = tx["meta"]
    entraron = delta_tokens(meta, wallet)
    if not entraron:
        return None
    sol = 0.0
    try:
        i = WT.cuentas_tx(tx).index(wallet)
        sol = (meta["preBalances"][i] - meta["postBalances"][i]) / 1e9
    except Exception:
        pass
    if sol <= 0.01:                       # sin SOL que salga no es compra pagada (regalo/airdrop)
        return None
    return max(entraron, key=lambda m: entraron[m]), round(sol, 3)


def vigilar_cartera(max_sigs=3, max_avisos=3):
    """Vigila las WALLETS de los cohortes conocidos: si una COMPRA, avisa al toque.

    El chequeo viejo miraba al revés —buscaba a los cohortes DENTRO de los tokens de la lista del
    radar— y se le escapaba todo: medido el 08/10, 4 de 6 wallets comprando en ese momento y CERO
    avisos, porque sus compras no estaban en esa lista. Vigilando la wallet no se pierde ninguna.
    """
    d = _leer(COHORTES, None)
    if not d or not d.get("cohortes"):
        log("  sin cohortes armados todavía")
        return 0
    # A QUIÉN SE PATRULLA (medido el 10/10): con 138 cohortes y 852 wallets, leerlas todas cada 3 min
    # costaba ~2,5 M de créditos por día (el mayor gasto del sistema, más que todo lo demás junto).
    # Se patrullan sólo las que importan: las wallets COPIABLES y las de los grupos 🟢/🟠 (los que
    # seguimos). Los 🔴/🟡 se miden igual por la cosecha, no necesitan vigilancia al segundo.
    _cop = {w for w, v in (puntajes_wallets() or {}).items() if v.get("tier_wallet") == "🟢 COPIABLE"}
    indice = {}
    for c in d["cohortes"]:
        if not (c.get("tier") or "").startswith(("🟢", "🟠")):
            continue
        for w in c["wallets"]:
            indice[w] = c
    for w in _cop:
        if w not in indice:
            indice[w] = {"wallets": [w], "tamano": 1, "nombre": "wallet copiable", "tier": "🟢 COPIABLE",
                         "n_lanzamientos": 0, "perfil": "grupo", "origen": [{"wallet": w}]}
    est = _leer(EST_CARTERA, {}) or {}
    canal = canal_argos()
    avisos = 0
    # OJO: antes decía [:40] — con 209 wallets vigiladas sólo se leían las PRIMERAS 40 (y siempre las
    # mismas), así que ~170 wallets nunca se revisaban: los grupos 🟢 no tenían ni una compra suya en el
    # búfer y su canal quedaba mudo sin que se entendiera por qué. Ahora se leen TODAS; el cupo se
    # controla bajando las firmas por wallet (4 en vez de 6).
    for w, c in list(indice.items()):
        try:
            sigs = WT.rpc("getSignaturesForAddress", [w, {"limit": max_sigs}]) or []
        except Exception:
            continue
        anotar_creditos(1 + len(sigs))
        firmas = [s["signature"] for s in sigs]
        vistas = est.get(w)
        est[w] = firmas
        if vistas is None:                 # primera vez: sólo se toma la línea base
            continue
        # Se REGISTRA la compra, no se avisa todavía: avisar cada compra suelta de estas wallets es un
        # río (10 wallets muy activas = hasta 160 avisos/hora, medido). El aviso sale cuando el grupo
        # actúa COORDINADO (2+ miembros del mismo cohorte en el mismo token dentro de la ventana).
        for s in reversed([s for s in sigs if s["signature"] not in vistas]):
            if s.get("err"):
                continue
            compra = compra_de_wallet(s["signature"], w)
            if compra:
                anotar_compra(w, compra[0], compra[1])
        time.sleep(0.05)
    _guardar(EST_CARTERA, est)
    avisos = avisar_coincidencias(max_avisos)
    log(f"  cartera: {len(indice)} wallets vigiladas · avisos: {avisos}")
    return avisos


def anotar_compra(w, mint, sol):
    """Guarda la compra en el buffer (6 h) para poder ver coincidencias del grupo."""
    b = _leer(BUFFER_COMPRAS, []) or []
    b.append({"w": w, "mint": mint, "sol": sol, "ts": time.time()})
    _guardar(BUFFER_COMPRAS, [x for x in b if time.time() - x.get("ts", 0) < 6 * 3600][-500:])


def avisar_coincidencias(max_avisos=3, ventana_min=15, edad_max_min=90):
    """Avisa cuando 2+ wallets del MISMO cohorte compraron el mismo token dentro de la ventana.

    Esa es la señal que importa: el grupo entrando coordinado. Las compras sueltas quedan registradas
    pero no se avisan, y los cohortes marcados como granja de bots se saltean (son máquinas).
    """
    d = _leer(COHORTES, None)
    if not d or not d.get("cohortes"):
        return 0
    limite = time.time() - ventana_min * 60
    por_mint = defaultdict(list)
    for x in (_leer(BUFFER_COMPRAS, []) or []):
        if x.get("ts", 0) >= limite:
            por_mint[x["mint"]].append(x)
    hechos = _leer(AVISO_HECHOS, {}) or {}
    # GOTEO, NO RÁFAGA (medido el 08/10): con tope de 6/hora por canal, los avisos salían todos juntos
    # en los primeros minutos y después el canal quedaba MUDO el resto de la hora (el tope solo se libera
    # cuando la ventana de 60 min se corre). Ahora: hasta 10 por hora por canal PERO con un mínimo de
    # 90 s entre avisos del mismo canal → el flujo queda parejo y el canal nunca se ve vacío.
    TOPE_HORA = 10
    INTERVALO_S = 90
    avisos = 0

    def _puede_avisar(canal):
        """(se puede, motivo) por canal: cupo de la hora + intervalo mínimo entre avisos.

        #seguir (los copiables) tiene el DOBLE de cupo: es el canal que importa y no debe competir.
        """
        ahora_t = time.time()
        tope = TOPE_HORA * 2 if canal == canal_para("🟢 SEGUIR") else TOPE_HORA
        # El canal de las MEJORES es curado: cupo diario chico (8) para que sea de verdad "lo mejor",
        # no otro canal de flujo. Medido: con el umbral solo, pasaba el 38% de las calls.
        if canal == canal_mejores_id():
            return True, ""
        previos = [t for k, t in hechos.items() if str(k).startswith(f"{canal}|")]
        en_hora = sum(1 for t in previos if ahora_t - t < 3600)
        if en_hora >= tope:
            return False, f"cupo de {tope}/hora"
        if previos and (ahora_t - max(previos)) < INTERVALO_S:
            return False, f"muy seguido (espera {INTERVALO_S} s)"
        return True, ""
    copiables = {w for w, v in puntajes_wallets().items() if v.get("tier_wallet") == "🟢 COPIABLE"}
    for mint, compras in sorted(por_mint.items(), key=lambda kv: -max(x["ts"] for x in kv[1])):
        if avisos >= max_avisos:
            break
        for c in d["cohortes"]:
            if "granja" in (c.get("perfil") or ""):
                continue                     # las granjas de bots no avisan en ningún canal
            dentro = sorted({x["w"] for x in compras} & set(c["wallets"]))
            if not dentro:
                continue
            # PRIORIDAD POR WALLET (medido el 08/10): las wallets que dan plata están DENTRO del grupo
            # grande, que es 🟠 → sus avisos se iban al canal de lotería y #seguir quedaba vacío. Ahora
            # manda la WALLET: si entra una copiable, el aviso va a #seguir y basta con UNA.
            entra_copiable = bool(set(dentro) & copiables)
            if entra_copiable:
                minimo = 1
            elif (c.get("tier") or "").startswith("🟢"):
                minimo = 1                   # en los grupos que seguimos, un miembro ya es noticia
            else:
                minimo = 3 if c["tamano"] >= 5 else 2      # en el resto: 2 chicos, 3 grandes
            if len(dentro) < minimo:
                continue
            canal = canal_para("🟢 SEGUIR") if entra_copiable else canal_para(c.get("tier"))
            # El canal de OBSERVAR/DESCARTAR se eliminó a pedido (su ruido tapaba lo bueno): esas
            # entradas se siguen registrando y midiendo, pero NO se avisan en ningún canal.
            if canal == canal_para("🟡 OBSERVAR"):
                hechos[f"{canal}|{mint}"] = time.time()
                continue
            puede, motivo = _puede_avisar(canal)
            if not puede:
                continue                     # ese canal ya usó su cupo o va muy seguido
            segundos = int(max(x["ts"] for x in compras) - min(x["ts"] for x in compras))
            if segundos > ventana_min * 60:
                continue
            coin = PA.buscar_coin(mint)
            creado = (coin or {}).get("created_timestamp") or 0
            edad_min = ((time.time() - creado / 1000) / 60) if creado else None
            if edad_min is not None and edad_min > edad_max_min:
                continue
            # dedupe por TOKEN (no por combinación de wallets): si no, el mismo token vuelve a avisar
            # cada vez que entra otro miembro del grupo → Argos se inunda (medido: 8 avisos por pasada).
            clave = f"{canal}|{mint}"       # el dedupe es por canal + token
            if hechos.get(clave) or hechos.get(mint):
                continue                     # `mint` pelado = aviso de antes del cambio de formato
            hechos[clave] = time.time()
            origen = ((c.get("origen") or [{}])[0].get("wallet")) or dentro[0]
            sol = round(sum(x["sol"] for x in compras if x["w"] in dentro), 2)
            # El puntaje de CADA wallet que entra, en el momento de decidir: es el paso final del flujo
            # (detectar → medir cómo les fue → puntuar). Si todavía no tiene entradas medidas, se dice
            # "sin registro" en vez de inventarle una nota.
            punt = puntajes_wallets()
            # Las COPIABLES primero: si el aviso llegó al canal de seguir es por una de ellas, así que
            # tienen que verse primero (antes salían las perdedoras y parecía un aviso de ruido).
            dentro_ord = sorted(dentro, key=lambda w: -((punt.get(w) or {}).get("pnl_copia") or -999))
            datos_w = []
            for w in dentro_ord[:6]:
                v = punt.get(w)
                if v and v.get("n"):
                    # se muestra el PnL DE COPIA (lo que gana copiándola), no la nota por mediana:
                    # medido el 08/10, las wallets que cazan los monstruos tienen mediana baja.
                    marca = "🟢" if v.get("tier_wallet") == "🟢 COPIABLE" else ""
                    datos_w.append(f"{marca}`{w[:8]}…` **{v.get('pnl_copia', 0):+.1f}** "
                                   f"({v.get('hits', 0)} ganadoras/{v['n']} entradas)")
                else:
                    datos_w.append(f"`{w[:8]}…` sin registro")
            linea_w = "👤 quiénes entran: " + " · ".join(datos_w)
            ev = PA.analizar(coin, pedir=True)
            nota, motivos, etq = CD.puntuar(
                {"n_wallets": len(dentro), "sol": sol, "segundos": segundos, "slots_iguales": 1,
                 "frescas": 0, "conocidas": len(dentro), "compras_en_ventana": len(compras)},
                ev.get("contexto") or {})
            if len(dentro) == 1:
                # En un grupo 🟢 la entrada de UNO ya es noticia: el motivo del puntaje ("solo N
                # wallets: no es una call") confunde en este caso, así que se reemplaza por el real.
                motivos = [m for m in (motivos or []) if "fuerza" not in m.lower()]
                motivos.append("**entrada individual** de un grupo 🟢 (no hace falta que entre todo el "
                               "grupo para avisar: seguimos a esas wallets)")
            # CALIDAD DE LA CALL (0-10). Si da ≥7 va al canal de las MEJORES (de cualquier grupo); si no,
            # sigue el ruteo normal por nivel. Así el canal de las mejores es un filtro real, no un adorno.
            nota_call, motivos_call = calidad_call(
                {"n_wallets": len(dentro), "sol": sol, "wallets": dentro[:8], "tier": c.get("tier")},
                ev.get("contexto") or {}, punt)
            # Umbral 8 (no 7): medido sobre 208 calls, el tramo 7-8 dio 9% de aciertos y el 8-10 dio 41%.
            # PERCENTIL del día: el puntaje absoluto se satura (Dream sacó 10/10 con 3 copiables y sin
            # liquidez real), así que lo que decide es estar en el TOP 10% de las calls del día, medido
            # contra las que ya se registraron hoy. Se auto-calibra: no depende de umbrales fijos.
            try:
                import registro_calls as RC
                hoy_s = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                del_dia = [x.get("nota_calidad") for x in (RC._leer() or [])
                           if str(x.get("ts", "")).startswith(hoy_s) and x.get("nota_calidad") is not None]
            except Exception:
                del_dia = []
            pct = 100.0 * sum(1 for v in del_dia if v < nota_call) / max(1, len(del_dia))
            _mejores = canal_mejores_id()
            _hay_copiable = bool(set(dentro) & copiables)      # el ingrediente que más pesa (35% vs 8%)
            # Respaldo mientras no haya historial del día: umbral absoluto 9 (para que el canal no quede
            # vacío las primeras horas). Con 10+ calls del día, manda el percentil.
            _entra_top = (pct >= 90) if len(del_dia) >= 10 else (nota_call >= 9)
            _a_mejores = False
            if _entra_top and _hay_copiable and _mejores:
                canal = _mejores
                _a_mejores = True
                puede, motivo = _puede_avisar(canal)
                if not puede:
                    continue
            ev["call"] = {
                "nota": nota, "etq": etq, "n_wallets": len(dentro), "sol": sol, "segundos": segundos,
                "nota_calidad": nota_call, "percentil_calidad": round(pct), "motivos_calidad": motivos_call,
                "en_mejores": _a_mejores,
                "slots_iguales": 1, "frescas": 0, "conocidas": len(dentro),
                "compras_en_ventana": len(compras), "cluster": True, "wallets": dentro[:8],
                "roster": [{"wallet": x, "sol": round(sum(y["sol"] for y in compras if y["w"] == x), 2),
                            "nivel": ("origen" if x == origen else "miembro"),
                            "nota": (punt.get(x) or {}).get("nota"),
                            "medidas": (punt.get(x) or {}).get("n")} for x in dentro[:8]],
                "motivos": (motivos or []) + [
                    f"**{c.get('nombre') or 'grupo sin nombre'}** · {c.get('tier', '')}"
                    + (" · entrada individual (el resto del grupo todavía no entró)" if len(dentro) == 1 else ""),
                    linea_w,
                    f"_{c.get('nombre_motivo') or ''}_",
                    f"**{c.get('tier', '')}** · {c.get('tier_motivos', [''])[0] if c.get('tier_motivos') else ''}",
                    f"**{len(dentro)} wallet{'s' if len(dentro) != 1 else ''} del mismo "
                    f"{c.get('perfil', 'grupo')}** "
                    f"({c['tamano']} miembros, repite en {c['n_lanzamientos']} lanzamientos) "
                    f"comprando el mismo token en {segundos // 60} min",
                    f"origen del grupo: `{origen[:12]}…` (`{mint[:12]}…`)"],
                "hora": datetime.now(timezone.utc).strftime("%H:%M") + " UTC",
                "ts_iso": datetime.now(timezone.utc).isoformat(), "es_cohorte": True,
                "perfil": c.get("perfil"), "origen": origen, "tamano_grupo": c["tamano"],
                "repeticiones": c["n_lanzamientos"], "nombre_grupo": c.get("nombre"),
                "linea_wallets": linea_w}
            ev["por_rafaga"] = True
            avisos += 1
            log(f"  🕸️ COORDINADO: {len(dentro)} wallets de {c['tamano']} → {mint[:10]}… ({sol} SOL)")
            try:
                import registro_calls as RC
                RC.registrar(ev["call"], ev)
            except Exception:
                pass
            PA.alertar_token(ev, "🕸️ GRUPO CONOCIDO ENTRANDO", canal=canal)
            break
    if not avisos and por_mint:
        # diagnóstico: había entradas coordinadas pero ninguna salió (cupo/intervalo/edad/tamaño)
        puede_o, motivo_o = _puede_avisar(canal_para("🟡 OBSERVAR"))
        log(f"  (sin avisos esta pasada: {len(por_mint)} token(s) con co-compra · motivo del canal "
            f"de observar: {motivo_o or 'pasó el filtro de canal, no el de token/edad'})")
    _guardar(AVISO_HECHOS, {k: v for k, v in hechos.items() if time.time() - v < 24 * 3600})
    return avisos


def main():
    ap = argparse.ArgumentParser(description="Cohortes persistentes (calls sin entrar al grupo)")
    ap.add_argument("--cosechar", type=int, default=0, metavar="N")
    ap.add_argument("--armar", action="store_true")
    ap.add_argument("--informe", action="store_true")
    ap.add_argument("--clasificar", action="store_true",
                    help="ordena los grupos en tiers (seguir / lotería / observar / descartar)")
    ap.add_argument("--grupos", action="store_true", help="muestra los grupos organizados por tier")
    ap.add_argument("--pagadores", action="store_true", help="familias de wallets que comparten pagador")
    ap.add_argument("--puntuar-wallets", action="store_true", help="puntúa cada wallet (0-10)")
    ap.add_argument("--wallets", action="store_true", help="muestra las wallets con mejor puntaje")
    ap.add_argument("--vigilar", action="store_true",
                    help="lo que corre el cron: SÓLO el detector de grupo coordinado")
    ap.add_argument("--vigilar-tokens", action="store_true",
                    help="chequeo viejo (cohortes dentro de la lista de tokens), manual, fuera de Argos")
    a = ap.parse_args()
    if a.cosechar:
        cosechar(a.cosechar)
    if a.armar or a.cosechar:
        armar()
        clasificar()      # el armado reconstruye los grupos y pierde tier/nombre: se reclasifica al toque
    if a.informe:
        informe()
    if a.clasificar:
        clasificar()
        puntuar_wallets()      # el puntaje de wallets se recalcula con la clasificación
    if a.grupos:
        informe_grupos()
    if a.pagadores:
        familias_por_pagador()
    if a.puntuar_wallets:
        puntuar_wallets()
    if a.wallets:
        informe_wallets()
    if a.vigilar:
        # OJO: acá va SÓLO el detector de grupo coordinado (wallets de cohortes conocidos comprando lo
        # mismo). El chequeo viejo -mirar quién compró los tokens de la lista del radar- se filtraba a
        # Argos y lo llenaba de avisos con formato de ráfaga; quedó como opción manual.
        vigilar_cartera()
    if a.vigilar_tokens:
        vigilar()
    if not any([a.cosechar, a.armar, a.informe, a.vigilar, a.clasificar, a.grupos, a.pagadores,
                a.puntuar_wallets, a.wallets]):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())