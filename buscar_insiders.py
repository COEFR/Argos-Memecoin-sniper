#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Buscar wallets con INFORMACIÓN PRIVILEGIADA (insiders), no buenos traders.

Por qué: a los que ganan copiando no se les puede seguir (son bots de 1-9 minutos), pero a un insider
SÍ se le puede sacar provecho de otra forma: **saber cuándo suelta**. Si una wallet con info
privilegiada vende, eso es una señal de salida para el resto.

Qué busca (señales de la investigación del proyecto):
  · **entró en el arranque**: compró en los primeros segundos del pool/curva (info de primera mano)
  · **recibió tokens sin comprar** (bundle/airdrop = acceso al supply sin riesgo)
  · **el creador del token la fondeó** (vínculo directo con el dev)
  · win rate muy alto (>85 %) sobre muestra decente

Cómo:
  1. Universo: memecoins del radar (score ≥70, con volumen y liquidez).
  2. Del pool se leen las tx del ARRANQUE: quién compró, cuánto puso y en qué segundo entró; y quién
     recibió tokens sin poner SOL.
  3. Los candidatos se auditan a fondo (auditar_wallet) para ver % sin compra, win rate, hold y PnL.
  4. A los mejores se les cruza el **creador del token** contra su fondeo (vínculo dev).

Uso:
  python3 buscar_insiders.py --tokens 24 --arranque 120 --auditar 12 --tx 800
  python3 buscar_insiders.py --creadores      # además cruza creador ↔ fondeo (más lento)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT             # noqa: E402
import auditor_wallets as AU            # noqa: E402
import pump_analizador as PA            # noqa: E402
import clasificar_wallets as CW         # noqa: E402
import buscar_10 as B10                 # noqa: E402
import red_wallets as RW                # noqa: E402

PAUSA = 0.04


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def leer_arranque(pool, mint, cuantas):
    """Lee las PRIMERAS tx del token y devuelve: compradores (con sol y segundo de entrada),
    receptores sin pagar, y el ts del primer tx.

    Se leen las tx del **mint**, no las del pool: las primeras tx del mint son el lanzamiento
    (compras en la curva + reparto del bundle), mientras que el pool que devuelve DexScreener es el
    de PumpSwap, que arranca recién en la graduación — leerlo a él da "primeros post-graduación",
    no el arranque real.
    """
    comp, gratis, primero = {}, {}, None
    sigs = B10._sigs_del_pool(mint, cuantas, "viejas")
    for s in sigs:
        if s.get("err"):
            continue
        ts = s.get("blockTime") or 0
        if ts and (primero is None or ts < primero):
            primero = ts
        try:
            tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                             "encoding": "jsonParsed"}])
        except Exception:
            tx = None
        time.sleep(PAUSA)
        if not tx or not tx.get("meta") or tx["meta"].get("err"):
            continue
        meta = tx["meta"]
        try:
            keys = WT.cuentas_tx(tx)
        except Exception:
            continue
        pre_b, post_b = meta.get("preBalances") or [], meta.get("postBalances") or []

        def por_dueno(lista):
            d = {}
            for b in lista or []:
                if b.get("mint") == mint and b.get("owner"):
                    d[b["owner"]] = d.get(b["owner"], 0.0) + float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            return d

        pre_t, post_t = por_dueno(meta.get("preTokenBalances")), por_dueno(meta.get("postTokenBalances"))
        for owner in set(pre_t) | set(post_t):
            if owner in AU.AU_IGNORAR or owner not in keys:
                continue
            d_tok = post_t.get(owner, 0.0) - pre_t.get(owner, 0.0)
            if d_tok <= 0:
                continue
            i = keys.index(owner)
            if i >= len(pre_b) or i >= len(post_b):
                continue
            d_sol = (post_b[i] - pre_b[i]) / 1e9
            if d_sol <= -0.2:                       # compró
                e = comp.setdefault(owner, {"sol": 0.0, "ts": ts})
                e["sol"] += -d_sol
                e["ts"] = min(e["ts"] or ts, ts)
            elif abs(d_sol) < 0.01:                 # recibió tokens sin pagar
                e = gratis.setdefault(owner, {"tokens": 0.0})
                e["tokens"] += d_tok
    return comp, gratis, primero


