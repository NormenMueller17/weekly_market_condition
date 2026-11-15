"""Prueft, ob Kurskorrelation gleichzeitig offener Positionen eine Klumpenbildung
sichtbar macht, die der bestehende Sektor-Cap (`max_positions_per_sector`,
Datenanbieter-Sektor) uebersieht.

Anlass (2026-11-15): Externes Feedback bemaengelt, dass SBH ("Consumer
Cyclical"), EL und ELF ("Consumer Defensive" lt. eigenem Datenfeld, Feedback
nennt "Household & Personal Products") als Beauty-Cluster rund 42 % des
Eigenkapitals ausmachen, der Sektor-Cap das aber nicht erfasst, weil die
Sektor-Tags unterschiedlich sind. Empfehlung im Feedback: "Limit nach
Korrelation oder Branchenthema statt nach Datenanbieter-Sektor."

Ein "Branchenthema" ist subjektiv (wer legt die Cluster-Liste fest?). Dieses
Skript testet stattdessen die objektiv berechenbare Variante -- Korrelation
der taeglichen Renditen -- weil sie genau das Risiko misst, vor dem ein
Cluster-Limit eigentlich schuetzen soll (gleichzeitig in dieselbe Richtung
laufende Positionen), unabhaengig von jeder Sektor-Taxonomie.

Methode:
  1. Zeitstrahl aller je gehaltenen Positionen (offen + geschlossen) aus dem
     Journal: symbol, entry_date, exit_date (offene Positionen: bis heute).
  2. Fuer jedes Paar mit UEBERLAPPENDER Haltedauer: Korrelation der taeglichen
     Returns im Ueberlappungsfenster (mind. `--min-overlap-days` Handelstage,
     sonst zu verrauscht).
  3. Vergleich: Paare mit |Korrelation| >= `--corr-threshold`, aufgeteilt in
     - gleicher Sektor-Tag (der Cap haette gegriffen)
     - unterschiedlicher Sektor-Tag (der Cap haette NICHT gegriffen -- das
       ist genau die Luecke aus dem Feedback)

  python sector_cluster_correlation_analysis.py
  python sector_cluster_correlation_analysis.py --corr-threshold 0.6
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from atr_stop_analysis import load_ohlc
from experiment_log import log_experiment


def _clean_close(df: pd.DataFrame) -> pd.Series:
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s = df["Close"].astype(float)
    s.index = idx
    return s.sort_index()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default="docs/data/trades.json")
    ap.add_argument("--corr-threshold", type=float, default=0.6,
                    help="Ab welcher Korrelation ein Paar als Klumpen gilt (Default 0.6).")
    ap.add_argument("--min-overlap-days", type=int, default=10,
                    help="Mindest-Handelstage gemeinsamer Haltedauer fuer eine Korrelationsberechnung (Default 10).")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as fh:
        data = json.load(fh)
    closed = data.get("closed", [])
    opened = data.get("open", [])
    all_trades = closed + opened
    if len(all_trades) < 2:
        print("Zu wenige Trades fuer einen Paarvergleich.")
        return 0

    today = pd.Timestamp(datetime.now().date())
    positions = []
    for t in all_trades:
        entry = pd.Timestamp(t["entry_date"])
        exit_ = pd.Timestamp(t["exit_date"]) if t.get("exit_date") else today
        positions.append({"symbol": t["symbol"], "sector": t.get("sector"),
                          "entry": entry, "exit": exit_})
    pos_df = pd.DataFrame(positions)

    symbols = sorted(pos_df["symbol"].unique())
    ohlc = load_ohlc(symbols, args.refresh)
    closes = {s: _clean_close(ohlc[s]) for s in symbols if s in ohlc and not ohlc[s].empty}

    rows = []
    n = len(pos_df)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = pos_df.iloc[i], pos_df.iloc[j]
            if a["symbol"] == b["symbol"]:
                continue
            overlap_start = max(a["entry"], b["entry"])
            overlap_end = min(a["exit"], b["exit"])
            if overlap_end <= overlap_start:
                continue  # Haltedauern ueberschneiden sich nicht

            ca, cb = closes.get(a["symbol"]), closes.get(b["symbol"])
            if ca is None or cb is None:
                continue
            ra = ca.pct_change()
            rb = cb.pct_change()
            common = ra.index.intersection(rb.index)
            window = common[(common >= overlap_start) & (common <= overlap_end)]
            if len(window) < args.min_overlap_days:
                continue

            corr = float(ra.reindex(window).corr(rb.reindex(window)))
            if pd.isna(corr):
                continue

            rows.append({
                "symbol_a": a["symbol"], "symbol_b": b["symbol"],
                "sector_a": a["sector"], "sector_b": b["sector"],
                "same_sector": bool(a["sector"] and a["sector"] == b["sector"]),
                "overlap_days": len(window),
                "overlap_start": overlap_start.date(), "overlap_end": overlap_end.date(),
                "corr": round(corr, 3),
            })

    if not rows:
        print("Keine auswertbaren Paare (keine ueberlappenden Haltedauern mit genug Kursdaten).")
        return 0

    pairs = pd.DataFrame(rows).sort_values("corr", ascending=False)
    print(f"=== Paare mit ueberlappender Haltedauer (n={len(pairs)}) ===")
    print(f"Korrelations-Schwelle: {args.corr_threshold}, Mindest-Ueberlappung: {args.min_overlap_days} Handelstage\n")

    cols = ["symbol_a", "symbol_b", "sector_a", "sector_b", "same_sector",
           "overlap_days", "corr"]
    print(pairs[cols].to_string(index=False))
    print()

    geklumpt = pairs[pairs["corr"].abs() >= args.corr_threshold]
    geklumpt_diff_sektor = geklumpt[~geklumpt["same_sector"]]
    geklumpt_gleich_sektor = geklumpt[geklumpt["same_sector"]]

    print(f"Paare >= |{args.corr_threshold}| Korrelation: {len(geklumpt)} von {len(pairs)}")
    print(f"  davon GLEICHER Sektor-Tag (Cap haette gegriffen):      {len(geklumpt_gleich_sektor)}")
    print(f"  davon UNTERSCHIEDLICHER Sektor-Tag (Cap haette NICHT\n"
         f"  gegriffen -- die vom Feedback beschriebene Luecke):    {len(geklumpt_diff_sektor)}")
    if not geklumpt_diff_sektor.empty:
        print("\nKonkrete Faelle mit uebersehener Korrelation:")
        print(geklumpt_diff_sektor[cols].to_string(index=False))

    log_experiment(
        tool="sector_cluster_correlation_analysis",
        params={"corr_threshold": args.corr_threshold, "min_overlap_days": args.min_overlap_days},
        metrics={
            "n_pairs": len(pairs),
            "n_geklumpt": len(geklumpt),
            "n_geklumpt_diff_sektor": len(geklumpt_diff_sektor),
            "n_geklumpt_gleich_sektor": len(geklumpt_gleich_sektor),
        },
        context={"json": args.json, "n_positions": n},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
