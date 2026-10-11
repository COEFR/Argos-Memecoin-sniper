#!/usr/bin/env python3
"""Auditoría de TODOS los contratos publicados en el server: ¿cuáles dieron profit?

Qué hace:
  1. Lee los mensajes de los canales del server y extrae las direcciones de token (mints) publicadas.
  2. Para cada mint busca su referencia de precio: el FDV del aviso (si salió por el sistema), el FDV
     de la cosecha, o el primer dato del mercado; y su pico posterior (serie medida o mercado ahora).
  3. Saca el veredicto y arma la tabla + el resumen, y (con --discord) lo publica.

Referencias de la medición (igual que el resto del sistema):
  · el múltiplo se mide desde el PRIMER dato que tuvimos del token (aviso o cosecha) hasta su pico.
  · ≥1,5x = hubo profit real · 1,05-1,5x = marginal · <1,05x = no hubo (nunca pasó del punto de partida).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pump_analizador as PA          # noqa: E402

BASE58 = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
NO_ES_TOKEN = {"So11111111111111111111111111111111111111112",       # wSOL
               "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",       # USDC
               "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}       # USDT


def _env():
    d = {}
    for l in open(os.path.expanduser("~/.hermes/.env")):
        if "=" in l and not l.strip().startswith("#"):
            k, v = l.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def leer_mensajes(token, canal, paginas=4):
    """Mensajes del canal (hasta `paginas` x 100), del más nuevo al más viejo."""
    H = {"Authorization": "Bot " + token, "User-Agent": "hermes/1.0"}
    out, antes = [], None
    for _ in range(paginas):
        url = f"https://discord.com/api/v10/channels/{canal}/messages?limit=100"
        if antes:
            url += f"&before={antes}"
        try:
            msgs = json.loads(urllib.request.urlopen(
                urllib.request.Request(url, headers=H), timeout=25).read().decode())
        except Exception:
            break
        if not msgs:
            break
        out += msgs
        antes = msgs[-1]["id"]
        time.sleep(0.4)
    return out


def mints_del_server():
    """{mint: {ts, canal, autor}} de todos los contratos publicados en los canales."""
    e = _env()
    tok = e["DISCORD_BOT_TOKEN"]
    canales = {k: v for k, v in e.items() if k.startswith("DISCORD_") and k.endswith("CHANNEL") and v.isdigit()}
    canales["#trading-argos-seguir"] = e.get("DISCORD_ARGOS_SEGUIR")
    canales["#trading-argos-loteria"] = e.get("DISCORD_ARGOS_LOTERIA")
    canales["#trading-argos-observar"] = e.get("DISCORD_ARGOS_OBSERVAR")
    out = {}
    for nombre, canal in canales.items():
        if not canal:
            continue
        for m in leer_mensajes(tok, canal):
            texto = (m.get("content") or "")
            for emb in (m.get("embeds") or []):
                texto += " " + (emb.get("description") or "") + " " + str(emb.get("footer") or "")
            for mt in BASE58.findall(texto):
                if mt in NO_ES_TOKEN:
                    continue
                hora = m.get("timestamp") or ""
                e0 = out.setdefault(mt, {"ts": hora, "canal": nombre,
                                         "autor": (m.get("author") or {}).get("username")})
                if hora and (not e0.get("ts") or hora < e0["ts"]):
                    e0["ts"] = hora
    return out


def referencia_y_pico(mint, reg, lanz, cache):
    """(fdv_inicial, pico, de_dónde) para un mint: aviso medido > cosecha > mercado ahora."""
    x = reg.get(mint)
    if x:
        seg = [s for s in (x.get("seguimiento") or []) if s.get("mcap")]
        if len(seg) >= 2 and x.get("mcap_call"):
            return x["mcap_call"], max(s["mcap"] for s in seg), "aviso medido"
        if x.get("mcap_call"):
            return x["mcap_call"], None, "aviso (sin serie)"
    d = lanz.get(mint)
    if d and d.get("mcap_cosecha"):
        mc = None
        if mint in cache:
            mc = cache[mint].get("mc")
        else:
            p = PA.dexscreener(mint)
            mc = (p.get("marketCap") or p.get("fdv")) if p else None
            cache[mint] = {"mc": mc}
            time.sleep(0.15)
        return d["mcap_cosecha"], mc, "cosecha"
    p = PA.dexscreener(mint)
    mc = (p.get("marketCap") or p.get("fdv")) if p else None
    return None, mc, "sólo mercado"


def main():
    ap = argparse.ArgumentParser(description="Auditoría de los contratos publicados en el server")
    ap.add_argument("--discord", action="store_true", help="publica el informe en el canal general")
    ap.add_argument("--n", type=int, default=20, help="cuántos mostrar en la tabla")
    a = ap.parse_args()

    reg = {x.get("mint"): x for x in (json.load(open(os.path.join(AQUI, "calls_historial.json"))) or [])}
    lanz = (json.load(open(os.path.join(AQUI, "lanzamientos.json"))) or {}).get("tokens", {})
    cache = {}
    log("leyendo los canales del server…")
    mints = mints_del_server()
    log(f"contratos encontrados en el server: {len(mints)}")

    filas = []
    for mint, meta in mints.items():
        m0, mp, fuente = referencia_y_pico(mint, reg, lanz, cache)
        if not m0 or not mp:
            filas.append({"mint": mint, "simbolo": str(lanz.get(mint, {}).get("simbolo") or "?"),
                          "mult": None, "fuente": fuente, "ts": meta.get("ts"), "canal": meta.get("canal")})
            continue
        filas.append({"mint": mint, "simbolo": str(lanz.get(mint, {}).get("simbolo") or "?"),
                      "mult": mp / m0, "m0": m0, "mp": mp, "fuente": fuente,
                      "ts": meta.get("ts"), "canal": meta.get("canal")})
    con = [f for f in filas if f["mult"]]

    def usd(v):
        return ("$%.0fk" % (v / 1000)) if v and v >= 1000 else ("$%.0f" % (v or 0))

    con.sort(key=lambda f: -f["mult"])
    lin = ["📋 **AUDITORÍA DE LOS CONTRATOS DEL SERVER**",
           f"_(del primer precio que tuvimos de cada uno a su pico — {len(filas)} contratos publicados, "
           f"{len(con)} con datos de precio)_", ""]
    gan = [f for f in con if f["mult"] >= 1.5]
    marg = [f for f in con if 1.05 <= f["mult"] < 1.5]
    per = [f for f in con if f["mult"] < 1.05]
    lin.append(f"✅ **Dieron profit (≥1,5x): {len(gan)}**")
    for f in gan[:a.n]:
        lin.append(f"   · {f['mult']:5.2f}x  ${f['simbolo'][:12]}  ({usd(f['m0'])} → {usd(f['mp'])})")
    if len(gan) > a.n:
        lin.append(f"   … y {len(gan)-a.n} más")
    lin.append("")
    lin.append(f"🟡 **Marginal (1,05x-1,5x): {len(marg)}**")
    lin.append(f"❌ **Sin profit (<1,05x): {len(per)}**")
    if con:
        lin.append("")
        lin.append(f"📊 mediana {sorted(f['mult'] for f in con)[len(con)//2]:.2f}x · "
                   f"total medidos {len(con)}")
    texto = "\n".join(lin)
    print(texto)
    try:
        import jsonio
        jsonio.guardar(os.path.join(AQUI, "auditoria_server.json"),
                       {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "contratos": len(filas), "con_precio": len(con),
                        "profit": len(gan), "marginal": len(marg), "sin_profit": len(per),
                        "filas": sorted(con, key=lambda f: -f["mult"])})
    except Exception as e:
        log(f"  ! no pude guardar el resultado: {e}")
    if a.discord:
        import cohortes as CO
        PA.enviar_en_canal(texto[:1900], canal=CO.canal_argos())
        log("informe publicado en el canal general")
    return 0


if __name__ == "__main__":
    sys.exit(main())
