"""Prueft, ob eine fruehe RS-Linien-Divergenz vor dem Kaufsignal etwas ueber
den spaeteren Trade-Ausgang aussagt.

Anlass (2026-09-06): Das TraderLion Model Book (2018-2020, 2023-2024, ~40 der
staerksten Marktfuehrer der letzten Jahre) zeigt bei fast jeder Aktie dasselbe
Muster in den Analysten-Annotationen: Die RS-Linie (Kurs/SPY-Verhaeltnis)
macht ein neues Mehrmonatshoch, WAEHREND der Kurs selbst noch unter seinem
alten Hoch liegt -- "Positive RS Divergence at the beginning of the move"
(UPST), "RS line started to make new all-time highs a long time before price
did" (AMZN). Das ist etwas anderes als das im System vorhandene RS-Rating
(Perzentil ggue. Universum, siehe project_rs_min_history) -- es geht um die
zeitliche Reihenfolge: outperformt die Aktie den Markt schon VOR dem
Ausbruch, oder erst gleichzeitig mit ihm?

Definition (parametrisierbar):
  * rs_line[t]  = Close[t] / SPY_Close[t]
  * "Fenster-Hoch" an Tag t: der Wert an t ist das Maximum der letzten
    `--roll-window` Handelstage (rollierendes Hoch, Ende t).
  * Im Zeitraum von `--pre-days` Handelstagen vor dem Entry wird je Serie
    (Kurs und RS-Linie) der FRUEHESTE Tag gesucht, an dem ein Fenster-Hoch
    erreicht wird.
  * rs_lead_days = price_high_day - rs_high_day (in Handelstagen).
    Positiv  -> RS-Linie hat ihr Fenster-Hoch VOR dem Kurs erreicht
                (Divergenz wie im Model Book beschrieben).
    Negativ  -> Kurs war zuerst am Hoch, RS-Linie zieht erst nach
                (Outperformance ist reine Ausbruchsfolge, keine Vorwarnung).
    None     -> eine der beiden Serien erreichte im Fenster gar kein neues
                Hoch (z.B. weil der Titel schon vorher am ATH war).

Bewertet wird gegen `realized_plpc` der GESCHLOSSENEN Journal-Trades (n≈23,
also mit Vorsicht -- siehe project_analysis_tooling zur Stichproben-Falle).
Offene Trades werden separat mit unrealisierter Rendite seit Entry gezeigt,
flossen aber NICHT in Korrelation/Median-Split ein.

  python rs_line_divergence_analysis.py
  python rs_line_divergence_analysis.py --pre-days 40 --roll-window 60
  python rs_line_divergence_analysis.py --json docs/data/trades.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from atr_stop_analysis import load_ohlc
from experiment_log import log_experiment

BENCHMARK = "SPY"


def _clean_close(df: pd.DataFrame) -> pd.Series:
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s = df["Close"].astype(float)
    s.index = idx
    return s.sort_index()


def _first_window_high_day(series: pd.Series, roll_window: int) -> int | None:
    """Index (Position) des fruehesten Tages, an dem `series` ein rollierendes
    `roll_window`-Hoch erreicht -- innerhalb der uebergebenen (bereits auf das
    Pre-Entry-Fenster zugeschnittenen) Serie.
    """
    if len(series) < roll_window:
        return None
    roll_max = series.rolling(roll_window, min_periods=roll_window).max()
    hits = np.where(series.values >= roll_max.values - 1e-9)[0]
    hits = hits[hits >= roll_window - 1]
    if len(hits) == 0:
        return None
    return int(hits[0])


def rs_lead_for_trade(symbol: str, entry_date: pd.Timestamp,
                      ohlc: dict[str, pd.DataFrame], pre_days: int,
                      roll_window: int) -> dict:
    out = {"symbol": symbol, "entry_date": entry_date.date(),
          "rs_lead_days": np.nan, "price_high_day": pd.NaT,
          "rs_high_day": pd.NaT, "status": None}

    df = ohlc.get(symbol)
    bench = ohlc.get(BENCHMARK)
    if df is None or bench is None or df.empty or bench.empty:
        out["status"] = "keine_daten"
        return out

    close = _clean_close(df)
    spy = _clean_close(bench)
    common = close.index.intersection(spy.index)
    close = close.reindex(common)
    spy = spy.reindex(common)
    rs_line = close / spy

    lookback_start = entry_date - pd.Timedelta(days=int((pre_days + roll_window) * 1.6) + 20)
    window_idx = common[(common >= lookback_start) & (common < entry_date)]
    if len(window_idx) < roll_window + 5:
        out["status"] = "zu_kurze_historie"
        return out

    price_w = close.reindex(window_idx).tail(pre_days + roll_window)
    rs_w = rs_line.reindex(window_idx).tail(pre_days + roll_window)

    p_day = _first_window_high_day(price_w, roll_window)
    r_day = _first_window_high_day(rs_w, roll_window)

    if p_day is None or r_day is None:
        out["status"] = "kein_fenster_hoch"
        return out

    out["rs_lead_days"] = p_day - r_day
    out["price_high_day"] = price_w.index[p_day].date()
    out["rs_high_day"] = rs_w.index[r_day].date()
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
    ap.add_argument("--pre-days", type=int, default=40,
                    help="Handelstage vor Entry, die nach dem fruehesten Fenster-Hoch durchsucht werden (Default 40 ~ 8 Wochen).")
    ap.add_argument("--roll-window", type=int, default=60,
                    help="Fenstergroesse fuer das rollierende Hoch in Handelstagen (Default 60 ~ 3 Monate).")
    ap.add_argument("--lead-threshold", type=int, default=5,
                    help="Ab wie vielen Handelstagen Vorsprung eine Divergenz als 'RS fuehrt' zaehlt (Default 5 ~ 1 Woche).")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as fh:
        data = json.load(fh)
    closed = pd.DataFrame(data.get("closed", []))
    opened = pd.DataFrame(data.get("open", []))
    if closed.empty:
        print(f"Keine geschlossenen Trades in {args.json}.")
        return 0

    all_symbols = sorted(set(closed["symbol"]) | set(opened.get("symbol", pd.Series(dtype=str))) | {BENCHMARK})
    ohlc = load_ohlc(all_symbols, args.refresh)

    rows = []
    for _, r in closed.iterrows():
        res = rs_lead_for_trade(r["symbol"], pd.Timestamp(r["entry_date"]),
                                ohlc, args.pre_days, args.roll_window)
        res["realized_plpc"] = r.get("realized_plpc")
        res["exit_reason"] = r.get("exit_reason")
        res["pattern"] = r.get("pattern")
        res["rs_score"] = r.get("rs_score")
        rows.append(res)
    res_df = pd.DataFrame(rows)

    print(f"=== Geschlossene Trades (n={len(res_df)}) ===")
    print(f"Pre-Entry-Fenster: {args.pre_days} Handelstage, Roll-Fenster: {args.roll_window} Handelstage\n")

    status_counts = res_df["status"].value_counts()
    print("Status:")
    for s, c in status_counts.items():
        print(f"  {s}: {c}")
    print()

    ok = res_df[res_df["status"] == "ok"].copy()
    if ok.empty:
        print("Keine auswertbare Trades (zu wenig Historie / keine Fenster-Hochs gefunden).")
    else:
        cols = ["symbol", "entry_date", "rs_lead_days", "rs_high_day",
               "price_high_day", "realized_plpc", "exit_reason"]
        print(ok[cols].sort_values("rs_lead_days", ascending=False).to_string(index=False))
        print()

        leads = ok["rs_lead_days"]
        pl = ok["realized_plpc"]
        corr = leads.corr(pl)
        corr_s = leads.rank().corr(pl.rank())
        print(f"Korrelation rs_lead_days <-> realized_plpc: Pearson={corr:.3f}  Spearman={corr_s:.3f}  (n={len(ok)})")

        divergent = ok[ok["rs_lead_days"] >= args.lead_threshold]
        non_divergent = ok[ok["rs_lead_days"] < args.lead_threshold]
        print(f"\nMedian-Split bei rs_lead_days >= {args.lead_threshold} Handelstage:")
        print(f"  RS fuehrt (n={len(divergent)}):     median plpc = {divergent['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(divergent['realized_plpc'] > 0).mean()*100:.0f}%")
        print(f"  RS fuehrt NICHT (n={len(non_divergent)}): median plpc = {non_divergent['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(non_divergent['realized_plpc'] > 0).mean()*100:.0f}%")

        log_experiment(
            tool="rs_line_divergence_analysis",
            params={"pre_days": args.pre_days, "roll_window": args.roll_window,
                   "lead_threshold": args.lead_threshold},
            metrics={
                "n_ok": len(ok), "n_total": len(res_df),
                "pearson_corr": None if pd.isna(corr) else round(float(corr), 4),
                "spearman_corr": None if pd.isna(corr_s) else round(float(corr_s), 4),
                "median_plpc_divergent": None if divergent.empty else round(float(divergent["realized_plpc"].median()), 2),
                "median_plpc_non_divergent": None if non_divergent.empty else round(float(non_divergent["realized_plpc"].median()), 2),
                "hitrate_divergent": None if divergent.empty else round(float((divergent["realized_plpc"] > 0).mean()), 3),
                "hitrate_non_divergent": None if non_divergent.empty else round(float((non_divergent["realized_plpc"] > 0).mean()), 3),
            },
            context={"json": args.json, "n_closed": len(closed)},
        )

    if not opened.empty:
        print(f"\n=== Offene Trades (n={len(opened)}) -- nicht in Korrelation, nur zur Einordnung ===")
        orows = []
        for _, r in opened.iterrows():
            res = rs_lead_for_trade(r["symbol"], pd.Timestamp(r["entry_date"]),
                                    ohlc, args.pre_days, args.roll_window)
            df = ohlc.get(r["symbol"])
            unreal = np.nan
            if df is not None and not df.empty:
                last_close = float(_clean_close(df).iloc[-1])
                unreal = (last_close / float(r["entry_price"]) - 1) * 100
            res["entry_price"] = r.get("entry_price")
            res["unrealized_plpc"] = unreal
            orows.append(res)
        odf = pd.DataFrame(orows)
        print(odf[["symbol", "entry_date", "rs_lead_days", "unrealized_plpc", "status"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
