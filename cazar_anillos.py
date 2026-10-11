#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cazar ANILLOS de insiders — co-firing + union-find + clúster de fondeo + bundle de tx única.

Aplica en un solo pase las técnicas del dossier `TECNICAS_INSIDERS.md`:

  T4 **co-firing / anillos persistentes** (arXiv 2607.02795): un wallet solo puede comprar por
     casualidad; un GRUPO de wallets que vuelve a comprar junto en varios lanzamientos es un anillo.
     Se resuelve con **union-find sobre el grafo de co-ocurrencia** (pares de wallets que coinciden
     como compradores tempranos en ≥ N tokens).
  T5 **bundle de tx única**: transacción con ≥3 dueños distintos que suman el mismo mint (un solo
     pagador repartiendo compras) — firma de bundle.
  T3 **clúster de fondeo**: quién le mandó SOL a los miembros de cada anillo (mismo funder = mismo
     operador).
  T2 **tamaño**: SOL puesto por cada wallet en los tokens del anillo (para separar al que pone
     capital del que recibe regalado).

Universo: los memecoins que ya pasó el radar del proyecto (`logs/cron.log`, score ≥70).

Uso:
  python3 cazar_anillos.py --tokens 30 --primeros 40 --min-co 2
  python3 cazar_anillos.py --tokens 30 --primeros 40 --min-co 2 --auditar 8 --tx 800
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import wallet_tracker as WT          # noqa: E402
import auditor_wallets as AU         # noqa: E402
import pump_analizador as PA         # noqa: E402
import clasificar_wallets as CW      # noqa: E402
import buscar_10 as B10              # noqa: E402
import insider_token as IT           # noqa: E402
import red_wallets as RW             # noqa: E402

PAUSA = 0.03


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


class Union:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra

    def grupos(self, minimo=2):
        d = {}
        for x in list(self.p):
            d.setdefault(self.find(x), []).append(x)
        return [sorted(v) for v in d.values() if len(v) >= minimo]


def compradores_arranque(mint, cuantas, max_paginas=30):
    """Primeros compradores del token (tx más viejas alcanzables) + señales por token."""
    sigs, paginas, total = IT.sigs_mas_viejas(mint, cuantas, max_paginas)
    comp, gratis, bundles = {}, {}, []
    for s in sigs:
        if s.get("err"):
            continue
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
        pre_t, post_t = {}, {}
        for b in meta.get("preTokenBalances") or []:
            if b.get("mint") == mint and b.get("owner"):
                pre_t[b["owner"]] = pre_t.get(b["owner"], 0.0) + float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        for b in meta.get("postTokenBalances") or []:
            if b.get("mint") == mint and b.get("owner"):
                post_t[b["owner"]] = post_t.get(b["owner"], 0.0) + float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        suben = []
        for owner in set(pre_t) | set(post_t):
            if owner in AU.AU_IGNORAR or owner not in keys:
                continue
            d_tok = post_t.get(owner, 0.0) - pre_t.get(owner, 0.0)
            if d_tok <= 0:
                continue
            i = keys.index(owner)
            d_sol = (post_b[i] - pre_b[i]) / 1e9 if (i < len(pre_b) and i < len(post_b)) else 0.0
            if d_sol <= -0.2:
                e = comp.setdefault(owner, {"sol": 0.0, "tx": 0})
                e["sol"] += -d_sol
                e["tx"] += 1
                suben.append(owner)
            elif abs(d_sol) < 0.01:
                g = gratis.setdefault(owner, {"tokens": 0.0})
                g["tokens"] += d_tok
        if len(set(suben)) >= 3:                  # T5: bundle de una sola tx
            bundles.append({"tx": s["signature"], "wallets": sorted(set(suben))})
    return comp, gratis, bundles, {"tx_historial": total, "paginas": paginas,
                                   "ventana": "arranque real" if paginas < max_paginas else "ventana antigua"}


