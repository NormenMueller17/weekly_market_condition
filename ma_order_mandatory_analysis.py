"""Prueft, ob die MA-Ordnung (SMA10W > SMA30W > SMA40W) als PFLICHTKRITERIUM
statt als einer von 8 Score-Bausteinen etwas gebracht haette.

Anlass (2026-11-15): Externes Feedback bemaengelt, dass ASND die MA-Ordnung
nicht erfuellt und trotzdem ueber "6 von 8" durchkommt -- Empfehlung: MA-
Ordnung zur Pflicht machen. Das ist NICHT dieselbe Frage wie die bereits
getestete Score-Verschaerfung auf 8/8 ([[project_rev_eps_or_logic]], an den
eigenen 28 Trades widerlegt) -- dort mussten ALLE 6 Trendkriterien gleich-
zeitig erfuellt sein, hier geht es NUR um das eine MA-Ordnung-Kriterium
isoliert.

`criteria` in den Journal-/Signal-Daten ist fast immer leer (nur 2 von 166
Eintraegen in docs/data/signals_meta_*.json haben es befuellt) -- die
MA-Ordnung wird daher lookahead-frei aus der Kurshistorie neu berechnet,
exakt nach der Definition in `screener.compute_minervini_template`:
Wochenaggregation (W-FRI), SMA10/30/40 auf dem woechentlichen Close,
ma_order = SMA10 > SMA30 > SMA40 am letzten VOR dem Entry abgeschlossenen
Wochen-Close.

Methode: Median-Split von realized_plpc (geschlossene Trades) zwischen
Trades MIT und OHNE erfuellte MA-Ordnung am Entry-Tag, analog zu
distribution_day_analysis.py / rs_line_divergence_analysis.py.

  python ma_order_mandatory_analysis.py
  python ma_order_mandatory_analysis.py --json docs/data/trades.json
"""
from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd

from atr_stop_analysis import load_ohlc
from experiment_log import log_experiment


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    d = df.copy()
    d.index = idx
    return d.sort_index()


def ma_order_for_trade(symbol: str, entry_date: pd.Timestamp,
                       ohlc: dict[str, pd.DataFrame]) -> dict:
    out = {"symbol": symbol, "entry_date": entry_date.date(),
          "ma_order": None, "sma10": np.nan, "sma30": np.nan, "sma40": np.nan,
          "status": None}

    df = ohlc.get(symbol)
    if df is None or df.empty:
        out["status"] = "keine_daten"
        return out

    df = _clean(df)
    before_entry = df[df.index < entry_date]
    if before_entry.empty:
        out["status"] = "keine_daten_vor_entry"
        return out

    dfw = before_entry.resample("W-FRI").agg({"Close": "last"}).dropna()
    close = dfw["Close"]
    if len(close) < 40:
        out["status"] = "zu_kurze_historie"
        return out

    sma10 = close.rolling(10).mean().iloc[-1]
    sma30 = close.rolling(30).mean().iloc[-1]
    sma40 = close.rolling(40).mean().iloc[-1]

    out["sma10"], out["sma30"], out["sma40"] = float(sma10), float(sma30), float(sma40)
    out["ma_order"] = bool(sma10 > sma30 > sma40)
    out["status"] = "ok"
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default="docs/data/trades.json")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as fh:
        data = json.load(fh)
    closed = pd.DataFrame(data.get("closed", []))
    opened = pd.DataFrame(data.get("open", []))
    if closed.empty:
        print(f"Keine geschlossenen Trades in {args.json}.")
        return 0

    all_symbols = sorted(set(closed["symbol"]) | set(opened.get("symbol", pd.Series(dtype=str))))
    ohlc = load_ohlc(all_symbols, args.refresh)

    rows = []
    for _, r in closed.iterrows():
        res = ma_order_for_trade(r["symbol"], pd.Timestamp(r["entry_date"]), ohlc)
        res["realized_plpc"] = r.get("realized_plpc")
        res["exit_reason"] = r.get("exit_reason")
        res["pattern"] = r.get("pattern")
        rows.append(res)
    res_df = pd.DataFrame(rows)

    print(f"=== Geschlossene Trades (n={len(res_df)}) ===")
    print("MA-Ordnung neu berechnet (W-FRI, SMA10/30/40), lookahead-frei zum Entry-Tag.\n")

    status_counts = res_df["status"].value_counts()
    print("Status:")
    for s, c in status_counts.items():
        print(f"  {s}: {c}")
    print()

    ok = res_df[res_df["status"] == "ok"].copy()
    if ok.empty:
        print("Keine auswertbaren Trades (zu wenig Historie).")
    else:
        cols = ["symbol", "entry_date", "ma_order", "sma10", "sma30", "sma40",
               "realized_plpc", "exit_reason"]
        print(ok[cols].sort_values("ma_order", ascending=False).to_string(index=False))
        print()

        mit = ok[ok["ma_order"] == True]
        ohne = ok[ok["ma_order"] == False]
        print(f"Median-Split MA-Ordnung erfuellt vs. nicht (n={len(ok)}):")
        print(f"  Erfuellt (n={len(mit)}):    median plpc = {mit['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(mit['realized_plpc'] > 0).mean()*100:.0f}%")
        print(f"  NICHT erfuellt (n={len(ohne)}): median plpc = {ohne['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(ohne['realized_plpc'] > 0).mean()*100:.0f}%")
        print(f"\nWaeren bei MA-Ordnung als Pflicht zusaetzlich ausgeschlossen worden: "
             f"{len(ohne)} von {len(ok)} Trades ({len(ohne)/len(ok)*100:.0f}%)")

        log_experiment(
            tool="ma_order_mandatory_analysis",
            params={},
            metrics={
                "n_ok": len(ok), "n_total": len(res_df),
                "n_ohne_ma_order": len(ohne),
                "median_plpc_mit": None if mit.empty else round(float(mit["realized_plpc"].median()), 2),
                "median_plpc_ohne": None if ohne.empty else round(float(ohne["realized_plpc"].median()), 2),
                "hitrate_mit": None if mit.empty else round(float((mit["realized_plpc"] > 0).mean()), 3),
                "hitrate_ohne": None if ohne.empty else round(float((ohne["realized_plpc"] > 0).mean()), 3),
            },
            context={"json": args.json, "n_closed": len(closed)},
        )

    if not opened.empty:
        print(f"\n=== Offene Trades (n={len(opened)}) -- nicht in Auswertung, nur zur Einordnung ===")
        orows = []
        for _, r in opened.iterrows():
            res = ma_order_for_trade(r["symbol"], pd.Timestamp(r["entry_date"]), ohlc)
            df = ohlc.get(r["symbol"])
            unreal = np.nan
            if df is not None and not df.empty:
                last_close = float(_clean(df)["Close"].iloc[-1])
                unreal = (last_close / float(r["entry_price"]) - 1) * 100
            res["unrealized_plpc"] = unreal
            orows.append(res)
        odf = pd.DataFrame(orows)
        print(odf[["symbol", "entry_date", "ma_order", "unrealized_plpc", "status"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
