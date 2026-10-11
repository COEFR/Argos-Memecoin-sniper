#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLÚSTER ON-CHAIN — ver las "calls" de los grupos SIN estar en el grupo.

La idea: la call no se ve en el mensaje, se ve en la CADENA. Los miembros de un grupo compran el
mismo token casi al mismo tiempo; si identificamos a los miembros (sus wallets), entonces **cuando
varias de esas wallets compran juntas, eso ES la call** — y la vemos antes de que el precio reaccione.

Cómo se arma la lista de miembros (sin entrar a ningún grupo):
  1. **Wallets que repiten** en nuestras propias ráfagas (el registro guarda la lista de cada call):
     una wallet que aparece en 2+ calls distintas es candidata a miembro.
  2. **Listas públicas de KOLs**: el directorio de `madeonsol.com/kol-tracker` (direcciones en el HTML,
     sin key ni login) y las candidatas ya verificadas por nosotros (`publicas_vivas.json`).
  3. Las wallets que seguimos en el tracker.

Después, para cada token joven: se leen sus compras y se cruzan con la lista de miembros. Si compran
varias wallets de la lista → **CLÚSTER** y se avisa con el roster (quién compró, en qué segundo y por
cuánto). No hace falta estar en el grupo: el grupo se delata solo en la cadena.

Uso:
  python3 grupos_onchain.py --armar          # rehace la lista de miembros (cron cada 60 min)
  python3 grupos_onchain.py --una-pasada     # revisa los tokens jóvenes y avisa clústers (cron 15 min)
  python3 grupos_onchain.py --mirar <mint>   # mirar un token concreto
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
import wallet_tracker as WT          # noqa: E402
import auditor_wallets as AU         # noqa: E402
import pump_analizador as PA         # noqa: E402
import calls_detector as CD          # noqa: E402
import registro_calls as RC          # noqa: E402

MIEMBROS = os.path.join(AQUI, "miembros.json")
UA = "Mozilla/5.0 (compatible; hermes-cluster/1.0)"
RX = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
MIN_SOL = 0.2
VENTANA = 180         # segundos para considerar que compraron "juntos"
MIN_CLUSTER = 2       # miembros conocidos que tienen que coincidir para avisar


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def _usuarios_solana_ignorar():
    return AU.AU_IGNORAR


