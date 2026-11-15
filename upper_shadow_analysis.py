"""Prueft, ob eine lange obere Lunte (Abweisung an den Hochs) in der Entry-
Woche etwas ueber den spaeteren Trade-Ausgang aussagt.

Anlass (2026-11-15): Chart-Lesart von SBH (Kaufkandidat Rang 2) -- die
letzte Wochenkerze vor dem geplanten Kauf (O 18,69 H 21,06 L 18,30 C 18,96)
hat einen winzigen Koerper (0,27 $) und eine riesige obere Lunte (2,10 $,
~76 % der Wochenspanne): Kaeufer trieben den Kurs bis 21,06 $, Verkaeufer
druecketen ihn fast bis zur Eroeffnung zurueck. Klassisches Abweisungsmuster
(long upper shadow / Shooting-Star-Charakter) nach einem bereits
ausgedehnten Lauf -- ein moegliches Warnsignal, aber bisher reine
Chart-Intuition, keine geprueft Regel.

Definition je Entry-Woche (dieselbe Wochenaggregation wie
screener.compute_minervini_template, W-FRI, aber zusaetzlich mit Open):
  rng               = High - Low
  body              = |Close - Open|
  upper_shadow      = High - max(Open, Close)
  lower_shadow      = min(Open, Close) - Low
  upper_shadow_ratio = upper_shadow / rng   (0 = keine Lunte, 1 = reine Lunte)
  body_ratio         = body / rng           (klein = unentschlossene Kerze)

Lookahead-frei: nur Handelstage VOR dem Entry-Datum fliessen in die
Wochenaggregation ein (die Woche, deren Freitagsschluss das Signal
ausgeloest hat).

Bewertet wird gegen `realized_plpc` der GESCHLOSSENEN Journal-Trades
(n~40, siehe project_analysis_tooling zur Stichproben-Falle). Offene
Trades werden separat mit unrealisierter Rendite gezeigt, fliessen aber
NICHT in Korrelation/Median-Split ein.

  python upper_shadow_analysis.py
  python upper_shadow_analysis.py --shadow-threshold 0.5
  python upper_shadow_analysis.py --json docs/data/trades.json
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

CACHE_NAME = "upper_shadow_ohlc.pkl"


def load_ohlc(tickers: list[str], refresh: bool) -> dict[str, pd.DataFrame]:
    """Tages-OHLC (inkl. Open) nur fuer die gebrauchten Ticker, gecacht.

    Eigener Cache statt `atr_stop_analysis.load_ohlc`, weil der kein Open
    mitfuehrt (ATR braucht nur High/Low/Close) -- die Lunten-Kennzahl hier
    aber schon.
    """
    d = Path(SETTINGS.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / CACHE_NAME

    cached: dict[str, pd.DataFrame] = {}
    if path.exists() and not refresh:
        age = (time.time() - path.stat().st_mtime) / 86400
        if age <= CACHE_TTL_DAYS:
            with path.open("rb") as fh:
                cached = pickle.load(fh)
            missing = [t for t in tickers if t not in cached]
            if not missing:
                print(f"[CACHE] {len(cached)} OHLC-Serien aus {path.name}")
                return {t: cached[t] for t in tickers if t in cached}
            print(f"[CACHE] {len(cached)} vorhanden, {len(missing)} fehlen — lade nach")
            tickers = missing

    batched = download_ohlcv_batched(tickers=tickers, period="3y", interval="1d",
                                     chunk_size=40, auto_adjust=False, threads=False)
    for t, sub in batched.items():
        if sub is None or sub.empty:
            continue
        if not {"Open", "High", "Low", "Close"}.issubset(sub.columns):
            continue
        s = sub[["Open", "High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
        if not s.empty:
            cached[t] = s

    with path.open("wb") as fh:
        pickle.dump(cached, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[CACHE] {len(cached)} OHLC-Serien in {path.name}")
    return cached


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    d = df.copy()
    d.index = idx
    return d.sort_index()


def upper_shadow_for_trade(symbol: str, entry_date: pd.Timestamp,
                           ohlc: dict[str, pd.DataFrame]) -> dict:
    out = {"symbol": symbol, "entry_date": entry_date.date(),
          "open": np.nan, "high": np.nan, "low": np.nan, "close": np.nan,
          "upper_shadow_ratio": np.nan, "body_ratio": np.nan, "status": None}

    df = ohlc.get(symbol)
    if df is None or df.empty:
        out["status"] = "keine_daten"
        return out

    df = _clean(df)
    before_entry = df[df.index < entry_date]
    if before_entry.empty:
        out["status"] = "keine_daten_vor_entry"
        return out

    dfw = before_entry.resample("W-FRI").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
    }).dropna()
    if dfw.empty:
        out["status"] = "zu_kurze_historie"
        return out

    last = dfw.iloc[-1]
    o, h, l, c = float(last["Open"]), float(last["High"]), float(last["Low"]), float(last["Close"])
    rng = h - l

    out["open"], out["high"], out["low"], out["close"] = o, h, l, c
    if rng <= 0:
        out["status"] = "keine_spanne"
        return out

    upper_shadow = h - max(o, c)
    body = abs(c - o)
    out["upper_shadow_ratio"] = round(upper_shadow / rng, 4)
    out["body_ratio"] = round(body / rng, 4)
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
    ap.add_argument("--shadow-threshold", type=float, default=0.5,
                    help="Ab welchem upper_shadow_ratio eine Entry-Woche als 'Abweisungskerze' gilt (Default 0.5).")
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
        res = upper_shadow_for_trade(r["symbol"], pd.Timestamp(r["entry_date"]), ohlc)
        res["realized_plpc"] = r.get("realized_plpc")
        res["exit_reason"] = r.get("exit_reason")
        res["pattern"] = r.get("pattern")
        rows.append(res)
    res_df = pd.DataFrame(rows)

    print(f"=== Geschlossene Trades (n={len(res_df)}) ===")
    print("Lunten-Kennzahl der Entry-Woche (W-FRI), lookahead-frei zum Entry-Tag.\n")

    status_counts = res_df["status"].value_counts()
    print("Status:")
    for s, c in status_counts.items():
        print(f"  {s}: {c}")
    print()

    ok = res_df[res_df["status"] == "ok"].copy()
    if ok.empty:
        print("Keine auswertbaren Trades (zu wenig Historie).")
    else:
        cols = ["symbol", "entry_date", "upper_shadow_ratio", "body_ratio",
               "realized_plpc", "exit_reason"]
        print(ok[cols].sort_values("upper_shadow_ratio", ascending=False).to_string(index=False))
        print()

        pearson = ok["upper_shadow_ratio"].corr(ok["realized_plpc"])
        spearman = ok["upper_shadow_ratio"].rank().corr(ok["realized_plpc"].rank())
        print(f"Korrelation upper_shadow_ratio <-> realized_plpc: "
             f"Pearson={pearson:.3f}  Spearman={spearman:.3f}  (n={len(ok)})")

        abweisung = ok[ok["upper_shadow_ratio"] >= args.shadow_threshold]
        sauber = ok[ok["upper_shadow_ratio"] < args.shadow_threshold]
        print(f"\nMedian-Split bei upper_shadow_ratio >= {args.shadow_threshold}:")
        print(f"  Abweisungskerze (n={len(abweisung)}):  median plpc = "
             f"{abweisung['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(abweisung['realized_plpc'] > 0).mean()*100:.0f}%")
        print(f"  Saubere Kerze   (n={len(sauber)}):  median plpc = "
             f"{sauber['realized_plpc'].median():.2f}%  "
             f"Trefferquote(>0%) = {(sauber['realized_plpc'] > 0).mean()*100:.0f}%")

        log_experiment(
            tool="upper_shadow_analysis",
            params={"shadow_threshold": args.shadow_threshold},
            metrics={
                "n_ok": len(ok), "n_total": len(res_df),
                "pearson": None if pd.isna(pearson) else round(float(pearson), 4),
                "spearman": None if pd.isna(spearman) else round(float(spearman), 4),
                "n_abweisung": len(abweisung),
                "median_plpc_abweisung": None if abweisung.empty else round(float(abweisung["realized_plpc"].median()), 2),
                "median_plpc_sauber": None if sauber.empty else round(float(sauber["realized_plpc"].median()), 2),
                "hitrate_abweisung": None if abweisung.empty else round(float((abweisung["realized_plpc"] > 0).mean()), 3),
                "hitrate_sauber": None if sauber.empty else round(float((sauber["realized_plpc"] > 0).mean()), 3),
            },
            context={"json": args.json, "n_closed": len(closed)},
        )

    if not opened.empty:
        print(f"\n=== Offene Trades (n={len(opened)}) -- nicht in Auswertung, nur zur Einordnung ===")
        orows = []
        for _, r in opened.iterrows():
            res = upper_shadow_for_trade(r["symbol"], pd.Timestamp(r["entry_date"]), ohlc)
            df = ohlc.get(r["symbol"])
            unreal = np.nan
            if df is not None and not df.empty:
                last_close = float(_clean(df)["Close"].iloc[-1])
                unreal = (last_close / float(r["entry_price"]) - 1) * 100
            res["unrealized_plpc"] = unreal
            orows.append(res)
        odf = pd.DataFrame(orows)
        print(odf[["symbol", "entry_date", "upper_shadow_ratio", "body_ratio",
                  "unrealized_plpc", "status"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
