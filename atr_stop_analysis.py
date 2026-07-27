"""Schlüsselt Frühausstoppungen nach der Volatilität des Titels auf.

Anlass (2026-07-27): Im Tradetagebuch wurden 5 von 16 Positionen nach 2–6 Tagen
ausgestoppt (MU nach 2 Tagen, VICR nach 2, RVMD nach 4), und ausgerechnet diese
Titel liefen danach stark (Alpha-Median +12,8 % nach 4 Wochen). Verdacht: Der
Stop liegt innerhalb der normalen Schwankungsbreite dieser Titel, trifft also
Rauschen statt gescheiterter Trades.

`signal_generator._stop_vcp` setzt den Stop auf 2× ATR unter dem Breakout-Level
(Floor 15 % darunter), gedeckelt durch `rules.json: max_stop_pct = 20`. ATR ist
dabei ATR14 auf TAGESdaten. Bei einem Titel mit 5 % Tages-ATR sind 2× ATR die
normale Zwei-Tages-Bewegung — die Frage ist also, ob die Stop-Weite mit der
Volatilität mitwächst oder ob volatile Titel systematisch früher rausfliegen.

Dieses Tool misst auf den Backtest-Entries (ordentliches n, im Gegensatz zu den
9 auswertbaren Journal-Trades):
  * ATR14 % zum Zeitpunkt des Entries
  * den Stop nach Systemregel und wann er triggert (Woche 1 / 2–4 / 5–13)
  * was die früh Ausgestoppten danach gemacht hätten

Tages-OHLC wird nur für die betroffenen Ticker geladen (nicht fürs Universum)
und gecacht.

  python atr_stop_analysis.py --csv .cache/vcp_forward.csv
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
from vcp_universe_check import CACHE_TTL_DAYS, load_history

ATR_WINDOW = 14
STOP_ATR_MULT = 2.0        # signal_generator._stop_vcp
STOP_FLOOR_PCT = 0.15      # ebenda: nie mehr als 15 % unter Breakout
STOP_CAP_PCT = 0.20        # rules.json: max_stop_pct


def load_ohlc(tickers: list[str], refresh: bool) -> dict[str, pd.DataFrame]:
    """Tages-OHLC nur für die gebrauchten Ticker — ATR braucht High/Low, die im
    universumsweiten Tages-Cache (nur Close/Volume) nicht enthalten sind.
    """
    d = Path(SETTINGS.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "atr_ohlc.pkl"

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
        if not {"High", "Low", "Close"}.issubset(sub.columns):
            continue
        s = sub[["High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
        if not s.empty:
            cached[t] = s

    with path.open("wb") as fh:
        pickle.dump(cached, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[CACHE] {len(cached)} OHLC-Serien in {path.name}")
    return cached


def atr_pct_at(ohlc: pd.DataFrame, date: pd.Timestamp) -> float:
    """ATR14 in % des Schlusskurses, Stand `date` — exakt wie screener.py, und
    strikt nur mit Daten BIS zu diesem Tag.
    """
    idx = pd.to_datetime(ohlc.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    sub = ohlc[idx <= pd.Timestamp(date)]
    if len(sub) < ATR_WINDOW + 1:
        return float("nan")

    high, low, close = sub["High"], sub["Low"], sub["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(ATR_WINDOW).mean().iloc[-1]
    last = float(close.iloc[-1])
    if not np.isfinite(atr) or last <= 0:
        return float("nan")
    return float(atr) / last * 100.0


def system_stop_pct(atr_pct: float) -> float:
    """Stop-Abstand in Prozent nach Systemregel (Näherung: Entry ≈ Breakout,
    der Buy-Stop-Puffer betraegt nur 0,1 %).
    """
    if not np.isfinite(atr_pct):
        return float("nan")
    pct = STOP_ATR_MULT * atr_pct / 100.0
    pct = min(pct, STOP_FLOOR_PCT)     # _stop_vcp: Floor 15 % unter Breakout
    return min(pct, STOP_CAP_PCT)


def analyse(entries: pd.DataFrame, hist: dict[str, pd.DataFrame],
            ohlc: dict[str, pd.DataFrame], horizon: int) -> pd.DataFrame:
    rows = []
    for _, r in entries.iterrows():
        sym = r["ticker"]
        wk, oh = hist.get(sym), ohlc.get(sym)
        if wk is None or oh is None:
            continue
        idx = pd.to_datetime(wk.index)
        pos = int(idx.searchsorted(pd.Timestamp(r["date"])))
        if pos + horizon >= len(wk):
            continue

        entry_px = float(r["entry_px"])
        atr = atr_pct_at(oh, pd.Timestamp(r["date"]))
        stop_pct = system_stop_pct(atr)
        if not np.isfinite(stop_pct) or entry_px <= 0:
            continue

        low = wk["Low"].astype(float).iloc[pos + 1:pos + horizon + 1]
        close = wk["Close"].astype(float).iloc[pos + 1:pos + horizon + 1]
        stop_px = entry_px * (1 - stop_pct)

        below = np.where(low.to_numpy() <= stop_px)[0]
        trig_week = int(below[0]) + 1 if len(below) else None

        rows.append({
            "ticker": sym, "date": r["date"], "atr_pct": atr,
            "stop_pct": stop_pct * 100,
            "trig_week": trig_week,
            "frueh": bool(trig_week == 1),
            "ret": float(close.iloc[-1] / entry_px - 1) * 100,
        })
    return pd.DataFrame(rows)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=".cache/vcp_forward.csv")
    ap.add_argument("--horizon", type=int, default=13)
    ap.add_argument("--journal", default="docs/data/trades.json")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    entries = pd.read_csv(args.csv).drop_duplicates(subset=["ticker", "date"])
    hist = load_history(156, None, False)
    ohlc = load_ohlc(sorted(entries["ticker"].unique()), args.refresh)

    res = analyse(entries, hist, ohlc, args.horizon)
    if res.empty:
        print("[ERROR] Keine auswertbaren Entries.")
        return 1

    print(f"\n{len(res)} Entries mit ATR und {args.horizon}W Zukunft")
    print(f"ATR14: Median {res.atr_pct.median():.2f} %   "
          f"Q25 {res.atr_pct.quantile(.25):.2f} %   Q75 {res.atr_pct.quantile(.75):.2f} %")
    print(f"Stop nach Systemregel: Median {res.stop_pct.median():.1f} % unter Entry "
          f"(Cap greift bei ATR > {STOP_FLOOR_PCT * 100 / STOP_ATR_MULT:.1f} %)")

    print("\n" + "=" * 76)
    print("Frueh-Ausstoppung (Woche 1) nach ATR-Quartil")
    print("=" * 76)
    res["atr_q"] = pd.qcut(res["atr_pct"], 4,
                           labels=["Q1 ruhig", "Q2", "Q3", "Q4 volatil"])
    print(f"{'ATR-Quartil':<12} {'n':>4} {'ATR-Ø':>7} {'Stop-Ø':>8} "
          f"{'Woche-1-Stop':>13} {'je gestoppt':>12} {'Ø Rendite':>10}")
    print("-" * 76)
    for q, g in res.groupby("atr_q", observed=True):
        gestoppt = g["trig_week"].notna()
        print(f"{str(q):<12} {len(g):>4} {g.atr_pct.mean():>6.2f}% "
              f"{g.stop_pct.mean():>7.1f}% {g.frueh.mean() * 100:>12.0f}% "
              f"{gestoppt.mean() * 100:>11.0f}% {g.ret.mean():>9.2f}%")

    print("\n" + "=" * 76)
    print("Was haetten die Frueh-Ausgestoppten (Woche 1) danach gemacht?")
    print("=" * 76)
    fr, rest = res[res.frueh], res[~res.frueh]
    if not fr.empty:
        print(f"  frueh gestoppt (n={len(fr):3d}): {args.horizon}W-Rendite "
              f"Median {fr.ret.median():6.2f} %  Ø {fr.ret.mean():6.2f} %  "
              f"positiv {(fr.ret > 0).mean() * 100:3.0f} %")
    print(f"  uebrige       (n={len(rest):3d}): {args.horizon}W-Rendite "
          f"Median {rest.ret.median():6.2f} %  Ø {rest.ret.mean():6.2f} %  "
          f"positiv {(rest.ret > 0).mean() * 100:3.0f} %")

    # Gegenprobe am Journal: dieselbe Rechnung auf den echten Trades
    try:
        with open(args.journal, encoding="utf-8") as fh:
            data = json.load(fh)
        jt = pd.DataFrame([t for t in data.get("closed", [])
                           if t.get("exit_reason") == "stop_hit"])
        if not jt.empty:
            jt["entry_date"] = pd.to_datetime(jt["entry_date"])
            jo = load_ohlc(sorted(jt["symbol"].unique()), False)
            jt["atr_pct"] = [atr_pct_at(jo[s], d) if s in jo else np.nan
                             for s, d in zip(jt.symbol, jt.entry_date)]
            jt["haltetage"] = (pd.to_datetime(jt.exit_date) - jt.entry_date).dt.days
            print("\n" + "=" * 76)
            print("Gegenprobe: die realen Trades aus dem Journal")
            print("=" * 76)
            print(jt[["symbol", "atr_pct", "haltetage", "realized_plpc"]]
                  .sort_values("haltetage").round(2).to_string(index=False))
            fast = jt[jt.haltetage <= 7]
            if not fast.empty:
                print(f"\n  <= 7 Tage gehalten (n={len(fast)}): ATR-Median "
                      f"{fast.atr_pct.median():.2f} %  |  laenger gehalten: "
                      f"{jt[jt.haltetage > 7].atr_pct.median():.2f} %")
    except Exception as e:
        print(f"\n[WARN] Journal-Gegenprobe uebersprungen: {e}")

    log_experiment(
        tool="atr_stop_analysis",
        params={"horizon": args.horizon, "stop_atr_mult": STOP_ATR_MULT,
                "stop_floor_pct": STOP_FLOOR_PCT},
        metrics={"n": len(res), "atr_median": float(res.atr_pct.median()),
                 "stop_pct_median": float(res.stop_pct.median()),
                 "week1_stop_pct": float(res.frueh.mean() * 100),
                 "frueh_ret_median": float(fr.ret.median()) if not fr.empty else np.nan,
                 "rest_ret_median": float(rest.ret.median())},
        context={"csv": args.csv},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
