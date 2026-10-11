#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cazar wallets con INFO PRIVILEGIADA — método propio, sin listas públicas.

Idea: el insider no se delata comprando (las compras de la curva no quedan en el historial del mint),
se delata **recibiendo o entrando baratísimo en el lanzamiento y cobrando en el pico**. Entonces:

  1. Universo: los memecoins que pasó **nuestro radar** y que **despegaron** (mcap/volumen alto hoy,
     según DexScreener). Nada de listas de terceros.
  2. Para cada token: se lee el historial MÁS VIEJO del mint (ahí están las creaciones de cuentas y
     los repartos del lanzamiento) y se separa por wallet: cuántos tokens recibió SIN pagar, cuánto
     SOL puso y en qué momento entró.
  3. Candidatos: quien recibió/entró barato en el lanzamiento, ordenado por tamaño recibido.
  4. Verificación on-chain (motor propio): se audita cada candidato y se mira **su resultado en ESE
     token** (sol_in vs sol_out del mint): si entró casi gratis y cobró fuerte, es insider con
     información privilegiada. Se revisa también si todavía tiene la posición.
  5. Red: quién lo fondeó y a quién le mandó la plata (clúster del operador).

Uso:
  python3 cazar_privilegiados.py --tokens 6 --tx 400 --min-pnl 5
  python3 cazar_privilegiados.py --tokens 6 --tx 800 --min-pnl 10 --profundizar 4
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
import insider_token as IT           # noqa: E402
import buscar_10 as B10              # noqa: E402

PAUSA = 0.03


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {m}", flush=True)


def tokens_que_despegaron(max_tokens=8, min_mcap=300_000, min_vol=50_000):
    """Memecoins del RADAR del proyecto que hoy están grandes (mcap y volumen reales)."""
    out = []
    for c in B10.universo_radar(30):            # ya filtra volumen 24h ≥50k y liquidez ≥10k
        d = PA.dexscreener(c["mint"]) or {}
        mc = d.get("marketCap") or d.get("fdv") or 0
        vol = (d.get("volume") or {}).get("h24") or 0
        creado = (d.get("pairCreatedAt") or 0) / 1000
        edad_h = (time.time() - creado) / 3600 if creado else None
        if mc < min_mcap or vol < min_vol:
            continue
        out.append({"mint": c["mint"], "symbol": c["symbol"], "mcap": mc, "vol24": vol,
                    "edad_h": round(edad_h, 1) if edad_h else None,
                    "precio": d.get("priceUsd"), "cambio24": (d.get("priceChange") or {}).get("h24")})
    out.sort(key=lambda x: -x["mcap"])
    return out[:max_tokens]


def arranque_del_mint(mint, cuantas=250, max_paginas=40):
    """Historial más viejo del mint: por wallet, tokens recibidos sin pagar / SOL puesto / primera vez."""
    sigs, paginas, total = IT.sigs_mas_viejas(mint, cuantas, max_paginas)
    libre, pago, primera = {}, {}, {}
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
        ts = s.get("blockTime") or 0
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
            if abs(d_tok) <= 0:
                continue
            i = keys.index(owner)
            d_sol = (post_b[i] - pre_b[i]) / 1e9 if (i < len(pre_b) and i < len(post_b)) else 0.0
            if owner not in primera:
                primera[owner] = ts
            if d_tok > 0 and d_sol <= -0.2:
                pago[owner] = pago.get(owner, 0.0) + (-d_sol)
            elif d_tok > 0 and abs(d_sol) < 0.01:
                libre[owner] = libre.get(owner, 0.0) + d_tok
    return libre, pago, primera, {"tx_total": total, "paginas": paginas}


def resultado_en_token(wallet, mint, max_tx=400):
    """Audita el wallet y devuelve su resultado EN ESE token + datos de red."""
    m = AU.auditar_wallet(wallet, min(max_tx, 1000))
    if not m:
        return None
    det = None
    for f in (m.get("detalle_cerradas") or []):
        if f.get("mint") == mint:
            det = f
            break
    ten = WT.rpc("getTokenAccountsByOwner", [wallet, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                                             {"encoding": "jsonParsed"}])
    tiene = 0.0
    for a in ((ten or {}).get("value") or []):
        try:
            i = a["account"]["data"]["parsed"]["info"]
            if i["mint"] == mint:
                tiene = float(i["tokenAmount"]["uiAmount"] or 0)
        except Exception:
            continue
    return {"wallet": wallet, "cerradas": m.get("cerradas"), "pnl_total": m.get("pnl_neto_sol"),
            "win_rate": m.get("win_rate"), "sin_compra": m.get("pct_sin_compra"),
            "hold": m.get("min_tenencia_mediano"), "etiqueta": CW.etiquetar(m),
            "token_pnl": (det or {}).get("pnl"), "token_in": (det or {}).get("sol_in"),
            "token_out": (det or {}).get("sol_out"), "token_roi": (det or {}).get("roi"),
            "tiene_ahora": tiene, "fondeo": [(f.get("wallet") or "") for f in (m.get("fondeo_principal") or [])][:3]}


