#!/usr/bin/env python3
"""Comandos por chat en Discord para el sistema de calls.

Por qué con prefijo y no slash command: los slash exigen registrar la aplicación y responder la
interacción en menos de 3 segundos (necesita un gateway propio corriendo). Con prefijo alcanza, no
depende de nada extra y se lee igual.

Comandos:
  !rick            los contratos con mejor pico del día, listos para pegar a mano en Rick
  !rick todo       los mejores del histórico
  !tabla           resultados del día (del FDV del aviso al pico)
  !tabla todo      resultados completos
  !ayuda           esta lista

Lo corre el cron cada minuto; ignora los mensajes de bots (si no, se contestaría a sí mismo).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import tabla_calls as TC          # noqa: E402

VISTO = os.path.join(AQUI, "comando_visto.json")
DISPARADORES = ("!rick", "!tabla", "!ath", "!ayuda", "!profit", "!ganancia", "!grupos", "!wallets")


def log(m):
    print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {m}", flush=True)


def _env():
    d = {}
    for linea in open(os.path.expanduser("~/.hermes/.env")):
        if "=" in linea and not linea.strip().startswith("#"):
            k, v = linea.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def _api(method, path, cuerpo=None, token=None):
    req = urllib.request.Request(
        "https://discord.com/api/v10" + path, method=method,
        headers={"Authorization": f"Bot {token}", "User-Agent": "hermes-calls/1.0",
                 "Content-Type": "application/json"},
        data=json.dumps(cuerpo).encode() if cuerpo else None)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=25).read().decode() or "null")
    except urllib.error.HTTPError as e:
        log(f"  ! Discord {e.code}: {e.read().decode()[:160]}")
        return None


def _leer(path, dflt):
    try:
        return json.load(open(path))
    except Exception:
        return dflt


def _guardar(path, datos):
    tmp = path + ".%d.tmp" % os.getpid()
    json.dump(datos, open(tmp, "w"))
    os.replace(tmp, path)


def ayuda():
    return ("🤖 **Comandos del sistema de calls**\n"
            "`!rick` → contratos de los **últimos 30 min** (para pegar en Rick ya mismo)\n"
            "`!rick dia` → los mejores del día · `!rick todo` → del histórico\n"
            "`!profit` → ¿hubo profit después de las últimas calls?\n"
            "`!profit <contrato>` → el chequeo de ESE token, desde el FDV del aviso\n"
            "`!tabla` → resultados del día (del FDV del aviso al pico)\n"
            "`!tabla todo` → resultados completos\n"
            "`!ath` → el ATH de todos los contratos del server",
            "`!ayuda` → esta lista")


def _wallets():
    """Las wallets con mejor puntaje (medido), aclarando cuáles tienen muestra suficiente."""
    d = _leer(os.path.join(AQUI, "wallets_puntaje.json"), None)
    if not d:
        return "🎯 Todavía no hay puntajes de wallets. Probá en unos minutos."
    ws = d.get("wallets") or {}
    con = [(w, v) for w, v in ws.items() if v.get("pnl_copia") is not None and v["n"] >= 3]
    con.sort(key=lambda kv: -kv[1]["pnl_copia"])
    copy = [x for x in con if x[1].get("tier_wallet") == "🟢 COPIABLE"]
    nunca = [x for x in con if x[1].get("tier_wallet") == "🔴 NUNCA"]
    lin = [f"🎯 **WALLETS — ¿CONVIENE COPIARLAS?** {len(ws)} medidas · {len(con)} con 3+ entradas",
           "_el ranking es por **resultado de copiarlas** ($1 por entrada, suma de múltiplo−1): la mediana engaña_", ""]
    lin.append(f"🟢 **COPIABLES ({len(copy)})** — su historial da ganancia:")
    for w, v in copy[:10]:
        lin.append(f"   **{v['pnl_copia']:+.1f}** `{w[:10]}…` — {v['n']} entradas · {v['hits']} ganadoras · "
                   f"mejor {v['mejor']:.1f}x · mediana {v['mediana']:.2f}x")
    if len(copy) > 10:
        lin.append(f"   … y {len(copy)-10} más")
    lin += ["", f"🔴 **NUNCA ({len(nunca)})** — no dieron ni una ganadora y pierden: {len(nunca)} wallets"]
    lin.append("")
    lin.append("_El canal #🟢-seguir avisa SÓLO cuando entra una de las copiables._")
    return "\n".join(lin)


def _grupos():
    """Los grupos de wallets organizados por tier (lee lo que dejó la clasificación)."""
    d = _leer(os.path.join(AQUI, "grupos_tier.json"), None)
    if not d:
        return "🕸️ Todavía no hay clasificación de grupos. Probá en unos minutos."
    lin = ["🕸️ **GRUPOS DE WALLETS por calidad (medido, no por tamaño)**", ""]
    for t in ("🟢 SEGUIR", "🟠 LOTERÍA", "🟡 OBSERVAR", "🔴 DESCARTAR"):
        gs = (d.get("resumen") or {}).get(t) or []
        if not gs:
            continue
        lin.append(f"**{t}** ({len(gs)})")
        for g in gs[:6]:
            rec = (f"{g['n']} entradas · mediana {g['mediana']:.2f}x · {100*(g['hit'] or 0):.0f}% ≥1,5x · "
                   f"mejor {g['mejor']:.2f}x") if g["n"] else "sin entradas medidas"
            lin.append(f"   · **{g.get('nombre') or '?'}** ({g['tamano']} wallets) — {rec}")
        if len(gs) > 6:
            lin.append(f"   … y {len(gs)-6} más")
        lin.append("")
    lin.append("_Los 🔴 no avisan más (compran tokens gigantes o pierden sistemático); los 🟠 avisan marcados._")
    return "\n".join(lin)



def _fk(x):
    """Número corto para la tabla: 3.3M, 184k, 950."""
    if not x:
        return "—"
    x = float(x)
    if x >= 1_000_000:
        return "%.1fM" % (x / 1_000_000)
    if x >= 1_000:
        return "%.0fk" % (x / 1_000)
    return "%.0f" % x


def _ath():
    """ATH de los contratos del server: aviso, pico posterior y máximo histórico del pool."""
    d = _leer(os.path.join(AQUI, "ath_server.json"), {})
    f = d.get("filas") or []
    if not f:
        return ("Todavía no hay mediciones de ATH. Se van completando de a poco porque la API de velas "
                "tiene límite de ritmo.")
    top = f[:15]
    medidos = sum(1 for x in f if x.get("ath"))
    l = ["**ATH de los contratos del server**",
         "_%d contratos auditados · %d con ATH de pool medido · %s_" % (d.get("contratos") or len(f), medidos,
                                                                        (d.get("generado") or "")[:16]),
         "```",
         "%-12s %7s %8s %6s %8s" % ("token", "aviso", "pico", "pico x", "ath pool")]
    for x in top:
        l.append("%-12s %7s %8s %5sx %8s" % (
            str(x.get("simbolo") or "?")[:12], _fk(x.get("fdv_aviso")), _fk(x.get("pico")),
            "%.1f" % (x.get("mult_pico") or 0), _fk(x.get("ath"))))
    l.append("```")
    l.append("_aviso_ = FDV cuando salió la call · _pico_ = máximo posterior medido · "
             "_ath pool_ = máximo histórico sobre el pool del DEX")
    l.append("_Ojo: el tramo de la curva de pump.fun (antes de graduarse) no aparece en las velas del "
             "pool; cuando el pico del aviso supera al del pool, ese tramo no está cubierto._")
    return "\n".join(l)


def respuesta(texto):
    """Arma la contestación según el comando pedido."""
    t = " ".join((texto or "").lower().split())
    todo = "todo" in t
    if t.startswith("!ayuda") or t.startswith("!help"):
        return ayuda()
    if t.startswith("!tabla"):
        # sólo queda Argos: la tabla es la de sus avisos
        return TC.texto_tabla(todo=todo, n=10 if not todo else 15, argos=True)
    if t.startswith("!ath") or t.startswith("!ath"):
        return _ath()
    if t.startswith("!grupos"):
        return _grupos()
    if t.startswith("!wallets"):
        return _wallets()
    if t.startswith("!profit") or t.startswith("!ganancia"):
        partes = (texto or "").split()
        if len(partes) > 1:                       # !profit <contrato>
            return TC.evaluar(partes[1].strip())
        return TC.ultimas_evaluaciones(6)
    if t.startswith("!rick"):
        # Por defecto SÓLO los últimos 30 min: Rick cuenta la ganancia desde el momento del pegado, así
        # que pegar calls viejas le da una línea base falsa (se come todo lo que el token ya subió).
        if todo:
            contratos, ventana = TC.contratos(n=10, todo=True), "del histórico"
        elif "dia" in t or "día" in t:
            contratos, ventana = TC.contratos(n=10), "del día"
        else:
            contratos, ventana = TC.contratos(n=8, minutos=30), "de los últimos 30 minutos"
        if not contratos:
            return (f"🔧 No hay calls medidas {ventana}. Pedí `!rick dia` o esperá la próxima.")
        return (f"🔧 **Contratos para Rick** — {ventana}. Pegá uno por mensaje y **ya mismo**: Rick "
                f"cuenta la ganancia desde el momento en que VOS los pegás, así que si esperás, su "
                f"línea base arranca más arriba y no cuenta lo que el token ya movió.\n\n"
                + "\n".join(contratos))
    return None


PEGADOS = os.path.join(AQUI, "rick_pegados.json")


def anotar_pegados(token, canales, mints):
    """Registra cuándo alguien pegó a mano un contrato NUESTRO, y qué contestó Rick.

    Por qué existe: Rick toma como línea base el FDV del primer scan, así que la demora entre el aviso
    y el pegado es justo lo que su leaderboard NO cuenta. Guardando el momento del pegado (y el texto
    de la respuesta de Rick) después se puede medir cuánto se perdió por pegarlo tarde.
    """
    d = _leer(PEGADOS, {"vistos": [], "pegados": []})
    vistos = set(d.get("vistos") or [])
    nuevos = 0
    for canal in canales:
        msgs = _api("GET", f"/channels/{canal}/messages?limit=20", token=token)
        if not isinstance(msgs, list):
            continue
        for m in reversed(msgs):
            mid = m.get("id")
            if mid in vistos:
                continue
            vistos.add(mid)
            autor = m.get("author") or {}
            texto = (m.get("content") or "")
            es_rick = "rick" in (autor.get("username") or "").lower() and autor.get("bot")
            if es_rick:
                # Respuesta de Rick: se guarda tal cual (todavía no escaneó ninguno, así que
                # no se inventa un parser del footer: se guarda el crudo para cuando haya muestras).
                for p in d["pegados"]:
                    if not p.get("rick_respuesta") and p.get("canal") == canal:
                        p["rick_respuesta"] = texto[:300]
                continue
            if autor.get("bot"):
                continue
            hits = [x for x in mints if x and x in texto]
            if not hits:
                continue
            ahora = time.time()
            for mint in hits:
                c = next((x for x in _leer_hist() if x.get("mint") == mint), None)
                demora = ((ahora - (c.get("ts_epoch") or ahora)) / 60) if c else None
                ya = [p for p in d["pegados"] if p.get("mint") == mint]
                if ya:
                    continue
                d["pegados"].append({"mint": mint, "simbolo": (c or {}).get("simbolo"),
                                     "pegado_ts": ahora, "demora_min": None if demora is None else round(demora, 1),
                                     "canal": canal, "autor": autor.get("username")})
                nuevos += 1
                log(f"  pegado detectado: {(c or {}).get('simbolo')} · {demora and round(demora, 1)} min después del aviso")
    d["vistos"] = list(vistos)[-500:]
    _guardar(PEGADOS, d)
    return nuevos


def _leer_hist():
    return _leer(os.path.join(AQUI, "calls_historial.json"), []) or []


def revisar(token, canales, test=False):
    visto = _leer(VISTO, {})
    contestados = 0
    for canal in canales:
        msgs = _api("GET", f"/channels/{canal}/messages?limit=10", token=token)
        if not isinstance(msgs, list):
            continue
        for m in reversed(msgs):                      # del más viejo al más nuevo
            mid = m.get("id")
            autor = m.get("author") or {}
            if autor.get("bot"):                      # nunca contestar a un bot (ni a nosotros mismos)
                continue
            texto = (m.get("content") or "").strip()
            if not any(texto.lower().startswith(d) for d in DISPARADORES):
                continue
            if visto.get(mid):
                continue
            visto[mid] = time.time()
            r = respuesta(texto)
            if not r:
                continue
            if len(r) > 1900:                         # Discord corta en 2000
                r = r[:1890] + "\n…"
            _api("POST", f"/channels/{canal}/messages", {"content": r}, token=token)
            contestados += 1
            log(f"  contestado {texto.split()[0]!r} en {canal} ({len(r)} chars)")
            time.sleep(1.2)                           # no juntar dos envíos seguidos
    _guardar(VISTO, {k: v for k, v in visto.items() if time.time() - v < 7 * 86400})
    return contestados


def main():
    ap = argparse.ArgumentParser(description="Comandos por chat (!rick, !tabla, !ayuda)")
    ap.add_argument("--probar", metavar="TEXTO", help="simula un comando y manda la respuesta al canal")
    ap.add_argument("--canal", default=None, help="canal para la prueba")
    a = ap.parse_args()
    env = _env()
    token = env.get("DISCORD_BOT_TOKEN")
    if not token:
        log("! falta DISCORD_BOT_TOKEN en ~/.hermes/.env")
        return 1
    canales = [c for c in (env.get("DISCORD_ARGOS_CHANNEL"), env.get("DISCORD_PUMP_CHANNEL")) if c]
    if a.probar:
        r = respuesta(a.probar)
        canal = a.canal or canales[0]
        print(f"--- respuesta a {a.probar!r} ({len(r or '')} chars) → canal {canal} ---")
        print(r)
        if r:
            _api("POST", f"/channels/{canal}/messages", {"content": r[:1900]}, token=token)
            log("  prueba enviada")
        return 0
    n = revisar(token, canales)
    if n:
        log(f"comandos contestados: {n}")
    mints = [x.get("mint") for x in _leer_hist() if x.get("mint")]
    p = anotar_pegados(token, canales, mints)          # mide la demora entre el aviso y el pegado
    if p:
        log(f"pegados nuevos registrados: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