def main():
    ap = argparse.ArgumentParser(description="Cazar anillos de insiders (co-firing + union-find)")
    ap.add_argument("--tokens", type=int, default=30, help="tokens del radar a procesar")
    ap.add_argument("--primeros", type=int, default=40, help="tx más viejas a leer por token")
    ap.add_argument("--paginas", type=int, default=30, help="tope de páginas para llegar al arranque")
    ap.add_argument("--min-co", type=int, default=2, help="cuántos tokens en común para unir dos wallets")
    ap.add_argument("--min-sol", type=float, default=0.3, help="SOL mínimo puesto para contar como comprador")
    ap.add_argument("--auditar", type=int, default=0, help="auditar a fondo N wallets de los anillos")
    ap.add_argument("--tx", type=int, default=800)
    a = ap.parse_args()

    log("1/4 universo: memecoins del radar…")
    universo = B10.universo_radar(a.tokens)
    log(f"    {len(universo)} tokens")

    log(f"2/4 leyendo el arranque de cada token ({a.primeros} tx más viejas)…")
    por_token, info, wallets_sol, gratis_sol, bundles_tot = {}, {}, {}, {}, []
    for i, c in enumerate(universo, 1):
        mint, sym = c["mint"], c["symbol"]
        comp, gratis, bundles, meta = compradores_arranque(mint, a.primeros, a.paginas)
        fuertes = {w: d for w, d in comp.items() if d["sol"] >= a.min_sol}
        # participantes tempranos = compradores + los que RECIBIERON tokens sin pagar (bundle/airdrop
        # del lanzamiento). Las compras de la curva no listan el mint, pero el reparto del bundle sí:
        # por eso los receptores son la mejor huella del lanzamiento en el historial del mint.
        participantes = sorted(set(fuertes) | set(gratis))
        if participantes:
            por_token[mint] = participantes
            info[mint] = {"symbol": sym, "n_compradores": len(fuertes), "n_gratis": len(gratis),
                          **meta}
        for w, d in fuertes.items():
            wallets_sol[w] = wallets_sol.get(w, 0.0) + d["sol"]
        for w, d in gratis.items():
            gratis_sol[w] = gratis_sol.get(w, 0.0) + d["tokens"]
        for b in bundles:
            bundles_tot.append({**b, "symbol": sym, "mint": mint})
        log(f"    {i}/{len(universo)} {sym:<12} compradores ≥{a.min_sol} SOL: {len(fuertes)} · "
            f"receptores sin pagar: {len(gratis)} · bundles de 1 tx: {len(bundles)} · {meta['ventana']}")
    log(f"    {len(por_token)} tokens con participantes · {len(wallets_sol)} compradores · "
        f"{len(gratis_sol)} receptores sin pagar")

    log(f"3/4 armando anillos (pares que coinciden en ≥{a.min_co} tokens)…")
    u = Union()
    co = {}
    for w in wallets_sol:
        u.find(w)
    for mint, ws in por_token.items():
        for i1 in range(len(ws)):
            for i2 in range(i1 + 1, len(ws)):
                k = (ws[i1], ws[i2])
                co[k] = co.get(k, 0) + 1
    for (w1, w2), n in co.items():
        if n >= a.min_co:
            u.union(w1, w2)
    anillos = [g for g in u.grupos(2) if len(g) <= 12]
    anillos.sort(key=lambda g: -sum(wallets_sol.get(w, 0) for w in g))
    log(f"    {len(anillos)} anillos (2-12 wallets) · {sum(len(g) for g in anillos)} wallets implicadas")

    # clúster de fondeo (T3) sobre los miembros de los anillos más pesados
    log("4/4 buscando el fondeo de los miembros de los anillos top…")
    fondos = {}
    for g in anillos[:6]:
        for w in g[:6]:
            if w in fondos:
                continue
            try:
                fondos[w] = IT.funder_de(w, 2)
            except Exception:
                fondos[w] = []

    res = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "tokens": {m: info[m] for m in info},
           "anillos": [{"wallets": g, "sol_total": round(sum(wallets_sol.get(w, 0) for w in g), 2),
                        "tokens_en_comun": [m for m, ws in por_token.items() if len(set(g) & set(ws)) >= 2]}
                       for g in anillos],
           "bundles_tx_unica": bundles_tot,
           "wallets": [{"wallet": w, "sol": round(sol, 3), "tokens": sum(1 for ws in por_token.values() if w in ws),
                        "recibido_gratis": w in gratis_sol, "fondeo": fondos.get(w, [])}
                       for w, sol in sorted(wallets_sol.items(), key=lambda kv: -kv[1])]}

    # --- ranking de los que RECIBIERON sin pagar (los candidatos a insider) + chequeo de vida ---
    if gratis_sol:
        log(f"    {len(gratis_sol)} wallets recibieron tokens sin pagar · chequeando cuáles siguen vivas…")
        gratis_orden = sorted(gratis_sol.items(), key=lambda kv: -kv[1])
        vivas = []
        for w, tok in gratis_orden[:60]:
            try:
                saldo = ((WT.rpc("getBalance", [w]) or {}).get("value") or 0) / 1e9
                sigs = WT.rpc("getSignaturesForAddress", [w, {"limit": 5}]) or []
            except Exception:
                continue
            ult = sigs[0].get("blockTime") if sigs else None
            n_tokens = sum(1 for ws in por_token.values() if w in ws)
            vivas.append({"wallet": w, "tokens_gratis": round(tok, 2), "en_tokens": n_tokens,
                          "saldo_sol": round(saldo, 3), "ultimo_mov": ult})
        vivas.sort(key=lambda x: (-x["en_tokens"], -(x["ultimo_mov"] or 0)))
        res["receptores_sin_pagar"] = vivas
        log(f"    {len(vivas)} de los {min(60, len(gratis_sol))} más grandes chequeadas"
            f" · en ≥2 tokens: {len([v for v in vivas if v['en_tokens'] >= 2])}")

    json.dump(res, open(os.path.join(AQUI, "anillos_insiders.json"), "w"), ensure_ascii=False, indent=1, default=str)

    # --- auditoría a fondo de los receptores más prometedores ---
    if a.auditar and res.get("receptores_sin_pagar"):
        log(f"    auditando a fondo los {a.auditar} receptores más prometedores ({a.tx} tx)…")
        fichas = []
        for v in res["receptores_sin_pagar"][:a.auditar]:
            try:
                m = AU.auditar_wallet(v["wallet"], min(a.tx, 1000))
            except Exception as e:
                log(f"      ! {v['wallet'][:10]}: {e}")
                m = None
            et = CW.etiquetar(m)
            filas = {**v, "etiqueta": et, "cerradas": (m or {}).get("cerradas"),
                     "pnl": (m or {}).get("pnl_neto_sol"), "win_rate": (m or {}).get("win_rate"),
                     "hold": (m or {}).get("min_tenencia_mediano"),
                     "sin_compra": (m or {}).get("pct_sin_compra"),
                     "fondeo": [(f.get("wallet") or "") for f in ((m or {}).get("fondeo_principal") or [])][:3]}
            fichas.append(filas)
            log(f"      {et['emoji']} {et['tipo']:<9} {v['wallet'][:12]}… gratis en {v['en_tokens']} tokens · "
                f"cerradas {filas['cerradas']} · pnl {filas['pnl']} · wr {filas['win_rate']}% · "
                f"sin compra {filas['sin_compra']}%")
        res["insiders_auditados"] = fichas
        json.dump(res, open(os.path.join(AQUI, "anillos_insiders.json"), "w"), ensure_ascii=False, indent=1, default=str)

    print(f"\n=== ANILLOS DE INSIDERS ({len(anillos)}) ===")
    for g in anillos[:12]:
        sol = sum(wallets_sol.get(w, 0) for w in g)
        comunes = [info[m]["symbol"] for m, ws in por_token.items() if len(set(g) & set(ws)) >= 2]
        print(f"\n  🕸️ {len(g)} wallets · {sol:.2f} SOL puestos en total · tokens en común: {', '.join(comunes[:8])}")
        for w in g[:8]:
            print(f"      {w}   ({wallets_sol.get(w, 0):.2f} SOL)")
    print(f"\n=== BUNDLES DE TX ÚNICA ({len(bundles_tot)}) ===")
    for b in bundles_tot[:8]:
        print(f"  {b['symbol']:<12} {len(b['wallets'])} wallets en una sola tx · {b['tx'][:20]}…")
    print("\n=== WALLETS QUE RECIBIERON TOKENS SIN PAGAR ===")
    for w in list(gratis_sol)[:10]:
        print("  ", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