def main():
    ap = argparse.ArgumentParser(description="Cazar wallets con info privilegiada (método propio)")
    ap.add_argument("--tokens", type=int, default=6)
    ap.add_argument("--tx", type=int, default=400, help="tx por wallet auditada")
    ap.add_argument("--arranque", type=int, default=250, help="tx más viejas del mint a leer")
    ap.add_argument("--candidatos", type=int, default=6, help="cuántos candidatos auditar por token")
    ap.add_argument("--min-pnl", type=float, default=3.0, help="SOL de ganancia en ESE token para considerarlo insider")
    ap.add_argument("--profundizar", type=int, default=0, help="re-auditar los mejores con más tx")
    a = ap.parse_args()

    log("1/4 tokens que despegaron (del radar propio, con mcap y volumen reales)…")
    toks = tokens_que_despegaron(a.tokens)
    for t in toks:
        log(f"    {t['symbol']:<12} mcap {t['mcap']:>12.0f} · vol24 {t['vol24']:>10.0f} · edad {t['edad_h']} h")

    log("2/4 leyendo el arranque de cada token y armando candidatos…")
    cands, ctx = [], {}
    for t in toks:
        libre, pago, primera, meta = arranque_del_mint(t["mint"], a.arranque)
        ctx[t["mint"]] = {"symbol": t["symbol"], "mcap": t["mcap"], "n_libre": len(libre),
                          "n_pago": len(pago), **meta}
        log(f"    {t['symbol']:<12} recibieron gratis: {len(libre):>4} · compraron: {len(pago):>4} · "
            f"historial {meta['tx_total']} tx")
        orden = sorted(libre.items(), key=lambda kv: -kv[1])[:a.candidatos]
        for w, tok in orden:
            cands.append({"wallet": w, "mint": t["mint"], "symbol": t["symbol"], "recibido": tok,
                          "pago": pago.get(w, 0.0), "primera": primera.get(w)})

    log(f"3/4 auditando {len(cands)} candidatos ({a.tx} tx cada uno)…")
    fichas = []
    for i, c in enumerate(cands, 1):
        try:
            r = resultado_en_token(c["wallet"], c["mint"], a.tx)
        except Exception as e:
            log(f"    ! {c['wallet'][:10]}: {e}")
            r = None
        if not r:
            continue
        f = {**c, **r}
        fichas.append(f)
        log(f"    {i}/{len(cands)} {c['symbol']:<10} {c['wallet'][:12]}… recibió {c['recibido']:.0f} · "
            f"en el token: in {r['token_in']} out {r['token_out']} pnl {r['token_pnl']} · "
            f"tiene ahora {r['tiene_ahora']:.0f}")

    # insiders = ganaron fuerte EN ESE token (entraron gratis/barato y cobraron)
    insiders = [f for f in fichas if (f.get("token_pnl") or 0) >= a.min_pnl]
    insiders.sort(key=lambda f: -(f.get("token_pnl") or 0))

    if a.profundizar and insiders:
        log(f"4/4 profundizando los {a.profundizar} mejores con {a.tx*2} tx…")
        for f in insiders[:a.profundizar]:
            try:
                r = resultado_en_token(f["wallet"], f["mint"], a.tx * 2)
                if r:
                    f.update(r)
            except Exception:
                pass

    res = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "tokens": ctx, "insiders": insiders, "candidatos": fichas}
    json.dump(res, open(os.path.join(AQUI, "privilegiados.json"), "w"), ensure_ascii=False, indent=1, default=str)

    print("\n=== WALLETS CON INFO PRIVILEGIADA ENCONTRADAS (método propio) ===")
    for f in insiders[:12]:
        print(f"\n  💎 {f['symbol']:<10} {f['wallet']}")
        print(f"      recibió {f['recibido']:.0f} tokens SIN pagar · puso {f['pago']:.2f} SOL")
        print(f"      en ese token: metió {f['token_in']} SOL y cobró {f['token_out']} SOL "
              f"= {f['token_pnl']:+.2f} SOL (ROI {f['token_roi']})")
        print(f"      sigue teniendo: {f['tiene_ahora']:.0f} tokens · su historial: {f['cerradas']} cerradas · "
              f"PnL total {f['pnl_total']} · wr {f['win_rate']}% · sin compra {f['sin_compra']}% · "
              f"{f['etiqueta']['emoji']} {f['etiqueta']['tipo']}")
        if f.get("fondeo"):
            print(f"      🔗 la fondearon: {', '.join(f['fondeo'])}")
    if not insiders:
        print("  (ninguno superó el mínimo de ganancia; revisar candidatos en privilegiados.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
