"""Prueft, ob Distribution-Days vor dem Entry etwas ueber den Trade-Ausgang aussagen.

Anlass (2026-09-06): Das TraderLion Model Book (2018-2020, 2023-2024) zeigt in
jedem annotierten Chart dieselbe Volumen-Lesart: Farbe der Volumenbalken nach
Up-/Down-Tag UND relativ zum Durchschnitt -- "easy to see that upside volume
is above average while downside volume is below average, a sign of
accumulation by institutional investors" (UPST-Chart). Umgekehrt gilt ein
Down-Tag mit ueberdurchschnittlichem Volumen ("Distribution Day", klassisches
O'Neil-Konzept) als Warnsignal fuer institutionelles Verkaufen.

Definition je Handelstag t (Einzeltitel, nicht Index):
  * avg_vol[t]      = 20-Tage-Rolling-Mean(Volume)  -- dieselbe MA20-Volumen-
                       Konvention wie `industry_strength._compute_volume_
                       activity_direction` (Konsistenz mit dem Rest des Systems)
  * distribution_day: Close[t] < Close[t-1]  UND  Volume[t] > avg_vol[t]
  * accumulation_day: Close[t] > Close[t-1]  UND  Volume[t] > avg_vol[t]

Gezaehlt wird ueber die `--window` Handelstage VOR dem Entry (Default 20 ~ 4
Wochen, nahe am klassischen O'Neil-Zaehlfenster von 25 Tagen), der Entry-Tag
selbst zaehlt nicht mit (er ist per Konstruktion fast immer ein Accumulation-
Day, das waere zirkulaer).

Kennzahlen je Trade:
  * dist_days   -- Anzahl Distribution Days im Fenster
  * accum_days  -- Anzahl Accumulation Days im Fenster
  * net_days    -- accum_days - dist_days (positiv = Nettoakkumulation)

Bewertet wird gegen `realized_plpc` der GESCHLOSSENEN Journal-Trades (n≈23,
also mit Vorsicht -- siehe project_analysis_tooling zur Stichproben-Falle).
Offene Trades werden separat mit unrealisierter Rendite gezeigt, fliessen
aber NICHT in Korrelation/Median-Split ein.

  python distribution_day_analysis.py
  python distribution_day_analysis.py --window 25
  python distribution_day_analysis.py --json docs/data/trades.json
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from config import SETTINGS
from data_sources import download_ohlcv_batched
from experiment_log import log_experiment
from vcp_universe_check import CACHE_TTL_DAYS

VOL_MA_WINDOW = 20


def load_close_volume(tickers: list[str], refresh: bool) -> dict[str, pd.DataFrame]:
    """Tages-Close+Volume nur fuer die gebrauchten Ticker, gecacht analog zu
    `atr_stop_analysis.load_ohlc` (das nur High/Low/Close liefert, kein Volume).
    """
    d = Path(SETTINGS.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "distday_close_volume.pkl"

    cached: dict[str, pd.DataFrame] = {}
    if path.exists() and not refresh:
        age = (time.time() - path.stat().st_mtime) / 86400
        if age <= CACHE_TTL_DAYS:
            with path.open("rb") as fh:
                cached = pickle.load(fh)
            missing = [t for t in tickers if t not in cached]
            if not missing:
                print(f"[CACHE] {len(cached)} Close/Volume-Serien aus {path.name}")
                return {t: cached[t] for t in tickers if t in cached}
            print(f"[CACHE] {len(cached)} vorhanden, {len(missing)} fehlen — lade nach")
            tickers = missing

    batched = download_ohlcv_batched(tickers=tickers, period="3y", interval="1d",
                                     chunk_size=40, auto_adjust=False, threads=False)
    for t, sub in batched.items():
        if sub is None or sub.empty:
            continue
        if not {"Close", "Volume"}.issubset(sub.columns):
            continue
        s = sub[["Close", "Volume"]].apply(pd.to_numeric, errors="coerce").dropna()
        if not s.empty:
            cached[t] = s

    with path.open("wb") as fh:
        pickle.dump(cached, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[CACHE] {len(cached)} Close/Volume-Serien in {path.name}")
    return cached


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    d = df.copy()
    d.index = idx
    return d.sort_index()


def dist_days_for_trade(symbol: str, entry_date: pd.Timestamp,
                        data: dict[str, pd.DataFrame], window: int) -> dict:
    out = {"symbol": symbol, "entry_date": entry_date.date(),
          "dist_days": np.nan, "accum_days": np.nan, "net_days": np.nan,
          "status": None}

    df = data.get(symbol)
    if df is None or df.empty:
        out["status"] = "keine_daten"
        return out

    df = _clean(df)
    close = df["Close"]
    vol = df["Volume"]
    avg_vol = vol.rolling(VOL_MA_WINDOW).mean()

    prior = close.shift(1)
    is_down = close < prior
    is_up = close > prior
    above_avg = vol > avg_vol

    dist_flag = (is_down & above_avg)
    accum_flag = (is_up & above_avg)

    before_entry = df.index < entry_date
    idx = df.index[before_entry]
    if len(idx) < window + VOL_MA_WINDOW:
        out["status"] = "zu_kurze_historie"
        return out

    win_idx = idx[-window:]
    d = int(dist_flag.reindex(win_idx).sum())
    a = int(accum_flag.reindex(win_idx).sum())

    out["dist_days"] = d
    out["accum_days"] = a
    out["net_days"] = a - d
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
    ap.add_argument("--window", type=int, default=20,
                    help="Handelstage vor Entry, in denen gezaehlt wird (Default 20 ~ 4 Wochen).")
    ap.add_argument("--dist-threshold", type=int, default=4,
                    help="Ab wie vielen Distribution Days ein Trade als 'belastet' gilt (Default 4).")
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
    cv = load_close_volume(all_symbols, args.refresh)

    rows = []
    for _, r in closed.iterrows():
        res = dist_days_for_trade(r["symbol"], pd.Timestamp(r["entry_date"]), cv, args.window)
        res["realized_plpc"] = r.get("realized_plpc")
        res["exit_reason"] = r.get("exit_reason")
        res["pattern"] = r.get("pattern")
        rows.append(res)
    res_df = pd.DataFrame(rows)

    print(f"=== Geschlossene Trades (n={len(res_df)}) ===")
    print(f"Fenster: {args.window} Handelstage vor Entry, Volumen-MA: {VOL_MA_WINDOW} Tage\n")

    status_counts = res_df["status"].value_counts()
    print("Status:")
    for s, c in status_counts.items():
        print(f"  {s}: {c}")
    print()

    ok = res_df[res_df["status"] == "ok"].copy()
    if ok.empty:
        print("Keine auswertbaren Trades (zu wenig Historie).")
    else:
        cols = ["symbol", "entry_date", "dist_days", "accum_days", "net_days",
               "realized_plpc", "exit_reason"]
        print(ok[cols].sort_values("dist_days", ascending=False).to_string(index=False))
        print()

        for col in ("dist_days", "net_days"):
            pearson = ok[col].corr(ok["realized_plpc"])
            spearman = ok[col].rank().corr(ok["realized_plpc"].rank())
            print(f"Korrelation {col} <-> realized_plpc: Pearson={pearson:.3f}  Spearman={spearman:.3f}  (n={len(ok)})")

        belastet = ok[ok["dist_days"] >= args.dist_threshold]
        sauber = ok[ok["dist_days"] < args.dist_threshold]
        print(f"\nMedian-Split bei dist_days >= {args.dist_threshold}:")
        print(f"  Belastet (n={len(belastet)}):  median plpc = {belastet['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(belastet['realized_plpc'] > 0).mean()*100:.0f}%")
        print(f"  Sauber   (n={len(sauber)}):  median plpc = {sauber['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(sauber['realized_plpc'] > 0).mean()*100:.0f}%")

        log_experiment(
            tool="distribution_day_analysis",
            params={"window": args.window, "vol_ma_window": VOL_MA_WINDOW,
                   "dist_threshold": args.dist_threshold},
            metrics={
                "n_ok": len(ok), "n_total": len(res_df),
                "pearson_dist": None if pd.isna(ok["dist_days"].corr(ok["realized_plpc"])) else round(float(ok["dist_days"].corr(ok["realized_plpc"])), 4),
                "pearson_net": None if pd.isna(ok["net_days"].corr(ok["realized_plpc"])) else round(float(ok["net_days"].corr(ok["realized_plpc"])), 4),
                "median_plpc_belastet": None if belastet.empty else round(float(belastet["realized_plpc"].median()), 2),
                "median_plpc_sauber": None if sauber.empty else round(float(sauber["realized_plpc"].median()), 2),
                "hitrate_belastet": None if belastet.empty else round(float((belastet["realized_plpc"] > 0).mean()), 3),
                "hitrate_sauber": None if sauber.empty else round(float((sauber["realized_plpc"] > 0).mean()), 3),
            },
            context={"json": args.json, "n_closed": len(closed)},
        )

    if not opened.empty:
        print(f"\n=== Offene Trades (n={len(opened)}) -- nicht in Korrelation, nur zur Einordnung ===")
        orows = []
        for _, r in opened.iterrows():
            res = dist_days_for_trade(r["symbol"], pd.Timestamp(r["entry_date"]), cv, args.window)
            df = cv.get(r["symbol"])
            unreal = np.nan
            if df is not None and not df.empty:
                last_close = float(_clean(df)["Close"].iloc[-1])
                unreal = (last_close / float(r["entry_price"]) - 1) * 100
            res["unrealized_plpc"] = unreal
            orows.append(res)
        odf = pd.DataFrame(orows)
        print(odf[["symbol", "entry_date", "dist_days", "accum_days", "net_days",
                  "unrealized_plpc", "status"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
