"""Prueft, ob ein zusaetzlicher Umsatzboden zur ODER-Regel (Umsatz >= 20 % ODER
EPS-Q >= 20 %) die eigenen Trade-Ergebnisse verbessert haette.

Anlass (2026-11-15): Externes Feedback (Feedback_Blueprint_und_Kaufsignale_
2026-11-15.pdf) bemaengelt, dass 4 von 6 juengsten Signalen (EL, GWW, IBEX,
SBH) die Wachstumshuerde nur ueber das EPS-Q-Kriterium schaffen, Umsatz oft
nur knapp zweistellig oder niedriger waechst. Vorschlag: zusaetzlich zur
ODER-Regel einen Umsatzboden (z. B. 15 %) verlangen, sodass ein reiner
EPS-Treffer ohne Mindest-Umsatzwachstum nicht mehr reicht.

Das ist NICHT dieselbe Frage wie [[project_rev_eps_or_logic]] (2026-09-06):
dort ging es darum, ob UND (frueher) oder ODER (jetzt) gilt, und ob der
Score als Ausgleich auf 8/8 verschaerft werden sollte -- beides wurde an den
eigenen Trades widerlegt. Hier geht es um einen ZUSAETZLICHEN Mindestwert
NUR fuer den Umsatz-Teil der bestehenden ODER-Regel, unabhaengig vom Score.

Methode:
  1. Fuer jeden Journal-Trade (offen + geschlossen) wird der Signal-Snapshot
     aus `docs/data/signals_meta_*.json` gesucht (Datei mit Datum <= Entry,
     innerhalb von `--lookback-days`, Ticker muss im Snapshot vorkommen).
     Diese Dateien existieren erst ab 2026-04-23 -- aeltere Trades bleiben
     ohne Fundamentaldaten und werden als `kein_signal_gefunden` ausgewiesen,
     nicht stillschweigend auf 0 gesetzt.
  2. Je gefundenem Trade: revenue_growth, eps_growth_last_q aus dem Snapshot.
  3. Gruppierung:
     - "eps_only_schwach": revenue_growth < --floor UND eps_growth_last_q
       erfuellt die (aktuelle) EPS-Schwelle -- genau die Gruppe, die der
       vorgeschlagene Umsatzboden zusaetzlich ausschliessen wuerde.
     - "umsatz_ok": revenue_growth >= --floor.
  4. Median-Split von realized_plpc (nur geschlossene Trades) zwischen
     beiden Gruppen, analog zu distribution_day_analysis.py.

  python revenue_floor_analysis.py
  python revenue_floor_analysis.py --floor 15
  python revenue_floor_analysis.py --json docs/data/trades.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from experiment_log import log_experiment

SIGNALS_DIR = Path("docs/data")


def find_signal_record(symbol: str, entry_date: date, lookback_days: int) -> tuple[str | None, dict | None]:
    """Sucht den Signal-Snapshot zu `symbol`, der zeitlich am naechsten VOR
    oder AM Entry-Datum liegt (Report samstags/mittwochs, Entry meist der
    naechste Handelstag danach). Gibt (gefundenes_datum, record) oder
    (None, None) zurueck.
    """
    for back in range(0, lookback_days + 1):
        d = entry_date - timedelta(days=back)
        f = SIGNALS_DIR / f"signals_meta_{d.isoformat()}.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for s in data.get("signals", []):
            if s.get("ticker") == symbol:
                return d.isoformat(), s
    return None, None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default="docs/data/trades.json")
    ap.add_argument("--floor", type=float, default=15.0,
                    help="Vorgeschlagener Umsatzboden in %% (Default 15, wie im Feedback-PDF).")
    ap.add_argument("--eps-threshold", type=float, default=20.0,
                    help="Aktuelle EPS-Q-Schwelle, ueber die ein Trade momentan durchkommt (Default 20, aus rules.json).")
    ap.add_argument("--lookback-days", type=int, default=7,
                    help="Wie viele Tage vor dem Entry nach dem Signal-Snapshot gesucht wird (Default 7).")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as fh:
        data = json.load(fh)
    closed = data.get("closed", [])
    opened = data.get("open", [])
    all_trades = closed + opened
    if not all_trades:
        print(f"Keine Trades in {args.json}.")
        return 0

    rows = []
    for t in all_trades:
        entry_date = date.fromisoformat(t["entry_date"])
        sig_date, rec = find_signal_record(t["symbol"], entry_date, args.lookback_days)
        row = {
            "symbol": t["symbol"], "entry_date": t["entry_date"],
            "is_open": t in opened,
            "realized_plpc": t.get("realized_plpc"),
            "exit_reason": t.get("exit_reason"),
        }
        if rec is None:
            row["status"] = "kein_signal_gefunden"
            row["revenue_growth"] = None
            row["eps_growth_last_q"] = None
        else:
            row["status"] = "ok"
            row["signal_date"] = sig_date
            row["revenue_growth"] = rec.get("revenue_growth")
            row["eps_growth_last_q"] = rec.get("eps_growth_last_q")
        rows.append(row)

    df = pd.DataFrame(rows)

    print(f"=== Alle Journal-Trades (n={len(df)}, davon offen={len(opened)}) ===")
    print(f"Signal-Snapshots verfuegbar ab 2026-04-23, Lookback-Fenster: {args.lookback_days} Tage\n")
    print("Status:")
    for s, c in df["status"].value_counts().items():
        print(f"  {s}: {c}")
    print()

    ok = df[df["status"] == "ok"].copy()
    if ok.empty:
        print("Keine auswertbaren Trades (kein passender Signal-Snapshot gefunden).")
        return 0

    ok["eps_only_schwach"] = (
        ok["revenue_growth"].notna() & (ok["revenue_growth"] < args.floor)
        & ok["eps_growth_last_q"].notna() & (ok["eps_growth_last_q"] >= args.eps_threshold)
    )
    ok["umsatz_ok"] = ok["revenue_growth"].notna() & (ok["revenue_growth"] >= args.floor)

    cols = ["symbol", "entry_date", "revenue_growth", "eps_growth_last_q",
           "eps_only_schwach", "realized_plpc", "exit_reason", "is_open"]
    print(ok[cols].sort_values("revenue_growth").to_string(index=False))
    print()

    n_floor_excluded = int(ok["eps_only_schwach"].sum())
    print(f"Waeren bei Umsatzboden {args.floor:.0f}% zusaetzlich ausgeschlossen worden: "
         f"{n_floor_excluded} von {len(ok)} gefundenen Trades "
         f"({n_floor_excluded/len(ok)*100:.0f}%)\n")

    closed_ok = ok[~ok["is_open"]]
    if closed_ok.empty or closed_ok["eps_only_schwach"].nunique() < 2:
        print("Zu wenige geschlossene Trades mit beiden Gruppen fuer einen Median-Split.")
        metrics_split = {}
    else:
        schwach = closed_ok[closed_ok["eps_only_schwach"]]
        stark = closed_ok[~closed_ok["eps_only_schwach"]]
        print(f"Median-Split (nur geschlossene Trades, n={len(closed_ok)}):")
        print(f"  Waere ausgeschlossen worden ('eps_only_schwach', n={len(schwach)}):  "
             f"median plpc = {schwach['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(schwach['realized_plpc'] > 0).mean()*100:.0f}%")
        print(f"  Waere weiter dabei ('umsatz_ok' o. Umsatz>=0 unter Boden, n={len(stark)}):  "
             f"median plpc = {stark['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(stark['realized_plpc'] > 0).mean()*100:.0f}%")
        metrics_split = {
            "n_closed_ok": len(closed_ok),
            "n_eps_only_schwach": len(schwach),
            "median_plpc_eps_only_schwach": None if schwach.empty else round(float(schwach["realized_plpc"].median()), 2),
            "median_plpc_rest": None if stark.empty else round(float(stark["realized_plpc"].median()), 2),
            "hitrate_eps_only_schwach": None if schwach.empty else round(float((schwach["realized_plpc"] > 0).mean()), 3),
            "hitrate_rest": None if stark.empty else round(float((stark["realized_plpc"] > 0).mean()), 3),
        }

    log_experiment(
        tool="revenue_floor_analysis",
        params={"floor": args.floor, "eps_threshold": args.eps_threshold,
               "lookback_days": args.lookback_days},
        metrics={
            "n_total": len(df), "n_ok": len(ok),
            "n_floor_excluded": n_floor_excluded,
            **metrics_split,
        },
        context={"json": args.json},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
