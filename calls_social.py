#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CALLS SOCIALES — leer los canales públicos de Telegram y convertirlos en calls puntuadas.

Por qué: los grupos pagos de verdad (Pastel Alpha, Heaven or Hell) no tienen vista pública, pero hay
canales públicos que **publican calls** (y algunos hasta anuncian el pump con fecha y hora). Se leen
**sin cuenta y sin API** por la vista web `https://t.me/s/<canal>`, que devuelve los últimos mensajes
en HTML. De cada mensaje se sacan las direcciones (base58) y los $SÍMBOLOS, se resuelve el token, se
analiza con el motor propio y **se avisa puntuado también por el historial del canal**.

El puntaje de una call social NO usa la fuerza de la ráfaga (no la hay): usa la calidad del token +
el **historial medido del canal** (si sus calls anteriores dieron resultado, sube; si no, baja). Ese
historial sale del mismo registro que mide los resultados a 1/6/24 h, así que el sistema aprende solo.

Uso:
  python3 calls_social.py --una-pasada          # lo que corre el cron
  python3 calls_social.py --listar-canales      # probar qué canales tienen vista pública
  python3 calls_social.py --canal pastel_alpha  # leer un canal y mostrar lo que saca (sin avisar)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pump_analizador as PA          # noqa: E402
import calls_detector as CD           # noqa: E402
import registro_calls as RC           # noqa: E402

ESTADO = os.path.join(AQUI, "calls_social_estado.json")
CANALES_DEF = ["pastel_alpha"]        # agregá acá los canales públicos que quieras seguir
RX_MINT = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
RX_SYM = re.compile(r"\$([A-Za-z0-9_]{2,12})\b")
UA = "Mozilla/5.0 (compatible; hermes-calls/1.0)"
MX_EDAD_CALL = 6 * 3600               # sólo tokens creados en las últimas 6 h (una call es de lanzamiento)
MIN_LIQ = 3_000


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def _estado():
    try:
        return json.load(open(ESTADO))
    except Exception:
        return {"vistos": [], "canales": CANALES_DEF}


def _guardar_estado(e):
    tmp = ESTADO + ".tmp"
    json.dump(e, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO)