def main():
    ap = argparse.ArgumentParser(description="Buscar insiders / wallets con info privilegiada")
    ap.add_argument("--tokens", type=int, default=24, help="memecoins del radar a revisar")
    ap.add_argument("--por-pool", type=int, default=150, help="tx del arranque a leer por token")
    ap.add_argument("--arranque", type=int, default=120, help="segundos para considerar 'entró en el arranque'")
    ap.add_argument("--auditar", type=int, default=12, help="cuántas candidatas auditar a fondo")
    ap.add_argument("--tx", type=int, default=800, help="transacciones por wallet auditada")
    ap.add_argument("--creadores", action="store_true", help="cruzar el creador del token con el fondeo")
    a = ap.parse_args()

    log("1/4 universo: memecoins del radar con volumen y liquidez…")
    universo = B10.universo_radar(a.tokens)
    log(f"    {len(universo)} tokens")
    if not universo:
        log("sin universo")
        return 1

    log(f"2/4 leyendo el arranque de cada pool ({a.por_pool} tx)…")
    cuenta, creadores = {}, {}
    for i, c in enumerate(universo, 1):
        mint, pool, sym = c["mint"], c["_pool"], c["symbol"]
        rc = PA.rugcheck(mint) or {}
        creadores[mint] = rc.get("creator")
        try:
            supply = float((rc.get("token") or {}).get("supply") or 0)
        except Exception:
            supply = 0.0
        comp, gratis, primero = leer_arranque(pool, mint, a.por_pool)
        for w, d in comp.items():
            entra = (d["ts"] - primero) if (primero and d["ts"]) else None
            e = cuenta.setdefault(w, {"arranques": [], "gratis": [], "sol": 0.0, "tokens": [],
                                      "gratis_pct": 0.0})
            e["sol"] += d["sol"]
            e["tokens"].append(sym)
            if entra is not None and 0 <= entra <= a.arranque:
                e["arranques"].append(sym)
        for w, d in gratis.items():
            pct = (d["tokens"] / supply * 100) if supply else 0.0
            if pct < 0.05:                     # migajas (airdrops de spam): no es un allocation
                continue
            e = cuenta.setdefault(w, {"arranques": [], "gratis": [], "sol": 0.0, "tokens": [],
                                      "gratis_pct": 0.0})
            e["gratis"].append(f"{sym} {pct:.2f}%")
            e["gratis_pct"] += pct
            e["tokens"].append(sym)
        log(f"    {i}/{len(universo)} {sym:<12} compradores {len(comp)} · "
            f"gratis ≥0,05% del supply {len([1 for w, d in gratis.items() if supply and d['tokens']/supply*100 >= 0.05])} · "
            f"acumulado {len(cuenta)} wallets")
    orden = sorted(cuenta.items(), key=lambda kv: (-(kv[1]["gratis_pct"] * 2 + len(kv[1]["arranques"]) * 2),
                                                   -kv[1]["sol"]))
    log(f"    {len(orden)} wallets con alguna señal de info privilegiada")
    if not orden:
        return 1

    log(f"3/4 auditando las {a.auditar} primeras ({a.tx} tx cada una)…")
    filas = []
    for i, (w, d) in enumerate(orden[:a.auditar], 1):
        try:
            m = AU.auditar_wallet(w, min(a.tx, 1000))
        except Exception as e:
            log(f"  ! error {w[:10]}: {e}")
            m = None
        et = CW.etiquetar(m)
        fondeo = [f.get("wallet") for f in ((m or {}).get("fondeo_principal") or [])]
        vinculo = []
        if a.creadores and m:
            for mint, cre in creadores.items():
                if cre and cre in fondeo:
                    vinculo.append(mint[:8])
        nota = 0.0
        nota += 2.0 * d.get("gratis_pct", 0.0)               # % del supply recibido sin pagar
        nota += 2.0 * len(d["arranques"])                    # entradas en el arranque
        if (m or {}).get("pct_sin_compra", 0) >= 40:
            nota += 2.0
        if (m or {}).get("win_rate") and m["win_rate"] >= 90 and (m or {}).get("cerradas", 0) >= 10:
            nota += 1.5
        if vinculo:
            nota += 2.0
        filas.append({"wallet": w, "señal": round(min(nota, 10.0), 1), "etiqueta": et,
                      "arranques": d["arranques"], "gratis": d["gratis"],
                      "gratis_pct": round(d.get("gratis_pct", 0.0), 2), "tokens": d["tokens"][:6],
                      "sol_puesto": round(d["sol"], 2),
                      "cerradas": (m or {}).get("cerradas"), "pnl": (m or {}).get("pnl_neto_sol"),
                      "win_rate": (m or {}).get("win_rate"), "hold": (m or {}).get("min_tenencia_mediano"),
                      "sin_compra": (m or {}).get("pct_sin_compra"), "vinculo_dev": vinculo,
                      "fondeo": fondeo[:3]})
        log(f"  {i}/{len(orden[:a.auditar])} {et['emoji']} {et['tipo']:<9} señal {filas[-1]['señal']}/10 "
            f"· arranques {len(d['arranques'])} · gratis {len(d['gratis'])} · "
            f"cerradas {filas[-1]['cerradas']} · wr {filas[-1]['win_rate']}% · pnl {filas[-1]['pnl']}")
    filas.sort(key=lambda f: -f["señal"])
    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tokens": [c["symbol"] for c in universo], "wallets": filas},
              open(os.path.join(AQUI, "insiders.json"), "w"), ensure_ascii=False, indent=2, default=str)
    log("guardado en insiders.json")

    print("\n=== WALLETS CON SEÑALES DE INFO PRIVILEGIADA ===")
    for f in filas:
        print(f"\n{f['señal']:>4}/10  {f['etiqueta']['emoji']} {f['etiqueta']['tipo']}  {f['wallet']}")
        print(f"      entró en el arranque de: {', '.join(f['arranques']) or '-'} · "
              f"recibió sin pagar: {', '.join(f['gratis']) or '-'} · puso {f['sol_puesto']} SOL")
        print(f"      {f['cerradas']} cerradas · PnL {f['pnl']} SOL · wr {f['win_rate']}% · "
              f"hold {f['hold']} min · sin compra {f['sin_compra']}%")
        if f["vinculo_dev"]:
            print(f"      🔗 el creador del token la fondeó: {', '.join(f['vinculo_dev'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
