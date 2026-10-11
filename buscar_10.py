#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Buscar wallets 10/10 por COMPRADORES REALES de pool (el método que sí funciona).

Por qué este camino: descubrir wallets por "top holders" trae insiders con tokens regalados, y
descubrir por "wallets conectadas" trae la infraestructura de la propia wallet (pools, PDAs, bots).
Lo que queda son los que COMPRARON: se leen las transacciones del pool de tokens que corrieron y se
extrae quién puso su propio SOL. Después se audita a cada uno y se puntúa 0-10 con los filtros del
proyecto (mismo criterio que `red_wallets.py`).

Pasos:
  1. Universo: tokens recientes que ya se movieron fuerte (`auditor_wallets.tokens_ganadores`).
  2. Para cada uno: tx del pool → dueños con delta de token POSITIVO y delta de SOL NEGATIVO = compró.
  3. Cuenta en cuántos ganadores compró cada wallet y cuánto SOL puso.
  4. Audita las mejores y las puntúa 0-10.

Uso:
  python3 buscar_10.py                          # 40 tokens · 12 tx por pool · 20 wallets · 200 tx c/u
  python3 buscar_10.py --tokens 20 --auditar 10 # más rápido
  python3 buscar_10.py --cargar                 # mete al tracker sólo las de 8/10 o más
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
import red_wallets as RW                # noqa: E402

PAUSA = 0.05
MIN_SOL_COMPRA = 0.2        # menos que esto es dust de bot


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def _sigs_del_pool(pool, cuantas, ventana="nuevas"):
    """Firmas del pool. `viejas` pagina hasta el arranque (los PRIMEROS compradores, que es donde
    está el edge según la investigación: el valor está en el momento de entrada, no en la etiqueta)."""
    if ventana == "nuevas":
        return WT.rpc("getSignaturesForAddress", [pool, {"limit": cuantas}]) or []
    out, antes, pag = [], None, 0
    while True:
        p = {"limit": 1000}
        if antes:
            p["before"] = antes
        lote = WT.rpc("getSignaturesForAddress", [pool, p]) or []
        if not lote:
            break
        out = lote + out                     # del más nuevo al más viejo → se prepone
        antes = lote[-1]["signature"]
        pag += 1
        if len(lote) < 1000 or pag >= 3:
            break
        time.sleep(0.05)
    return out[:cuantas]                     # los más viejos = el arranque del pool


def compradores_del_pool(pool, mint, cuantas, ventana="nuevas"):
    """Wallets que COMPRARON el token leyendo las tx del pool (token sube + SOL baja)."""
    out = {}
    try:
        sigs = _sigs_del_pool(pool, cuantas, ventana)
    except Exception:
        return out
    for s in sigs:
        if s.get("err"):
            continue
        try:
            tx = WT.rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": WT.MAX_VERSION,
                                                            "encoding": "jsonParsed"}])
        except Exception:
            tx = None
        time.sleep(PAUSA)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        if meta.get("err"):
            continue
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
            if owner in AU.AU_IGNORAR:
                continue
            d_tok = post_t.get(owner, 0.0) - pre_t.get(owner, 0.0)
            if d_tok <= 0:                      # sólo los que SUMARON tokens
                continue
            if owner not in keys:
                continue
            i = keys.index(owner)
            if i >= len(pre_b) or i >= len(post_b):
                continue
            d_sol = (post_b[i] - pre_b[i]) / 1e9
            if d_sol > -MIN_SOL_COMPRA:          # y PAGARON SOL por ellos
                continue
            e = out.setdefault(owner, {"sol": 0.0, "veces": 0})
            e["sol"] += -d_sol
            e["veces"] += 1
    return out


def universo_radar(n, min_vol=50_000, min_liq=10_000):
    """Universo = memecoins que pasaron el radar del proyecto (score ≥70), del log del cron.

    Mejor que la lista cruda de pump.fun: esa trae stablecoins y activos tokenizados (USDF, USDP,
    DOTF…) cuyos "compradores" son bots de arbitraje, no traders de memecoins.
    El pool para leer sale de DexScreener (`pairAddress`), que anda también para tokens viejos
    (el `pool_address` de pump.fun sólo viene si el token sigue en las listas del día).
    """
    import re
    mints, vistos = [], set()
    try:
        texto = open(os.path.join(AQUI, "logs", "cron.log"), errors="replace").read()
    except Exception:
        texto = ""
    for sym, score, mint in re.findall(r"ALERTA (\S+) \(([\d.]+)\) ([1-9A-HJ-NP-Za-km-z]{32,44})", texto):
        if mint not in vistos:
            vistos.add(mint)
            mints.append((sym, mint))
    salida = []
    for sym, mint in reversed(mints):                 # de la alerta más nueva a la más vieja
        d = PA.dexscreener(mint)
        vol = ((d or {}).get("volume") or {}).get("h24") or 0
        liq = ((d or {}).get("liquidity") or {}).get("usd") or 0
        if not d or not d.get("pairAddress") or vol < min_vol or liq < min_liq:
            continue
        salida.append({"mint": mint, "symbol": sym, "_pool": d["pairAddress"], "_vol24": vol,
                       "_liq": liq, "usd_market_cap": d.get("marketCap") or 0})
        if len(salida) >= n:
            break
    return salida