def arm(KOL=True):
    """Arma la lista de miembros y la guarda con su origen."""
    miembros = {}

    def sumar(w, origen):
        if not w or w in _usuarios_solana_ignorar():
            return
        e = miembros.setdefault(w, {"origenes": [], "calls": 0})
        if origen not in e["origenes"]:
            e["origenes"].append(origen)

    # 1) wallets que REPITEN en nuestras ráfagas (miembro detectado por la cadena)
    d = RC._leer()
    for x in d:
        for w in (x.get("wallets") or []):
            sumar(w, "repitio_en_rafagas")
            if w in miembros:
                miembros[w]["calls"] = miembros[w].get("calls", 0) + 1

    # 2) listas públicas
    for archivo, campo in (("publicas_vivas.json", None), ("candidatas_publicas.json", None),
                           ("wallets.json", "wallets")):
        try:
            j = json.load(open(os.path.join(AQUI, archivo)))
        except Exception:
            continue
        if isinstance(j, dict) and campo:
            for x in j.get(campo) or []:
                sumar(x.get("address") or x.get("wallet"), "lista_publica")
        elif isinstance(j, list):
            for x in j:
                sumar(x.get("wallet") or x.get("wallet_address"), "lista_publica")

    # OJO (pedido del usuario 08/10): NO se usan listas públicas de KOLs ni de "smart money".
    # Seguir wallets que todo el mundo ya conoce es comprar donde ellos venden (ser liquidez de
    # salida). Los miembros se identifican SÓLO por comportamiento en la cadena (co-compras repetidas),
    # que es lo que hace `cohortes.py`.
    ruta_kq = os.path.join(AQUI, "kols_publicos.json") if False else None
    try:
        if not os.path.exists(ruta_kq):
            req = urllib.request.Request(
                "https://raw.githubusercontent.com/nirholas/kol-quest/main/site/data/solwallets.json",
                headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                datos = json.loads(r.read().decode())
            lista = []
            for clave in ("walletsBySmartMoney", "walletsByKOL"):
                for x in ((datos.get("interceptorRaw") or {}).get(clave) or []):
                    w = x.get("wallet_address") or x.get("address")
                    if w:
                        lista.append({"wallet": w, "tipo": clave, "twitter": x.get("twitter_username")})
            json.dump(lista, open(ruta_kq, "w"), ensure_ascii=False)
        for x in json.load(open(ruta_kq)):
            sumar(x.get("wallet"), "kol_quest")
    except Exception as e:
        log(f"  ! no pude usar la lista de kol-quest: {e}")

    # 4) KOLs públicos (madeonsol: las direcciones vienen en el HTML, sin key ni login)
    kol_n = 0
    if KOL:
        for url in ("https://madeonsol.com/kol-tracker",):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=25) as r:
                    html = r.read().decode("utf-8", "replace")
                for w in set(RX.findall(html)):
                    sumar(w, "kol_publico")
                    kol_n += 1
            except Exception as e:
                log(f"  ! no pude bajar la lista de KOLs: {e}")

    # los que repitieron varias veces pesan más: se marca el nivel
    for w, e in miembros.items():
        e["nivel"] = ("miembro_fuerte" if e.get("calls", 0) >= 2 else
                      ("kol" if "kol_publico" in e["origenes"] else "conocida"))
    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "total": len(miembros), "kol_bajados": kol_n, "miembros": miembros},
              open(MIEMBROS, "w"), ensure_ascii=False, indent=1)
    log(f"  lista de miembros: {len(miembros)} wallets ({kol_n} desde el directorio de KOLs)")
    return miembros


def cargar_miembros():
    try:
        return json.load(open(MIEMBROS)).get("miembros") or {}
    except Exception:
        return arm()


def compras_de(mint, limite=60):
    """Compradores recientes del token (token sube + SOL baja). Devuelve {wallet: {sol, ts, slot}}."""
    sigs = WT.rpc("getSignaturesForAddress", [mint, {"limit": limite}]) or []
    out = {}
    for s in sigs:
        if s.get("err"):
            continue
        try:
            tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                             "encoding": "jsonParsed"}])
        except Exception:
            tx = None
        time.sleep(0.03)
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
            if owner in _usuarios_solana_ignorar() or owner not in keys:
                continue
            d_tok = post_t.get(owner, 0.0) - pre_t.get(owner, 0.0)
            if d_tok <= 0:
                continue
            i = keys.index(owner)
            d_sol = (post_b[i] - pre_b[i]) / 1e9 if (i < len(pre_b) and i < len(post_b)) else 0.0
            if d_sol <= -MIN_SOL:
                e = out.setdefault(owner, {"sol": 0.0, "ts": s.get("blockTime") or 0, "slot": tx.get("slot")})
                e["sol"] += -d_sol
                e["ts"] = max(e["ts"], s.get("blockTime") or 0)
    return out


def clúster(mint, miembros=None):
    """¿Compraron miembros del grupo en este token? Devuelve el roster o None."""
    miembros = miembros if miembros is not None else cargar_miembros()
    compras = compras_de(mint)
    roster = [(w, d, miembros.get(w)) for w, d in compras.items() if w in miembros]
    if not roster:
        return None
    roster.sort(key=lambda x: -(x[1]["sol"] or 0))
    ts = [d["ts"] for _w, d, _m in roster if d.get("ts")]
    dentro = len([1 for _w, d, _m in roster if ts and (max(ts) - d["ts"]) <= VENTANA])
    fuertes = len([1 for _w, _d, m in roster if (m or {}).get("nivel") == "miembro_fuerte"])
    kols = len([1 for _w, _d, m in roster if (m or {}).get("nivel") == "kol"])
    return {"mint": mint, "n_miembros": len(roster), "dentro_de_ventana": dentro,
            "fuertes": fuertes, "kols": kols, "sol_total": round(sum(d["sol"] for _w, d, _m in roster), 2),
            "roster": [{"wallet": w, "sol": round(d["sol"], 3), "nivel": (m or {}).get("nivel"),
                        "orig": (m or {}).get("origenes")} for w, d, m in roster[:8]]}


