#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Canal de auditoría de wallets — escucha #auditoria-wallets y contesta solo.

Cada pasada:
  1. Lee los mensajes recientes del canal.
  2. Detecta direcciones de Solana (base58) que todavía no auditó.
  3. Corre la forense (de dónde vino la plata, a dónde fue, qué compró) + la clasificación.
  4. Publica en el mismo canal: etiqueta de la wallet, su red, las wallets útiles y qué hacer.

No usa LLM: todo son reglas y datos públicos.

Uso:
  python3 canal_wallets.py --una-pasada            # lo que corre el cron
  python3 canal_wallets.py --wallet <dir>          # auditar una y publicarla a mano
  python3 canal_wallets.py --una-pasada --dry-run  # sin publicar
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
import wallet_forense as WF          # noqa: E402
import clasificar_wallets as CW      # noqa: E402
import pump_analizador as PA         # noqa: E402

CANAL = "1557047522357284916"        # #auditoria-wallets
ESTADO = os.path.join(AQUI, "canal_wallets_estado.json")
RX = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
COLOR = {"INFO PRIVILEGIADA": 0xE74C3C, "INSIDER": 0xE74C3C, "BOT/MEV": 0x95A5A6,
         "PIERDE": 0xE67E22, "CANDIDATA": 0x2ECC71, "PRIMISO": 0x3498DB, "SIN DATOS": 0x95A5A6}


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def _env():
    out = {}
    p = os.path.expanduser("~/.hermes/.env")
    for l in open(p):
        if "=" in l and not l.strip().startswith("#"):
            k, v = l.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def api(ruta, metodo="GET", cuerpo=None, tok=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(f"https://discord.com/api/v10/{ruta}", data=datos, method=metodo,
                                 headers={"Authorization": f"Bot {tok}", "User-Agent": "hermes-wallets/1.0",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            cuerpo_txt = r.read().decode()
        return r.status, (json.loads(cuerpo_txt) if cuerpo_txt else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return 0, str(e)


def embed_de_auditoria(addr, et, r):
    """Auditoría ordenada: veredicto → rastro del dinero → wallets útiles de la red."""
    v = r.get("veredicto") or {}
    sep = "\u2500" * 20
    em = {
        "title": "\U0001F50D Auditoría de wallet",
        "description": f"### {et['emoji']} {et['tipo']}\n{et['razon']}\n{sep}\n`{addr}`",
        "color": COLOR.get(et["tipo"], 0x5865F2),
        "fields": [],
        "footer": {"text": "medición propia sobre datos públicos · no incluye fees/tips"},
        "url": f"https://solscan.io/account/{addr}",
    }
    if v.get("banderas"):
        em["fields"].append({"name": "\U0001F9E0 Clasificación", "inline": False,
                             "value": "\n".join(f"\u203A {b}" for b in v["banderas"][:4])[:1024]})
    ent = sorted((r.get("recibido") or {}).items(), key=lambda kv: -kv[1]["sol"])[:4]
    if ent:
        em["fields"].append({"name": "\U0001F4B0 Le mandaron SOL", "inline": False,
                             "value": "\n".join(f"`{x['sol']:>8.2f} SOL` \u203A `{w[:16]}…`"
                                                 for w, x in ent)[:1024]})
    sal = sorted((r.get("enviado") or {}).items(), key=lambda kv: -kv[1]["sol"])[:4]
    if sal:
        em["fields"].append({"name": "\U0001F4E4 Mandó SOL a", "inline": False,
                             "value": "\n".join(f"`{x['sol']:>8.2f} SOL` \u203A `{w[:16]}…`"
                                                 for w, x in sal)[:1024]})
    if r.get("tokens"):
        lineas = []
        for t in r["tokens"][:6]:
            extra = " ⚠️ nunca lo compró" if t.get("solo_vendio") else ""
            est = "abierto" if t["abierto"] else "cerrado"
            lineas.append(f"`{t['pnl']:>+9.4f} SOL` · `{est}` · `{t['mint'][:10]}…`{extra}")
        em["fields"].append({"name": "\U0001FA99 Tokens operados", "inline": False,
                             "value": "\n".join(lineas)[:1024]})
    filas = r.get("wallets_utiles") or []
    if filas:
        lineas = []
        for f in filas[:6]:
            nums = []
            if f.get("pnl") is not None:
                nums.append(f"{f['pnl']:+.2f} SOL")
            if f.get("win_rate") is not None:
                nums.append(f"wr {f['win_rate']:.0f}%")
            marca = " ← la que pediste" if f.get("es_la_consultada") else ""
            lineas.append(f"{f['emoji']} **{f['tipo']}** `{f['wallet'][:20]}…`{marca}")
            lineas.append(f"   \u2514 {f['origen']} · {' · '.join(nums) if nums else 'sin datos'}")
        em["fields"].append({"name": "\U0001F3C6 Wallets útiles de esta red", "inline": False,
                             "value": "\n".join(lineas)[:1024]})
    if r.get("recomendacion"):
        em["fields"].append({"name": "\u2705 Qué hacer", "inline": False,
                             "value": "\n".join(f"\u203A {x}" for x in r["recomendacion"][:5])[:1024]})
    return em


def auditar_y_publicar(addr, tok, dry=False, tx=30, max_red=3):
    log(f"auditando {addr} (tx={tx}, red={max_red})…")
    r = WF.informe(addr, tx, con_cluster=True, auditar_hermanas=True, max_red=max_red)
    et = CW.etiquetar({"cerradas": r["veredicto"].get("cerradas", 0), "pnl_neto_sol": r["veredicto"]["pnl_total"],
                       "win_rate": r["veredicto"].get("win_rate"),
                       "pct_sin_compra": 0, "min_tenencia_mediano": None})
    # si la forense ya trae la fila de la wallet, usar esa etiqueta (más completa)
    for f in (r.get("wallets_utiles") or []):
        if f.get("es_la_consultada"):
            et = {"emoji": f["emoji"], "tipo": f["tipo"], "razon": f["razon"]}
            break
    em = embed_de_auditoria(addr, et, r)
    if dry:
        print(json.dumps(em, ensure_ascii=False)[:800])
        return et
    cod, resp = api(f"channels/{CANAL}/messages", "POST", {"embeds": [em]}, tok)
    log(f"  publicado en #auditoria-wallets: {cod}")
    return et


def main():
    ap = argparse.ArgumentParser(description="Canal de auditoría de wallets")
    ap.add_argument("--una-pasada", action="store_true")
    ap.add_argument("--wallet", help="auditar esta dirección y publicarla")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tx", type=int, default=30, help="transacciones a revisar (sube el costo)")
    ap.add_argument("--max-red", type=int, default=3)
    ap.add_argument("--max-por-pasada", type=int, default=1)
    ap.add_argument("--incluir-bots", action="store_true", help="para pruebas: no ignorar mensajes de bots")
    a = ap.parse_args()
    tok = _env().get("DISCORD_BOT_TOKEN")

    if a.wallet:
        auditar_y_publicar(a.wallet, tok, a.dry_run, a.tx, a.max_red)
        return 0
    if not a.una_pasada:
        ap.print_help()
        return 0

    # Si el estado quedó ilegible (por ejemplo con marcadores de conflicto de git), se arranca de
    # cero en vez de morir: antes esto rompía la tarea en cada pasada.
    try:
        est = json.load(open(ESTADO)) if os.path.exists(ESTADO) else {"procesados": []}
        if not isinstance(est, dict) or "procesados" not in est:
            est = {"procesados": []}
    except Exception:
        log("estado ilegible: arranco de cero")
        est = {"procesados": []}
    cod, msgs = api(f"channels/{CANAL}/messages?limit=30", tok=tok)
    if cod != 200:
        log(f"no pude leer el canal: {cod} {msgs}")
        return 1
    pendientes = []
    for m in reversed(msgs):
        if m["id"] in est["procesados"]:
            continue
        if m.get("author", {}).get("bot") and not a.incluir_bots:
            est["procesados"].append(m["id"])          # no auditar mensajes del propio bot
            continue
        for addr in RX.findall(m.get("content") or ""):
            if 32 <= len(addr) <= 44 and addr not in [p[0] for p in pendientes]:
                pendientes.append((addr, m["id"]))
    if not pendientes:
        log("sin direcciones nuevas en el canal")
    for addr, mid in pendientes[:a.max_por_pasada]:
        try:
            et = auditar_y_publicar(addr, tok, a.dry_run, a.tx, a.max_red)
            log(f"  {addr[:12]}… → {et['emoji']} {et['tipo']}")
        except Exception as e:
            log(f"  ! fallo con {addr[:12]}: {e}")
            api(f"channels/{CANAL}/messages", "POST",
                {"content": f"⚠️ No pude auditar `{addr}`: {str(e)[:150]}"}, tok)
        est["procesados"].append(mid)
    est["procesados"] = est["procesados"][-500:]
    if not a.dry_run:
        json.dump(est, open(ESTADO, "w"))
    log("pasada lista")
    return 0


if __name__ == "__main__":
    sys.exit(main())
