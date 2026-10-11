#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tracker de wallets de Solana — canal de seguimiento de "smart money".

Qué hace, cada pasada:
  1. Mira las últimas transacciones de cada wallet de tu lista (RPC público, sin API key).
  2. Detecta compras, ventas, transferencias de tokens y movimientos de SOL.
  3. Si una wallet COMPRA un token nuevo, corre solo el analizador (pump_analizador.py)
     y adjunta el veredicto: ¿vale la pena o no?
  4. Manda todo a Telegram y/o Discord. Cero tokens de LLM.

Uso:
  python3 wallet_tracker.py --listar
  python3 wallet_tracker.py --agregar <direccion> "etiqueta"
  python3 wallet_tracker.py --quitar <direccion>
  python3 wallet_tracker.py --descubrir 25            # propone wallets a seguir desde los tokens que corrieron
  python3 wallet_tracker.py --una-pasada --dry-run    # mira sin enviar ni guardar estado
  python3 wallet_tracker.py --una-pasada              # una pasada real
  python3 wallet_tracker.py --loop 120                # bucle: una pasada cada 120 s (para cron no hace falta)

Archivos: wallets.json (lista), tracker_state.json (firmas vistas), tokens_cache.json (símbolos).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pump_analizador as PA          # noqa: E402  (reutiliza el analizador y sus fuentes)

WALLETS = os.path.join(AQUI, "wallets.json")
STATE = os.path.join(AQUI, "tracker_state.json")
CACHE = os.path.join(AQUI, "tokens_cache.json")
LOG = os.path.join(AQUI, "logs")

def _hosts_helius():
    """TODAS las keys de Helius del .env, como lista de hosts.

    Con más de una key hay **más cupo** (cada key tiene su propio tope mensual) y además más rate
    limit. `rpc()` va rotando el orden para consumir las keys parejo, no para usar siempre la primera.
    """
    hosts = []
    try:
        for l in open(os.path.expanduser("~/.hermes/.env")):
            if l.startswith("HELIUS_API_KEY"):
                k = l.split("=", 1)[1].strip().strip('"').strip("'")
                if k:
                    hosts.append(f"https://mainnet.helius-rpc.com/?api-key={k}")
    except Exception:
        pass
    return hosts


HELIUS_HOSTS = _hosts_helius()
HELIUS = HELIUS_HOSTS[0] if HELIUS_HOSTS else None
_TURNO = [0]                       # contador para rotar las keys entre llamadas
# OJO con las versiones de transacción: desde 2026 la red trae tx **v1** y pedirlas con
# `maxSupportedTransactionVersion: 0` NO da error de red: el nodo devuelve error/nulo por tx.
# Medido: la misma docena de tx, con 0 → 1 con datos; con 1 → 14 de 14. Casi todo el historial
# de una wallet activa se ve "vacío" si no se sube la versión.
MAX_VERSION = 1
RPCS = HELIUS_HOSTS + [
    "https://api.mainnet-beta.solana.com",
    "https://rpc.solanatracker.io/public",
    "https://solana-rpc.publicnode.com",
    "https://solana.api.onfinality.io/public",
]
# El paginado necesita un host que devuelva la página completa (limit=1000): medido 2026-10-06,
# api.mainnet-beta → 1.000 · solanatracker → 112 · publicnode → 565 · onfinality → 429.
# Con Helius (key en .env) ese problema desaparece: pagina las 1.000 y no limita.
RPCS_FIRMAS = HELIUS_HOSTS + [
    "https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com"]
RPCS_TX = HELIUS_HOSTS + [
    "https://rpc.solanatracker.io/public", "https://solana-rpc.publicnode.com",
    "https://api.mainnet-beta.solana.com"]
MIN_SOL_MOV = 0.01        # movimientos de SOL menores a esto se ignoran (ruido/polvo)
MIN_TOK_AIRE = 1e-9
VISTAS_MAX = 400          # firmas recordadas por wallet


