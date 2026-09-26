"""Traegt fehlende Metadaten von Mittwochskaeufen im Tradetagebuch nach.

Hintergrund (2026-09-26): CDNA, QMCO und TRLV wurden am 2026-09-23 vom Mid-Week-Lauf
gekauft (Fill 09-24) und stehen im Journal ohne Firma, Sektor, Muster, RS und
Scorecard. Der Lauf schreibt seine `signals_meta_<Datum>.json` zwar seit 14d3a96, aber
der Workflow checkte sie bis zu diesem Tag nicht ein -- sie ging mit dem Runner
verloren. Ab jetzt landet sie im Repo; fuer diese drei Titel gibt es sie nicht mehr.

Was sich rekonstruieren laesst: die Watchlist, aus der der Mid-Week-Lauf seine Kandidaten
nimmt (`docs/data/midweek_watchlist.json`), liegt in der Git-Historie. Sie enthaelt
Firma, Sektor, RS, Industrie-Rang und die Scorecard-Kriterien je Kandidat. Das Skript
nimmt je Trade die NEUESTE Version mit `generated` VOR dem Einstiegsdatum, in der der
Titel steht -- also die, die der Lauf am Mittwoch tatsaechlich gelesen hat.

Was sich NICHT rekonstruieren laesst: Einstiegskurs am Signaltag und Stop. Der initiale
Stop wird deshalb nicht geraten; er kommt aus den Kindorders beim Broker
(`alpaca_client.find_initial_stops`, im Journal-Sync Schritt 4b).

Es werden nur LEERE Felder gefuellt, und die Herkunft steht im Feld `meta_quelle`.

  python repair_midweek_journal.py            # Vorschau, schreibt nichts
  python repair_midweek_journal.py --apply    # schreibt trades.json + trades.html
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import trade_journal

WATCHLIST = "docs/data/midweek_watchlist.json"
LEER = (None, "", "–", "-", "�")


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def watchlist_versionen(limit: int = 30) -> list[tuple[str, dict]]:
    """[(kurzer Hash, Inhalt)], neueste zuerst. Nur lesbare Versionen."""
    out = []
    for zeile in _git("log", f"-{limit}", "--format=%H", "--", WATCHLIST).split():
        try:
            out.append((zeile[:7], json.loads(_git("show", f"{zeile}:{WATCHLIST}"))))
        except (ValueError, TypeError):
            continue
    return out


def braucht_nachtrag(t: dict) -> bool:
    return (t.get("company") in LEER or t.get("sector") in LEER
            or t.get("rs_score") is None or not t.get("criteria")
            or t.get("pattern") in LEER)


def hat_wochensignal(symbol: str, entry_date: str, tage: int = 10) -> bool:
    """True, wenn ein Wochenlauf-Signal (Muster ungleich "Mid-Week") zum Titel existiert.

    Solche Trades haben ihre Metadaten in den signals_meta-Dateien und duerfen NICHT
    aus der Mid-Week-Watchlist ueberschrieben werden -- ein Titel kann in beiden
    stehen, gekauft wurde er dann ueber den Wochenlauf.
    """
    import datetime
    ziel = datetime.date.fromisoformat(entry_date)
    for f in trade_journal._signal_files(80):
        try:
            tag = datetime.date.fromisoformat(f.stem.split("_")[-1])
            if not (0 <= (ziel - tag).days <= tage):
                continue
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for sig in payload.get("signals", []):
            if sig.get("ticker") == symbol and sig.get("pattern") != "Mid-Week":
                return True
    return False


def finde_eintrag(symbol: str, entry_date: str, versionen: list) -> tuple | None:
    """Neueste Watchlist-Version mit generated < entry_date, in der `symbol` steht."""
    for h, d in versionen:
        if (d.get("generated") or "9999") >= entry_date:
            continue
        for item in d.get("watchlist", []):
            if item.get("ticker") == symbol:
                return h, d["generated"], item
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Aenderungen schreiben")
    args = ap.parse_args()

    data = trade_journal.load()
    kandidaten = [t for t in data.get("open", []) + data.get("closed", [])
                  if t.get("symbol") and t.get("entry_date") and braucht_nachtrag(t)]
    if not kandidaten:
        print("[MIDWEEK-REPAIR] Kein Trade mit fehlenden Metadaten.")
        return 0

    versionen = watchlist_versionen()
    print(f"[MIDWEEK-REPAIR] {len(kandidaten)} Trade(s) mit Luecken, "
          f"{len(versionen)} Watchlist-Version(en) in der Historie")

    geaendert = []
    for t in kandidaten:
        if hat_wochensignal(t["symbol"], t["entry_date"]):
            continue
        treffer = finde_eintrag(t["symbol"], t["entry_date"], versionen)
        if not treffer:
            continue
        h, generated, item = treffer
        neu = {
            "company":   item.get("company"),
            "sector":    item.get("sector"),
            "rs_score":  item.get("rs_score"),
            "criteria":  item.get("criteria") or {},
            "pattern":   "Mid-Week",
        }
        felder = []
        for k, v in neu.items():
            leer = (t.get(k) in LEER) if k != "criteria" else not t.get(k)
            if leer and v not in LEER and v != {}:
                felder.append((k, v))
        if not felder:
            continue
        geaendert.append((t, h, generated, felder))
        print(f"  {t['symbol']:6s} Entry {t['entry_date']}  Watchlist {generated} (Commit {h}): "
              + ", ".join(k for k, _ in felder))

    if not geaendert:
        print("[MIDWEEK-REPAIR] Nichts zu tun.")
        return 0
    if not args.apply:
        print(f"\n{len(geaendert)} Trade(s) — Vorschau, nichts geschrieben. Mit --apply anwenden.")
        return 0

    for t, h, generated, felder in geaendert:
        for k, v in felder:
            t[k] = v
        t["meta_quelle"] = f"rekonstruiert aus midweek_watchlist ({generated}, git {h})"
    trade_journal.save(data)
    trade_journal.build_and_save_html(data)
    print(f"[MIDWEEK-REPAIR] {len(geaendert)} Trade(s) aktualisiert → trades.json, trades.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
