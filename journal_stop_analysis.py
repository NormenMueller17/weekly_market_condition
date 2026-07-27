"""Prüft an den REALEN Trades: erholen sich ausgestoppte Positionen?

Anlass (2026-07-27): Die Stop-Analyse auf VCP-Backtest-Entries
(`vcp_stop_analysis.py`, n=212) ergab, dass gestoppte Titel sich NICHT erholen —
nur 16 % standen 13 Wochen später im Plus. Dieses Tool prüft dieselbe Frage auf
dem Tradetagebuch, also an dem, was das System tatsächlich gehandelt hat.

Die beiden Grundgesamtheiten sind NICHT dieselbe Sache und dürfen nicht
vermengt werden:
  * Backtest  — VCP-Geometrie, RS ≥ 70 (Median ≈ 84), n = 212
  * Journal   — überwiegend musterlose Volumen-Ausbrüche (`pattern: "–"`,
                Folge von `require_pattern=False`), RS-Median ≈ 95, n einstellig

Gemessen wird ab EXIT-Preis, gegen SPY über denselben Zeitraum — sonst zeigt
eine Erholungsphase des Gesamtmarkts ein Alpha vor, das keines ist.

  python journal_stop_analysis.py
  python journal_stop_analysis.py --horizons 4,8,13 --json docs/data/trades.json
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from experiment_log import log_experiment
from vcp_forward_test import load_benchmark
from vcp_universe_check import load_history


def post_stop_returns(trades: pd.DataFrame, hist: dict[str, pd.DataFrame],
                      bench: pd.DataFrame | None, horizons: tuple[int, ...]) -> pd.DataFrame:
    """Kursentwicklung ab Exit-Preis, je Horizont, plus Alpha gegen SPY."""
    bc = None
    if bench is not None:
        bc = bench["Close"].astype(float)
        bc.index = pd.to_datetime(bc.index)

    rows = []
    for _, r in trades.iterrows():
        df = hist.get(r["symbol"])
        if df is None:
            continue
        idx = pd.to_datetime(df.index)
        close = df["Close"].astype(float)
        pos = int(idx.searchsorted(pd.Timestamp(r["exit_date"])))
        exit_px = float(r["exit_price"])
        if pos >= len(close) or not np.isfinite(exit_px) or exit_px <= 0:
            continue

        out = {"symbol": r["symbol"], "exit_date": pd.Timestamp(r["exit_date"]).date(),
               "pattern": r.get("pattern"), "rs": r.get("rs_score"),
               "realized_plpc": r.get("realized_plpc"),
               "haltetage": (pd.Timestamp(r["exit_date"]) -
                             pd.Timestamp(r["entry_date"])).days}

        bpos = int(bc.index.searchsorted(pd.Timestamp(r["exit_date"]))) if bc is not None else None
        for h in horizons:
            out[f"ret_{h}w"] = (float(close.iloc[pos + h]) / exit_px - 1) * 100 \
                if pos + h < len(close) else np.nan
            if bc is not None and bpos is not None and bpos + h < len(bc):
                mk = (float(bc.iloc[bpos + h]) / float(bc.iloc[bpos]) - 1) * 100
                out[f"spy_{h}w"] = mk
                out[f"alpha_{h}w"] = out[f"ret_{h}w"] - mk
            else:
                out[f"spy_{h}w"] = np.nan
                out[f"alpha_{h}w"] = np.nan
        rows.append(out)

    return pd.DataFrame(rows)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default="docs/data/trades.json")
    ap.add_argument("--horizons", default="4,8,13")
    ap.add_argument("--reason", default="stop_hit", help="exit_reason-Filter")
    ap.add_argument("--history-weeks", type=int, default=156)
    args = ap.parse_args()

    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())

    with open(args.json, encoding="utf-8") as fh:
        data = json.load(fh)
    closed = data.get("closed", [])
    trades = pd.DataFrame([t for t in closed if t.get("exit_reason") == args.reason])
    if trades.empty:
        print(f"Keine Trades mit exit_reason={args.reason!r} in {args.json}.")
        return 0

    hist = load_history(args.history_weeks, None, False)
    bench = load_benchmark(args.history_weeks, False)
    res = post_stop_returns(trades, hist, bench, horizons)
    if res.empty:
        print("Keine Kursdaten zu den Trades gefunden.")
        return 1

    stop_dist = ((trades["entry_price"] - trades["initial_stop"])
                 / trades["entry_price"] * 100)
    print(f"\n{len(trades)} Trades mit exit_reason={args.reason!r}")
    print(f"Verlust je Trade : Median {trades.realized_plpc.median():6.2f} %")
    print(f"Stop-Abstand     : Median {stop_dist.median():6.1f} % unter Entry")
    print(f"Haltedauer       : Median {res.haltetage.median():6.0f} Tage "
          f"(Minimum {res.haltetage.min():.0f})")
    pat = res["pattern"].value_counts().to_dict()
    print(f"Muster           : {pat}")

    cols = ["symbol", "exit_date", "rs", "realized_plpc", "haltetage"] + \
           [f"ret_{h}w" for h in horizons] + [f"alpha_{h}w" for h in horizons]
    print("\n" + res[[c for c in cols if c in res.columns]]
          .round(1).to_string(index=False))

    print("\nNach dem Stop:")
    metrics = {}
    for h in horizons:
        s = res[f"ret_{h}w"].dropna()
        a = res[f"alpha_{h}w"].dropna()
        if s.empty:
            print(f"  {h:2d}W: noch keine Kurshistorie")
            continue
        print(f"  {h:2d}W (n={len(s):2d}): Titel Median {s.median():6.2f} %  |  "
              f"Alpha Median {a.median():6.2f} %  |  positiv {(a > 0).mean() * 100:3.0f} %")
        metrics.update({f"n_{h}w": len(s), f"ret_med_{h}w": float(s.median()),
                        f"alpha_med_{h}w": float(a.median()) if len(a) else np.nan,
                        f"alpha_pos_pct_{h}w": float((a > 0).mean() * 100) if len(a) else np.nan})

    log_experiment(
        tool="journal_stop_analysis",
        params={"reason": args.reason, "horizons": list(horizons)},
        metrics={**metrics, "trades": len(trades),
                 "stop_dist_median": float(stop_dist.median()),
                 "haltetage_median": float(res.haltetage.median())},
        context={"json": args.json, "generated": data.get("generated")},
    )

    print("\nWARNUNG zur Interpretation: Das ist eine andere Grundgesamtheit als der")
    print("VCP-Backtest — hier ueberwiegen musterlose Ausbrueche mit sehr hoher RS.")
    print("Bei einstelligem n taugt das als Alarmsignal, nicht als Beweis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