def log(msg):
    linea = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(linea, flush=True)
    try:
        os.makedirs(LOG, exist_ok=True)
        with open(os.path.join(LOG, "tracker.log"), "a") as fh:
            fh.write(linea + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ estado

def _leer_json(path, por_defecto):
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except Exception as e:
            log(f"! no pude leer {os.path.basename(path)}: {e}")
    return por_defecto


def _guardar_json(path, datos):
    """Guarda JSON de forma atómica y sin pisarse entre procesos (ver jsonio).

    Los crons se solapan (patrulla cada 3 min, cosecha cada 15, muestreo cada 5): con un temporal de
    nombre fijo uno renombraba el archivo del otro y el proceso moría a mitad de camino.
    """
    import jsonio
    if not jsonio.guardar(path, datos):
        with open(path, "w") as fh:
            json.dump(datos, fh, ensure_ascii=False, indent=2)


def cargar_wallets():
    d = _leer_json(WALLETS, {"wallets": []})
    return d.get("wallets", d if isinstance(d, list) else [])


def guardar_wallets(lista):
    _guardar_json(WALLETS, {"wallets": lista})


def cargar_estado():
    return _leer_json(STATE, {"vistas": {}, "ultima_pasada": None})


# --------------------------------------------------------------------- red

def rpc(method, params, intentos=2):
    """Llama al RPC probando hosts; devuelve el campo 'result' o None.

    El orden de hosts depende del método: `getSignaturesForAddress` sólo pagina bien contra el
    RPC oficial (los demás cortan la lista y la wallet "parece" tener 100 tx), y `getTransaction`
    conviene pedirlo a solanatracker, que aguanta la carga sin dar 429.
    """
    hosts = RPCS_FIRMAS if "SignaturesForAddress" in method else (
        RPCS_TX if method == "getTransaction" else RPCS)
    # Rotar SÓLO las keys de Helius (el resto de los hosts queda de respaldo en su orden): así el
    # gasto se reparte entre las dos cuentas en vez de agotar la primera.
    n_h = len(HELIUS_HOSTS)
    if n_h > 1 and len(hosts) > n_h:
        i = _TURNO[0] % n_h
        hosts = hosts[i:n_h] + hosts[:i] + hosts[n_h:]
        _TURNO[0] += 1
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for i in range(intentos):
        for url in hosts:
            try:
                req = urllib.request.Request(url, data=cuerpo,
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "hermes-wallet-tracker/1.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    d = json.loads(r.read().decode())
                if "result" in d:
                    return d["result"]
            except Exception:
                continue
        time.sleep(2)
    return None


def simbolo(mint, cache):
    """Símbolo del token, cacheado (DexScreener; si no, corto el mint)."""
    if mint in cache:
        return cache[mint]
    info = {"symbol": mint[:6], "name": mint[:6], "image": None}
    d = PA.http_json(f"{PA.DS}/latest/dex/tokens/{mint}", tries=1, timeout=12)
    for p in (d or {}).get("pairs") or []:
        bt = p.get("baseToken") or {}
        qt = p.get("quoteToken") or {}
        img = (p.get("info") or {}).get("imageUrl")
        if bt.get("address") == mint:
            info = {"symbol": bt.get("symbol") or mint[:6], "name": bt.get("name") or mint[:6], "image": img}
            break
        if qt.get("address") == mint:
            info = {"symbol": qt.get("symbol") or mint[:6], "name": qt.get("name") or mint[:6], "image": img}
            break
    if not info.get("image"):
        # respaldo: la imagen de pump.fun (su CDN por mint la tiene casi siempre)
        try:
            info["image"] = PA.imagen_de(mint, coin=PA.buscar_coin(mint))
        except Exception:
            pass
    cache[mint] = info
    return info


# --------------------------------------------------------------- parseo tx

def cuentas_tx(tx):
    """Cuentas de una tx en el MISMO orden que pre/postBalances.

    Las tx versionadas (v0/v1) traen parte de las cuentas en `meta.loadedAddresses` (las que salen
    de una lookup table): si se usan sólo las estáticas de `message.accountKeys`, los índices se
    desalinean con los arrays de saldos y el delta que se lee **es de otra cuenta**. Hay que
    concatenar estáticas + writable + readonly.
    """
    msg = tx["transaction"]["message"]
    out = [(k.get("pubkey") if isinstance(k, dict) else k) for k in (msg.get("accountKeys") or [])]
    la = (tx.get("meta") or {}).get("loadedAddresses") or {}
    out.extend(la.get("writable") or [])
    out.extend(la.get("readonly") or [])
    return out


def parsear_tx(tx, wallet):
    """Traduce una transacción a: acción, mint, cantidad de token, SOL y si es swap."""
    if not tx or not tx.get("meta") or tx["meta"].get("err"):
        return None
    meta = tx["meta"]
    try:
        keys = cuentas_tx(tx)
    except Exception:
        return None
    idx = None
    for i, pub in enumerate(keys):
        if pub == wallet:
            idx = i
    pre_b = meta.get("preBalances") or []
    post_b = meta.get("postBalances") or []
    sol = 0.0
    if idx is not None and idx < len(pre_b) and idx < len(post_b):
        sol = (post_b[idx] - pre_b[idx]) / 1e9

    def saldos(lst):
        out = {}
        for b in lst or []:
            if b.get("owner") != wallet:
                continue
            amt = ((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            out[b.get("mint")] = float(amt)
        return out

    antes, despues = saldos(meta.get("preTokenBalances")), saldos(meta.get("postTokenBalances"))
    deltas = {}
    for m in set(antes) | set(despues):
        d = despues.get(m, 0.0) - antes.get(m, 0.0)
        if abs(d) > MIN_TOK_AIRE:
            deltas[m] = d
    if not deltas:
        if abs(sol) >= MIN_SOL_MOV:
            return {"accion": "sol", "mint": None, "cantidad": None, "sol": sol, "swap": False,
                    "fee_sol": (meta.get("fee") or 0) / 1e9}
        return None
    mint, delta = max(deltas.items(), key=lambda kv: abs(kv[1]))
    if abs(sol) < MIN_SOL_MOV and not (delta < 0 and abs(sol) < MIN_SOL_MOV and False):
        # sin movimiento de SOL no hay operación: es polvo, airdrop basura o transferencia interna
        return None
    if delta > 0 and sol < -MIN_SOL_MOV:
        accion = "compra"
    elif delta < 0 and sol > MIN_SOL_MOV:
        accion = "venta"
    elif delta > 0:
        accion = "recibe"          # token regalado / airdrop / transferencia entrante
    else:
        accion = "envia"
    return {"accion": accion, "mint": mint, "cantidad": abs(delta), "sol": sol,
            "swap": accion in ("compra", "venta"), "fee_sol": (meta.get("fee") or 0) / 1e9}


# ------------------------------------------------------------------ entrega

def _env(path="~/.hermes/.env"):
    out = {}
    p = os.path.expanduser(path)
    if os.path.exists(p):
        for linea in open(p):
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def enviar(texto, canales=("discord",)):    # solo Discord (pedido del usuario)
    env = _env()
    res = {}
    if "telegram" in canales:
        tok = env.get("TELEGRAM_BOT_TOKEN")
        chat = env.get("TELEGRAM_WALLETS_CHANNEL") or env.get("TELEGRAM_HOME_CHANNEL")
        if tok and chat:
            try:
                cuerpo = urllib.parse.urlencode({"chat_id": chat, "text": texto[:4000],
                                                 "disable_web_page_preview": "true"}).encode()
                with urllib.request.urlopen(urllib.request.Request(
                        f"https://api.telegram.org/bot{tok}/sendMessage", data=cuerpo), timeout=30) as r:
                    res["telegram"] = json.loads(r.read().decode()).get("ok", False)
            except Exception as e:
                res["telegram"] = f"error: {e}"
        else:
            res["telegram"] = "sin token/chat"
    if "discord" in canales:
        hook = env.get("DISCORD_WALLETS_WEBHOOK") or env.get("DISCORD_WEBHOOK_URL")
        tok = env.get("DISCORD_BOT_TOKEN")
        canal = (env.get("DISCORD_TRACKED_WALLETS_CHANNEL") or env.get("DISCORD_WALLETS_CHANNEL")
                 or env.get("DISCORD_PUMP_CHANNEL")
                 or env.get("DISCORD_SIGNALS_CHANNEL") or env.get("DISCORD_HOME_CHANNEL"))
        partes, cur = [], ""
        for bloque in texto.split("\n\n"):
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
            ok = True
            for p in partes:
                try:
                    # Discord devuelve 403 al User-Agent por defecto de urllib: hay que identificarse
                    headers = {"Content-Type": "application/json", "User-Agent": "hermes-wallet-tracker/1.0"}
                    payload = {"content": p}
                    if hook:
                        payload["username"] = "Tracker de wallets"
                    else:
                        headers["Authorization"] = f"Bot {tok}"
                    with urllib.request.urlopen(urllib.request.Request(
                            url, data=json.dumps(payload).encode(), headers=headers, method="POST"),
                            timeout=30) as r:
                        ok = ok and (r.status in (200, 204))
                except Exception as e:
                    ok = False
                    log(f"! discord: {e}")
            res["discord"] = ok
    return res


def fonders_de(wallet, cache, dias=7):
    """Quién financió a la wallet (cacheado 7 días: sale caro y cambia poco)."""
    reg = cache.get("_fondeos", {}).get(wallet)
    if reg and (time.time() - reg.get("ts", 0)) < dias * 86400:
        return reg.get("fonders", [])
    try:
        import wallet_forense as WF
        _, recibido, _ = WF.flujos_sol(wallet, 25)
        fonders = [w for w, _ in sorted(recibido.items(), key=lambda kv: -kv[1]["sol"])[:6]]
    except Exception:
        fonders = []
    cache.setdefault("_fondeos", {})[wallet] = {"ts": time.time(), "fonders": fonders}
    return fonders


def analizar_privilegio(wallet, ev, coin, cache):
    """¿Esta compra huele a información privilegiada? Tres señales independientes."""
    creado = (coin.get("created_timestamp") or 0) / 1000
    ts = ev.get("ts") or 0
    seg = round(ts - creado) if (creado and ts) else None
    creador = coin.get("creator")
    fonders = fonders_de(wallet, cache)
    coincide = bool(creador and creador in fonders)
    # cuántos tokens operó y si recibió tokens sin comprar (patrón de acceso al supply)
    sin_compra = None
    try:
        import auditor_wallets as AU
        a = AU.auditar_wallet(wallet, 30)
        sin_compra = (a or {}).get("pct_sin_compra")
    except Exception:
        pass
    señales = []
    if seg is not None and seg <= 300:
        señales.append(f"compró a los {seg} s de creado el token (entró en el arranque)")
    if coincide:
        señales.append("el CREADOR del token financió a esta wallet → vínculo directo con el dev")
    if sin_compra is not None and sin_compra >= 40:
        señales.append(f"{sin_compra:.0f}% de sus posiciones las cobró sin comprar (acceso al supply)")
    return {"seg_desde_creacion": seg, "creador": creador, "creador_la_financia": coincide,
            "sin_compra": sin_compra, "señales": señales,
            # recibir tokens sin comprarlos ES acceso privilegiado (no una señal menor)
            "nivel": ("alta" if (coincide or (sin_compra is not None and sin_compra >= 40))
                      else ("media" if len(señales) >= 2 else ("baja" if señales else "sin señales")))}


COLOR_ACCION = {"compra": 0x2ECC71, "venta": 0xE74C3C, "recibe": 0x3498DB,
                "envia": 0x95A5A6, "sol": 0xF1C40F}
EMOJI = {"compra": "\U0001F7E2", "venta": "\U0001F534", "recibe": "\U0001F381",
         "envia": "\U0001F4E4", "sol": "\U0001F4B0"}
VERBO = {"compra": "compró", "venta": "vendió", "recibe": "recibió", "envia": "envió", "sol": "movió SOL"}


SEP = "\u2500" * 20
NIVEL_TXT = {"alta": "\U0001F6A8 ALTA", "media": "\U0001F7E0 MEDIA", "baja": "\U0001F7E1 BAJA"}


def embed_de_evento(ev, etiqueta, analisis=None, privilegio=None):
    """Aviso de movimiento: 1) qué hizo  2) si el token vale  3) si hay info privilegiada."""
    info = ev.get("info") or {}
    tok = info.get("symbol") or (ev.get("mint") or "")[:6]
    verbo = VERBO.get(ev["accion"], ev["accion"])
    emoji = EMOJI.get(ev["accion"], "")
    titulo = f"{emoji} {verbo.upper()} {tok}".strip() if ev.get("mint") else f"{emoji} MOVIÓ SOL"
    campos = [
        {"name": "\U0001F4B0 SOL", "value": f"`{ev['sol']:+.4f}`", "inline": True},
    ]
    if ev.get("cantidad") is not None:
        campos.append({"name": "\U0001FA99 Tokens", "value": f"`{ev['cantidad']:.6g}`", "inline": True})
    campos.append({"name": "\U0001F551 Hora", "value": f"`{ev.get('hora') or '?'}`", "inline": True})
    if ev.get("mint"):
        campos.append({"name": "\U0001F517 Enlaces",
                       "value": (f"[gráfico](https://dexscreener.com/solana/{ev['mint']}) \u00b7 "
                                 f"[token](https://solscan.io/token/{ev['mint']}) \u00b7 "
                                 f"[transacción](https://solscan.io/tx/{ev['sig']})"),
                       "inline": False})
    if analisis:
        c = analisis["contexto"]
        val = [f"**{analisis['etiqueta']} \u00b7 {analisis['puntaje']}/100**",
               f"\u203A mcap `{PA.usd(c['mcap_usd'])}` \u00b7 curva `{c['curva_pct']}%` \u00b7 "
               f"edad `{PA.fmt_edad(c['edad_min'])}`",
               f"\u203A top10 `{c['top10_pct']}%` \u00b7 liquidez `{PA.usd(c['liquidez_usd'])}` \u00b7 "
               f"holders `{c['holders'] or '?'}`"]
        val += [f"\u203A {x}" for x in (analisis["rojas"] + analisis["amarillas"])[:3]]
        campos.append({"name": "\U0001F9EA ¿Buena compra?", "value": "\n".join(val)[:1024], "inline": False})
    if privilegio:
        _sin = "\u26AA SIN SEÑALES"
        val = [f"**{NIVEL_TXT.get(privilegio['nivel'], _sin)}**"]
        val += [f"\u203A {s}" for s in privilegio["señales"]] or ["\u203A sin señales en esta compra"]
        campos.append({"name": "\U0001F575\U0000FE0F Info privilegiada",
                       "value": "\n".join(val)[:1024], "inline": False})
    em = {
        "title": titulo,
        "description": f"### {etiqueta}\n{SEP}",
        "color": COLOR_ACCION.get(ev["accion"], 0x95A5A6),
        "fields": campos,
        "footer": {"text": (ev.get("mint") or "wallet tracker") + " \u00b7 solo informa, no opera"},
    }
    if ev.get("ts"):
        em["timestamp"] = datetime.fromtimestamp(ev["ts"], timezone.utc).isoformat()
    if privilegio and (privilegio.get("nivel") == "alta"
                       or (privilegio.get("seg_desde_creacion") is not None
                           and privilegio["seg_desde_creacion"] <= 300)):
        em["color"] = 0xE67E22
    if info.get("image"):
        em["image"] = {"url": "attachment://token.png"}
    return em


def alertar_evento(ev, etiqueta, analisis=None, privilegio=None):
    """Manda el embed del movimiento (con imagen si el token la tiene) a Discord."""
    base = os.path.join(os.path.expanduser("~/.hermes/cache/scratch"), f"wt_{ev['mint'][:8] if ev.get('mint') else ev['sig'][:8]}")
    img = PA.descargar_imagen((ev.get("info") or {}).get("image"), base) if ev.get("info") else None
    ok = PA.enviar_embed(embed_de_evento(ev, etiqueta, analisis, privilegio),
                         contenido=f"{EMOJI.get(ev['accion'],'')} **{etiqueta}** \u00b7 wallet tracker",
                         imagen=img, canal=_env().get("DISCORD_TRACKED_WALLETS_CHANNEL"))
    if not ok:
        ok = enviar(formatear(ev.get("wallet") or "", etiqueta, [ev], analisis), canales=("discord",))
    return ok


def formatear(wallet, etiqueta, eventos, analisis=None):
    l = []
    for ev in eventos:
        emoji = {"compra": "🟢", "venta": "🔴", "recibe": "🎁", "envia": "📤", "sol": "💰"}[ev["accion"]]
        if ev["mint"]:
            tok = ev.get("info") or {"symbol": "?", "name": "?"}
            cab = f"{emoji} <b>{etiqueta}</b> {ev['accion'].upper()} {tok['symbol']}"
            l.append(cab)
            l.append(f"   mint: {ev['mint']}")
            l.append(f"   cantidad: {ev['cantidad']:.6g} · SOL movido: {ev['sol']:+.4f}")
        else:
            l.append(f"{emoji} <b>{etiqueta}</b> movió SOL: {ev['sol']:+.4f}")
        l.append(f"   https://solscan.io/tx/{ev['sig']}")
        l.append(f"   wallet: {wallet}")
        l.append("")
    if analisis:
        c = analisis["contexto"]
        l.append(f"🧪 Análisis automático: {analisis['etiqueta']} ({analisis['puntaje']}/100)")
        l.append(f"   · mcap {PA.usd(c['mcap_usd'])} · curva {c['curva_pct']}% · edad {PA.fmt_edad(c['edad_min'])}")
        l.append(f"   · top10 {c['top10_pct']}% · liquidez {PA.usd(c['liquidez_usd'])} · holders {c['holders']}")
        for x in (analisis["rojas"] + analisis["amarillas"])[:4]:
            l.append(f"   · {x}")
    return "\n".join(l)


# --------------------------------------------------------------- descubrir

def descubrir(n_tokens=25, minimo_apariciones=2):
    """Propone wallets: los que aparecen como holders grandes en varios tokens que ya corrieron."""
    log("descubriendo wallets desde los tokens que están corriendo…")
    coins = PA.pump_movidos(100) + PA.pump_live(50)
    vistos, candidatos = set(), {}
    usados = 0
    for c in sorted(coins, key=lambda x: -(x.get("usd_market_cap") or 0)):
        if usados >= n_tokens:
            break
        mc = c.get("usd_market_cap") or 0
        if mc < 50_000:          # solo tokens que ya se movieron de verdad
            continue
        mint = c.get("mint")
        if not mint or mint in vistos:
            continue
        vistos.add(mint)
        usados += 1
        rc = PA.rugcheck(mint)
        if not rc:
            continue
        conocidas = rc.get("knownAccounts") or {}
        for h in (rc.get("topHolders") or [])[:15]:
            owner = h.get("owner")
            if not owner or (h.get("pct") or 0) <= 0:
                continue
            info = conocidas.get(owner) or {}
            if (info.get("type") or "").upper() in ("AMM", "LOCKER", "CREATOR", "POOL", "BURN", "FEE"):
                continue
            if h.get("insider"):
                continue
            d = candidatos.setdefault(owner, {"veces": 0, "tokens": [], "pct_suma": 0.0})
            d["veces"] += 1
            d["tokens"].append((c.get("symbol") or "?", round(mc), round(h.get("pct") or 0, 2)))
            d["pct_suma"] += (h.get("pct") or 0)
    orden = [(v, k) for k, v in candidatos.items() if v["veces"] >= minimo_apariciones]
    orden.sort(key=lambda kv: (-kv[0]["veces"], -kv[0]["pct_suma"]))
    return orden[:30]


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Tracker de wallets de Solana")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--agregar", nargs=2, metavar=("DIRECCION", "ETIQUETA"))
    ap.add_argument("--quitar", metavar="DIRECCION")
    ap.add_argument("--descubrir", type=int, metavar="N_TOKENS")
    ap.add_argument("--una-pasada", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SEGUNDOS")
    ap.add_argument("--dry-run", action="store_true", help="no envía ni guarda estado")
    ap.add_argument("--sin-analisis", action="store_true", help="no corre el analizador en las compras")
    ap.add_argument("--max-tx", type=int, default=10, help="cuántas firmas mirar por wallet")
    ap.add_argument("--avisar-historial", action="store_true",
                    help="avisar también las transacciones viejas al agregar una wallet (por defecto no)")
    a = ap.parse_args()

    if a.listar:
        ws = cargar_wallets()
        print(f"{len(ws)} wallets seguidas:")
        for w in ws:
            print(f"  {w['address']}  {w.get('etiqueta','')}")
        return 0

    if a.agregar:
        addr, etq = a.agregar
        if len(addr) < 32:
            print("esa dirección no parece válida (muy corta)")
            return 1
        ws = cargar_wallets()
        if any(w["address"] == addr for w in ws):
            print("ya estaba en la lista")
            return 0
        ws.append({"address": addr, "etiqueta": etq,
                   "agregada": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        guardar_wallets(ws)
        print(f"agregada: {addr} ({etq}) — total {len(ws)}")
        return 0

    if a.quitar:
        ws = cargar_wallets()
        antes = len(ws)
        ws = [w for w in ws if w["address"] != a.quitar]
        guardar_wallets(ws)
        print(f"quitada ({antes} → {len(ws)})" if antes != len(ws) else "no estaba en la lista")
        return 0

    if a.descubrir:
        orden = descubrir(a.descubrir)
        if not orden:
            print("no encontré candidatos con esos filtros")
            return 0
        print(f"{len(orden)} candidatos (aparecen como holders grandes en varios tokens que corrieron):\n")
        for v, addr in orden:
            toks = ", ".join(f"{s}({mc//1000}k,{p}%)" for s, mc, p in v["tokens"][:4])
            print(f"  {addr}  · en {v['veces']} tokens: {toks}")
        print("\n⚠️ Aparecer en top holders NO es lo mismo que ser rentable: es una lista de partida,")
        print("   no una recomendación. Comprobá el PnL de cada una antes de seguirla.")
        print("\nPara agregar la que elijas:")
        print(f"  python3 {os.path.basename(__file__)} --agregar <direccion> \"etiqueta\"")
        return 0

    if not (a.una_pasada or a.loop):
        ap.print_help()
        return 0

    def pasada():
        ws = cargar_wallets()
        if not ws:
            log("no hay wallets en la lista: usá --agregar o --descubrir")
            return 0
        est = cargar_estado()
        cache = _leer_json(CACHE, {})
        cache.pop("_pendiente", None)
        cache.pop("_vacio", None)
        analizados = set(cache.get("_analizados") or [])
        cambios = 0
        for w in ws:
            addr, etq = w["address"], w.get("etiqueta") or w["address"][:6]
            sigs = rpc("getSignaturesForAddress", [addr, {"limit": a.max_tx}])
            if not sigs:
                log(f"  {etq}: sin respuesta del RPC")
                continue
            primera_vez = addr not in est["vistas"]
            vistas = est["vistas"].setdefault(addr, [])
            nuevas = [s for s in sigs if s.get("signature") not in vistas]
            if primera_vez and not a.avisar_historial:
                log(f"  {etq}: línea base ({len(nuevas)} firmas anotadas, sin avisar) "
                    f"— los avisos empiezan en la próxima pasada")
                est["vistas"][addr] = [s["signature"] for s in sigs][-VISTAS_MAX:]
                continue
            for s in reversed(nuevas):                      # de la más vieja a la más nueva
                if s.get("err"):
                    vistas.append(s["signature"])
                    continue
                tx = rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": MAX_VERSION,
                                                             "encoding": "jsonParsed"}])
                ev = parsear_tx(tx, addr) if tx else None
                if ev:
                    info = simbolo(ev["mint"], cache) if ev["mint"] else None
                    ev.update({"sig": s["signature"], "info": info,
                               "hora": datetime.fromtimestamp(s.get("blockTime") or time.time(),
                                                              timezone.utc).strftime("%H:%M:%S UTC")})
                    ev["ts"] = s.get("blockTime") or 0
                    analisis = None
                    privilegio = None
                    if ev["accion"] == "compra" and ev["mint"] and not a.sin_analisis:
                        try:
                            coin = PA.buscar_coin(ev["mint"])
                            if ev["mint"] not in analizados:
                                analisis = PA.analizar(coin, pedir=True, SOL_USD=PA.precio_sol())
                                analizados.add(ev["mint"])
                            privilegio = analizar_privilegio(addr, ev, coin, cache)
                            log(f"    privilegio: {privilegio['nivel']} "
                                f"({len(privilegio['señales'])} señal/es)")
                        except Exception as e:
                            log(f"  ! análisis falló para {ev['mint'][:8]}: {e}")
                    ev["wallet"] = addr
                    log(f"  {etq}: {ev['accion']} {(ev['info'] or {}).get('symbol','')} "
                        f"{ev['sol']:+.4f} SOL{' + análisis' if analisis else ''}")
                    if a.dry_run:
                        print(json.dumps(embed_de_evento(ev, etq, analisis), ensure_ascii=False)[:500])
                    else:
                        log(f"    envío: {alertar_evento(ev, etq, analisis, privilegio)}")
                    cambios += 1
                vistas.append(s["signature"])
            est["vistas"][addr] = vistas[-VISTAS_MAX:]
        cache["_analizados"] = sorted(analizados)[-500:]
        est["ultima_pasada"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not a.dry_run:
            _guardar_json(STATE, est)
            _guardar_json(CACHE, cache)
        log(f"pasada lista: {cambios} movimiento(s) nuevo(s)")
        return cambios

    if a.loop:
        log(f"bucle cada {a.loop}s (Ctrl+C para cortar)")
        while True:
            try:
                pasada()
            except KeyboardInterrupt:
                break
            except Exception as e:
                log(f"! error en la pasada: {e}")
            time.sleep(a.loop)
        return 0
    pasada()
    return 0


if __name__ == "__main__":
    sys.exit(main())