def main():
    ap = argparse.ArgumentParser(description="Buscar wallets 10/10 por compradores reales de pool")
    ap.add_argument("--tokens", type=int, default=40, help="tokens que corrieron a revisar")
    ap.add_argument("--por-pool", type=int, default=12, help="tx a leer de cada pool")
    ap.add_argument("--auditar", type=int, default=20, help="cuántas wallets auditar a fondo")
    ap.add_argument("--tx", type=int, default=200, help="transacciones por wallet auditada")
    ap.add_argument("--fuente", choices=("radar", "ganadores"), default="radar",
                    help="de dónde salen los tokens: radar del proyecto (memecoins) o lista cruda de pump.fun")
    ap.add_argument("--profundizar", type=int, default=8,
                    help="cuántas de las mejores re-evaluar con historial profundo (0 = no)")
    ap.add_argument("--tx-profundo", type=int, default=1200,
                    help="transacciones por wallet en la pasada profunda (nunca <1000: la ventana corta miente)")
    ap.add_argument("--ventana", choices=("nuevas", "viejas"), default="viejas",
                    help="qué tx del pool leer: 'viejas' = el arranque (primeros compradores, donde está el edge) "
                         "· 'nuevas' = las últimas")
    ap.add_argument("--cargar", action="store_true")
    a = ap.parse_args()

    log("1/3 universo: tokens recientes que ya se movieron fuerte…")
    if a.fuente == "radar":
        ganadores = universo_radar(a.tokens)
        log(f"    {len(ganadores)} memecoins del radar (score ≥70) con volumen 24 h ≥50k y liquidez ≥10k")
    else:
        crudos = AU.tokens_ganadores(tope=max(a.tokens * 2, 40))
        ganadores = []
        for c in crudos:                      # sólo tokens con volumen y liquidez reales
            d = PA.dexscreener(c["mint"])
            vol = ((d or {}).get("volume") or {}).get("h24") or 0
            liq = ((d or {}).get("liquidity") or {}).get("usd") or 0
            if vol < 50_000 or liq < 10_000:
                continue
            c["_vol24"] = vol
            c["_liq"] = liq
            c["_pool"] = (d or {}).get("pairAddress")
            ganadores.append(c)
            if len(ganadores) >= a.tokens:
                break
        log(f"    {len(ganadores)} tokens con mcap ≥150k, volumen 24 h ≥50k y liquidez ≥10k")
    if not ganadores:
        log("no hay universo; probá subir --tokens")
        return 1

    log(f"2/3 leyendo {a.por_pool} tx del pool de cada token → compradores reales…")
    cuenta = {}
    for i, c in enumerate(ganadores, 1):
        pool = c.get("_pool") or c.get("pool_address") or c.get("bonding_curve")
        sym = c.get("symbol") or "?"
        if not pool:
            continue
        comp = compradores_del_pool(pool, c["mint"], a.por_pool, a.ventana)
        for w, d in comp.items():
            e = cuenta.setdefault(w, {"veces": 0, "sol": 0.0, "tokens": []})
            e["veces"] += 1
            e["sol"] += d["sol"]
            e["tokens"].append(sym)
        log(f"    {i}/{len(ganadores)} {sym:<12} compradores en el pool: {len(comp)} · "
            f"acumulado: {len(cuenta)} wallets")
    orden = sorted(cuenta.items(), key=lambda kv: (-kv[1]["veces"], -kv[1]["sol"]))
    log(f"    {len(orden)} wallets compraron al menos uno de los {len(ganadores)} tokens")

    cand = [(w, d) for w, d in orden if d["veces"] >= 2][:a.auditar]
    if len(cand) < a.auditar:
        cand += [(w, d) for w, d in orden if d["veces"] == 1][:a.auditar - len(cand)]
    log(f"3/3 auditando {len(cand)} wallets ({a.tx} tx cada una)…")
    filas = []
    for i, (w, d) in enumerate(cand, 1):
        try:
            m = AU.auditar_wallet(w, a.tx)
        except Exception as e:
            log(f"  ! error {w[:10]}: {e}")
            m = None
        nota, ok, no = RW.puntaje_10(m)
        filas.append({"wallet": w, "nota": nota, "aprobados": ok, "fallados": no,
                      "compro_en": d["veces"], "tokens": d["tokens"][:6], "sol_puesto": round(d["sol"], 2),
                      "cerradas": (m or {}).get("cerradas", 0), "pnl": (m or {}).get("pnl_neto_sol"),
                      "win_rate": (m or {}).get("win_rate"), "roi": (m or {}).get("roi_total"),
                      "hold": (m or {}).get("min_tenencia_mediano"),
                      "t_stat": (m or {}).get("t_stat"),
                      "concentracion": (m or {}).get("concentracion_pnl"),
                      "sin_compra": (m or {}).get("pct_sin_compra"),
                      "fondeo": (m or {}).get("fondeo_principal")})
        log(f"  {i}/{len(cand)} nota {nota}/10 · {w[:12]}… · compró en {d['veces']} de los que corrieron "
            f"· cerradas {filas[-1]['cerradas']} · pnl {filas[-1]['pnl']} SOL · wr {filas[-1]['win_rate']}%")

    filas.sort(key=lambda f: -f["nota"])

    # --- 2ª etapa: re-evaluar las mejores con historial profundo (la ventana corta miente) ---
    if a.profundizar and filas:
        import forense_profunda as FP
        import wallet_forense as WF
        top = filas[:a.profundizar]
        log(f"4/4 profundizando {len(top)} candidatas con {a.tx_profundo} tx (paginado)…")
        for i, f in enumerate(top, 1):
            w = f["wallet"]
            try:
                sigs = FP.todas_las_firmas(w, a.tx_profundo)
                cache = FP.descargar(w, sigs, os.path.join(AQUI, f"cache_tx_{w[:8]}.json"))
                d = FP.analizar(w, cache, sigs)
                ten = WF.tenencias_actuales(w)
                cer, ab = FP.posiciones(d["tokens"], ten)
                saldo = ((WT.rpc("getBalance", [w]) or {}).get("value") or 0) / 1e9
                met = FP.metricas(cer, ab, saldo)
                n2, ok2, no2 = RW.puntaje_10(met)
                f["nota_rapida"] = f["nota"]
                f["nota"] = n2
                f["aprobados"], f["fallados"] = ok2, no2
                f.update({"tx_leidas": len(cache), "cerradas": met.get("cerradas"),
                          "pnl": met.get("pnl_neto_sol"), "win_rate": met.get("win_rate"),
                          "roi": met.get("roi_total"), "hold": met.get("min_tenencia_mediano"),
                          "t_stat": met.get("t_stat"), "concentracion": met.get("concentracion_pnl"),
                          "sin_compra": met.get("pct_sin_compra")})
                log(f"  {i}/{len(top)} {w[:12]}… rápida {f['nota_rapida']}/10 → profunda {n2}/10 "
                    f"({f['tx_leidas']} tx · {f['cerradas']} cerradas · pnl {f['pnl']} SOL · "
                    f"t {f['t_stat']})")
            except Exception as e:
                log(f"  ! error profundizando {w[:12]}: {e}")
        filas.sort(key=lambda f: -f["nota"])
        for f in filas:
            f.setdefault("nota_rapida", None)
            f.setdefault("tx_leidas", a.tx)

    salida = os.path.join(AQUI, "buscar10.json")
    json.dump({"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tokens": [c.get("symbol") for c in ganadores], "wallets": filas},
              open(salida, "w"), ensure_ascii=False, indent=2, default=str)
    log(f"guardado en {salida}")

    print(f"\n=== CANDIDATAS POR COMPRADORES REALES (puntuadas sobre 10) ===")
    for f in filas:
        v = "SEGUIR ⭐" if f["nota"] >= 8 else ("VIGILAR 🔎" if f["nota"] >= 5 else "EVITAR 🚫")
        rapida = f.get("nota_rapida")
        extra = (f" (rápida {rapida}/10 → profunda {f['nota']}/10 con {f.get('tx_leidas')} tx)"
                 if rapida is not None and rapida != f["nota"] else "")
        print(f"\n{f['nota']:>4}/10  {v}  {f['wallet']}{extra}")
        print(f"      compró en {f['compro_en']}/{len(ganadores)} tokens que corrieron ({', '.join(f['tokens'])}) "
              f"· puso {f['sol_puesto']} SOL")
        print(f"      {f['cerradas']} cerradas · PnL {f['pnl']} SOL · wr {f['win_rate']}% · ROI {f['roi']}% · "
              f"hold {f['hold']} min · t {f['t_stat']}")
        if f["aprobados"]:
            print(f"      ✅ {' · '.join(f['aprobados'])}")
        if f["fallados"]:
            print(f"      ❌ {' · '.join(f['fallados'])}")

    if a.cargar:
        buenas = [f for f in filas if f["nota"] >= 8 and f.get("tx_leidas", 0) >= 1000]
        for f in buenas[:5]:
            etq = f"⭐CANDIDATA {f['nota']:.0f}10 compra-en-{f['compro_en']} pnl{f['pnl']:+.1f}SOL wr{f['win_rate']:.0f}%"
            os.system(f'cd {AQUI} && python3 wallet_tracker.py --agregar {f["wallet"]} "{etq}"')
            log(f"cargada al tracker: {etq} · {f['wallet']}")
        if not buenas:
            log("ninguna llegó a 8/10: no se cargó nada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