def leer_canal(canal):
    """Baja la vista pública del canal y devuelve [(id_mensaje, texto)] en orden."""
    url = f"https://t.me/s/{canal}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            if r.status != 200:
                return []
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return []
    bloques = re.findall(r'data-post="[^"]*?/(\d+)".*?tgme_widget_message_text[^>]*>(.*?)</div>',
                         html, re.S)
    salida = []
    for mid, cuerpo in bloques:
        texto = re.sub(r"<br\s*/?>", "\n", cuerpo)
        texto = re.sub(r"<[^>]+>", " ", texto)
        for a, b in (("&amp;", "&"), ("&#036;", "$"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
            texto = texto.replace(a, b)
        texto = re.sub(r"[ \t]+", " ", texto).strip()
        if texto:
            salida.append((mid, texto))
    return salida


def resolver_mint(candidato):
    """De un $SÍMBOLO saca el mint: busca en DexScreener y se queda con el par Solana de mejor
    liquidez creado recientemente (así no agarra el token viejo con el mismo símbolo)."""
    try:
        d = PA.http_json(f"{PA.DS}/latest/dex/search?q={urllib.parse.quote(candidato)}", tries=1, timeout=15)
    except Exception:
        return None
    pares = [p for p in ((d or {}).get("pairs") or []) if p.get("chainId") == "solana"]
    if not pares:
        return None
    ahora = time.time() * 1000
    frescos = [p for p in pares if (p.get("pairCreatedAt") or 0) and (ahora - p["pairCreatedAt"]) < MX_EDAD_CALL * 1000]
    if not frescos:
        return None
    mejor = max(frescos, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    if ((mejor.get("liquidity") or {}).get("usd") or 0) < MIN_LIQ:
        return None
    return (mejor.get("baseToken") or {}).get("address")


def historial_canal(canal, d=None):
    """Cuántas calls de ese canal se midieron y cómo salieron (para puntuar al canal)."""
    d = d if d is not None else RC._leer()
    propias = [x for x in d if x.get("canal") == canal and RC._pico(x)[0] is not None]
    if not propias:
        return {"n": 0, "pico_prom": None, "ventanas": 0}
    picos = [RC._pico(x)[0] for x in propias]
    return {"n": len(propias), "pico_prom": round(sum(picos) / len(picos), 2),
            "ventanas": len([p for p in picos if p > 1.05])}


def puntuar_social(ctx, hist):
    """Puntaje 0-10 de una call social: calidad del token + historial medido del canal."""
    p, motivos = 0.0, []
    mcap = ctx.get("mcap_usd") or 0
    liq = ctx.get("liquidez_usd") or 0
    curva = ctx.get("curva_pct") or 0
    edad = ctx.get("edad_min") or 0
    if mcap >= 200_000:
        p += 3.0
        motivos.append(f"**+3,0 token con cuerpo** · mcap {CD._usd(mcap)}")
    elif mcap >= 100_000:
        p += 2.0
        motivos.append(f"**+2,0** · mcap {CD._usd(mcap)}")
    elif mcap >= 50_000:
        p += 1.0
        motivos.append(f"**+1,0** · mcap {CD._usd(mcap)}")
    else:
        p -= 1.0
        motivos.append(f"**−1,0 token chico** · mcap {CD._usd(mcap)}")
    if liq >= 20_000 or curva >= 50:
        p += 2.0
        motivos.append(f"**+2,0 salida disponible** · liq {CD._usd(liq)} · curva {curva}%")
    elif liq < MIN_LIQ:
        p -= 1.5
        motivos.append(f"**−1,5 sin salida** · liq {CD._usd(liq)}: no vas a poder vender")
    if 0 < edad <= 30:
        p += 1.0
        motivos.append(f"**+1,0 call fresca** · token de {edad:.0f} min")
    elif edad > 180:
        p -= 1.0
        motivos.append(f"**−1,0 llegamos tarde** · token de {edad / 60:.1f} h")
    if hist.get("n", 0) >= 5:
        if (hist.get("pico_prom") or 0) > 1.2:
            p += 2.0
            motivos.append(f"**+2,0 el canal da resultado** · {hist['ventanas']}/{hist['n']} de sus calls "
                           f"tuvieron ventana (pico prom {hist['pico_prom']}x)")
        elif (hist.get("pico_prom") or 0) < 0.9:
            p -= 2.0
            motivos.append(f"**−2,0 el canal NO da resultado** · pico prom {hist['pico_prom']}x en "
                           f"{hist['n']} calls medidas")
    else:
        motivos.append(f"**±0 canal sin historial** · {hist.get('n', 0)} calls medidas (neutro)")
    p = max(0.0, min(10.0, p))
    etq = "🔥 ALTA" if p >= 8 else ("✅ MEDIA-ALTA" if p >= 6 else ("⚠️ MEDIA" if p >= 4 else "🚫 BAJA"))
    return round(p, 1), motivos, etq


def procesar(canal, avisar=True, dry=False):
    est = _estado()
    hist = historial_canal(canal)
    nuevas = []
    for mid, texto in leer_canal(canal):
        if mid in est["vistos"]:
            continue
        est["vistos"].append(mid)
        candidatos = []
        for m in RX_MINT.findall(texto):
            candidatos.append(m)
        for s in RX_SYM.findall(texto):
            candidatos.append(s)
        for cand in candidatos[:3]:
            mint = cand if len(cand) >= 32 else resolver_mint(cand)
            if not mint:
                continue
            try:
                ev = PA.analizar(PA.buscar_coin(mint), pedir=True)
            except Exception:
                continue
            ctx = ev.get("contexto") or {}
            if (ctx.get("edad_min") or 0) > MX_EDAD_CALL / 60:
                continue                      # no es una call de lanzamiento: ya pasó
            nota, motivos, etq = puntuar_social(ctx, hist)
            ev["call"] = {"nota": nota, "motivos": motivos, "etq": etq, "n_wallets": 0, "sol": 0,
                          "segundos": 0, "slots_iguales": 1, "frescas": 0, "conocidas": 0,
                          "compras_en_ventana": 0, "canal": canal,
                          "hora": datetime.now(timezone.utc).strftime("%H:%M") + " UTC",
                          "ts_iso": datetime.now(timezone.utc).isoformat(), "social": True}
            ev["por_rafaga"] = True
            ev["canal_social"] = canal
            nuevas.append((canal, texto[:120], ev, mint))
            if avisar and not dry:
                try:
                    import registro_calls as _RC
                    _RC.registrar(ev["call"], ev)      # se registra para medir su resultado
                except Exception:
                    pass
                PA.alertar_token(ev, "📣 CALL DE CANAL SOCIAL")
            break
        time.sleep(0.2)
    est["vistos"] = est["vistos"][-2000:]
    est.setdefault("canales", CANALES_DEF)
    _guardar_estado(est)
    return nuevas


def main():
    ap = argparse.ArgumentParser(description="Calls desde canales públicos de Telegram")
    ap.add_argument("--una-pasada", action="store_true")
    ap.add_argument("--canal", help="probar un canal (no avisa)")
    ap.add_argument("--listar-canales", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.listar_canales:
        for c in _estado().get("canales", CANALES_DEF):
            msgs = leer_canal(c)
            log(f"  {c:<22} mensajes públicos: {len(msgs)}"
                + (f" · último: {msgs[-1][1][:60]}" if msgs else " (sin vista pública)"))
        return 0
    if a.canal:
        for canal, texto, ev, mint in procesar(a.canal, avisar=False, dry=True):
            ctx = ev.get("contexto") or {}
            print(f"  {canal} · {ev['nombre']} (${ev['simbolo']}) · {ev['call']['nota']}/10 "
                  f"{ev['call']['etq']} · mcap {CD._usd(ctx.get('mcap_usd') or 0)} · {mint[:14]}")
            print(f"     mensaje: {texto[:90]}")
        return 0
    for c in _estado().get("canales", CANALES_DEF):
        for canal, texto, ev, mint in procesar(c, avisar=not a.dry_run):
            log(f"  📣 {canal}: {ev['nombre']} (${ev['simbolo']}) {ev['call']['nota']}/10 "
                f"{ev['call']['etq']} · {mint[:12]}")
    log("pasada lista")
    return 0


if __name__ == "__main__":
    sys.exit(main())