def revisar_tokens(max_tokens=6, avisar=True, dry=False):
    """Revisa los tokens jóvenes y avisa cuando hay clúster de miembros."""
    try:
        jovenes = json.load(open(os.path.join(AQUI, "tokens_jovenes.json")))
    except Exception:
        jovenes = []
    if not jovenes:
        log("  sin tokens jóvenes en la lista (el radar la arma cada 10 min)")
        return []
    miembros = cargar_miembros()
    log(f"  revisando {min(max_tokens, len(jovenes))} tokens jóvenes contra {len(miembros)} miembros")
    hallados = []
    for t in jovenes[:max_tokens]:
        c = clúster(t["mint"], miembros)
        if not c or c["dentro_de_ventana"] < MIN_CLUSTER:
            continue
        try:
            ev = PA.analizar(PA.buscar_coin(t["mint"]), pedir=True)
        except Exception:
            continue
        ctx = ev.get("contexto") or {}
        nota, motivos, etq = CD.puntuar({"n_wallets": c["n_miembros"], "sol": c["sol_total"],
                                        "segundos": 0, "slots_iguales": 1, "frescas": 0,
                                        "conocidas": c["n_miembros"], "compras_en_ventana": c["n_miembros"]}, ctx)
        roster_txt = "\n".join(f"\u203A `{r['wallet'][:12]}…` {r['sol']} SOL · {r['nivel']}"
                               for r in c["roster"][:6])
        ev["call"] = {"nota": nota, "motivos": motivos + [f"**+0,75×{c['n_miembros']} miembros conocidos** "
                                                          f"· {c['kols']} KOLs y {c['fuertes']} miembros fuertes"],
                      "etq": etq, "n_wallets": c["n_miembros"], "sol": c["sol_total"],
                      "segundos": 0, "slots_iguales": 1, "frescas": 0, "conocidas": c["n_miembros"],
                      "compras_en_ventana": c["n_miembros"], "roster": c["roster"],
                      "hora": datetime.now(timezone.utc).strftime("%H:%M") + " UTC",
                      "ts_iso": datetime.now(timezone.utc).isoformat(),
                      "wallets": [r["wallet"] for r in c["roster"]], "cluster": True}
        ev["por_rafaga"] = True
        ev["cluster_txt"] = roster_txt
        hallados.append(ev)
        log(f"  🏛️ CLÚSTER en {ev['simbolo']}: {c['n_miembros']} miembros ({c['kols']} KOLs) · "
            f"{c['sol_total']} SOL · puntaje {nota}")
        if avisar and not dry:
            try:
                RC.registrar(ev["call"], ev)
            except Exception:
                pass
            PA.alertar_token(ev, "🏛️ CLÚSTER · LOS MIEMBROS ESTÁN COMPRANDO")
    return hallados


def main():
    ap = argparse.ArgumentParser(description="Clúster on-chain: ver las calls sin estar en el grupo")
    ap.add_argument("--armar", action="store_true")
    ap.add_argument("--una-pasada", action="store_true")
    ap.add_argument("--mirar", help="mint a revisar")
    ap.add_argument("--max", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.armar:
        arm()
        return 0
    if a.mirar:
        print(json.dumps(clúster(a.mirar) or {"clúster": None}, ensure_ascii=False, indent=1))
        return 0
    if a.una_pasada:
        revisar_tokens(a.max, avisar=not a.dry_run, dry=a.dry_run)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
