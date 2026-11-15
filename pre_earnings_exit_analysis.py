"""Prueft, ob Gewinnmitnahme vor Earnings bei bereits laufenden Positionen
etwas gebracht haette.

Anlass (2026-09-06): Das TraderLion Model Book zeigt wiederholt dieselbe
Regel bei bereits gut gelaufenen Positionen: "Enter here, but sell for small
profits since EPS are due" (UPST-Chart) -- Gewinne vor dem Earnings-Report
mitnehmen statt das Gap-Risiko zu tragen. Das ist etwas anderes als der
vorhandene Earnings-Blackout (`signal_generator._filter_earnings_blackout`),
der nur NEUE Einstiege in den 14 Tagen vor dem naechsten Report blockt --
offene Positionen laufen bislang unveraendert durch einen Earnings-Report,
falls die Haltedauer laenger ist als der Blackout ab Entry.

Testmethode:
  1. Historische Earnings-Termine je Ticker ueber `yfinance.get_earnings_dates`
     (gecacht, da Netzwerk-Call pro Ticker).
  2. Fuer jeden Trade: lag ein Earnings-Termin ECHT WAEHREND der Haltedauer
     (entry_date < earnings_date <= exit_date, bei offenen Trades bis heute)?
  3. Falls ja: Kurs am letzten Handelstag VOR dem Report (`pre_earn_close`)
     und am ersten Handelstag DANACH (`post_earn_close`).
       pre_earn_plpc         = (pre_earn_close / entry_price - 1) * 100
       earnings_reaction_pct = (post_earn_close / pre_earn_close - 1) * 100
  4. Kontrafaktisch: Ausstieg am Vortag des Reports, aber NUR wenn die
     Position zu dem Zeitpunkt bereits im Plus war (`pre_earn_plpc > 0`) --
     das ist exakt die im Model Book beschriebene Regel, kein genereller
     Zeitstop. Verglichen wird `pre_earn_plpc` gegen das tatsaechliche
     `realized_plpc` (nur geschlossene Trades).

Kleine Zusatzgruppe: Trades mit Earnings waehrend der Haltedauer, bei denen
die Position VOR dem Report bereits im Verlust war -- fuer die haette die
Regel gar nicht gegriffen (kein "sell for profit"), dienen nur als Kontext.

  python pre_earnings_exit_analysis.py
  python pre_earnings_exit_analysis.py --json docs/data/trades.json --refresh
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from config import SETTINGS
from experiment_log import log_experiment
from vcp_universe_check import CACHE_TTL_DAYS

CACHE_NAME = "earnings_dates.pkl"


def load_earnings_dates(tickers: list[str], refresh: bool) -> dict[str, list[date]]:
    """Historische Earnings-Termine je Ticker (nur das Datum, ohne Uhrzeit),
    absteigend sortiert wie yfinance sie liefert. Gecacht, weil jeder Ticker
    einen eigenen Netzwerk-Call braucht.
    """
    import yfinance as yf

    d = Path(SETTINGS.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / CACHE_NAME

    cached: dict[str, list[date]] = {}
    if path.exists() and not refresh:
        age = (time.time() - path.stat().st_mtime) / 86400
        if age <= CACHE_TTL_DAYS:
            with path.open("rb") as fh:
                cached = pickle.load(fh)
            missing = [t for t in tickers if t not in cached]
            if not missing:
                print(f"[CACHE] {len(cached)} Earnings-Serien aus {path.name}")
                return {t: cached[t] for t in tickers if t in cached}
            print(f"[CACHE] {len(cached)} vorhanden, {len(missing)} fehlen — lade nach")
            tickers = missing

    for t in tickers:
        try:
            ed = yf.Ticker(t).get_earnings_dates(limit=40)
            if ed is None or ed.empty:
                cached[t] = []
                continue
            dates = sorted({idx.date() for idx in ed.index})
            cached[t] = dates
        except Exception as e:
            print(f"[EARNINGS] {t}: Fehler ({e}) — leer gesetzt")
            cached[t] = []
        time.sleep(0.3)

    with path.open("wb") as fh:
        pickle.dump(cached, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[CACHE] {len(cached)} Earnings-Serien in {path.name}")
    return cached


def _clean_close(df: pd.DataFrame) -> pd.Series:
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s = df["Close"].astype(float)
    s.index = idx
    return s.sort_index()


def analyze_trade(symbol: str, entry_date: pd.Timestamp, exit_date: pd.Timestamp,
                  entry_price: float, close: pd.Series,
                  earnings: list[date]) -> dict:
    out = {"symbol": symbol, "entry_date": entry_date.date(),
          "exit_date": exit_date.date(), "earnings_date": None,
          "pre_earn_plpc": np.nan, "earnings_reaction_pct": np.nan,
          "status": "keine_earnings_im_zeitraum"}

    in_window = [e for e in earnings if entry_date.date() < e <= exit_date.date()]
    if not in_window:
        return out

    ev = min(in_window)
    out["earnings_date"] = ev
    ev_ts = pd.Timestamp(ev)

    before = close[close.index < ev_ts]
    after = close[close.index >= ev_ts]
    if before.empty or after.empty:
        out["status"] = "keine_kursdaten_um_earnings"
        return out

    pre_close = float(before.iloc[-1])
    post_close = float(after.iloc[0])

    out["pre_earn_plpc"] = (pre_close / entry_price - 1) * 100
    out["earnings_reaction_pct"] = (post_close / pre_close - 1) * 100
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
    if closed.empty and opened.empty:
        print(f"Keine Trades in {args.json}.")
        return 0

    all_symbols = sorted(set(closed.get("symbol", pd.Series(dtype=str))) |
                         set(opened.get("symbol", pd.Series(dtype=str))))
    earnings_by_symbol = load_earnings_dates(all_symbols, args.refresh)

    from distribution_day_analysis import load_close_volume
    cv = load_close_volume(all_symbols, False)

    today = pd.Timestamp(date.today())
    rows = []
    for _, r in closed.iterrows():
        df = cv.get(r["symbol"])
        if df is None or df.empty:
            continue
        close = _clean_close(df)
        res = analyze_trade(r["symbol"], pd.Timestamp(r["entry_date"]),
                            pd.Timestamp(r["exit_date"]), float(r["entry_price"]),
                            close, earnings_by_symbol.get(r["symbol"], []))
        res["realized_plpc"] = r.get("realized_plpc")
        res["exit_reason"] = r.get("exit_reason")
        res["is_open"] = False
        rows.append(res)

    for _, r in opened.iterrows():
        df = cv.get(r["symbol"])
        if df is None or df.empty:
            continue
        close = _clean_close(df)
        res = analyze_trade(r["symbol"], pd.Timestamp(r["entry_date"]), today,
                            float(r["entry_price"]), close,
                            earnings_by_symbol.get(r["symbol"], []))
        res["realized_plpc"] = (float(close.iloc[-1]) / float(r["entry_price"]) - 1) * 100
        res["exit_reason"] = "offen"
        res["is_open"] = True
        rows.append(res)

    res_df = pd.DataFrame(rows)

    print(f"=== Alle Trades (n={len(res_df)}, davon offen={int(res_df['is_open'].sum())}) ===\n")
    print("Status:")
    for s, c in res_df["status"].value_counts().items():
        print(f"  {s}: {c}")
    print()

    with_earnings = res_df[res_df["status"] == "ok"].copy()
    if with_earnings.empty:
        print("Kein einziger Trade hatte einen Earnings-Termin waehrend der Haltedauer.\n"
             "Der Earnings-Blackout beim Entry (14 Tage) plus die kurzen Haltedauern\n"
             "(ueberwiegend stop_hit binnen Tagen bis wenigen Wochen) verhindern das\n"
             "offenbar schon strukturell -- die Regel haette in diesem Sample nie\n"
             "gegriffen.")
        return 0

    cols = ["symbol", "entry_date", "exit_date", "earnings_date",
           "pre_earn_plpc", "earnings_reaction_pct", "realized_plpc",
           "exit_reason", "is_open"]
    print(with_earnings[cols].sort_values("earnings_date").to_string(index=False))
    print()

    profitable_before = with_earnings[with_earnings["pre_earn_plpc"] > 0]
    print(f"Davon bereits im Plus vor dem Report (n={len(profitable_before)}) -- "
         f"hier haette die Model-Book-Regel ueberhaupt gegriffen:")
    if profitable_before.empty:
        print("  keine")
    else:
        for _, r in profitable_before.iterrows():
            delta = r["realized_plpc"] - r["pre_earn_plpc"] if not r["is_open"] else np.nan
            print(f"  {r['symbol']}: vor Report {r['pre_earn_plpc']:.2f}%, "
                 f"Reaktion {r['earnings_reaction_pct']:+.2f}%, "
                 f"tatsaechliches Ergebnis {r['realized_plpc']:.2f}%"
                 + (f" (Delta ggue. Vorher-Ausstieg: {delta:+.2f}pp)" if not r["is_open"] else " (noch offen)"))

    closed_with_earn = with_earnings[~with_earnings["is_open"]]
    if not closed_with_earn.empty:
        log_experiment(
            tool="pre_earnings_exit_analysis",
            params={},
            metrics={
                "n_with_earnings_in_hold": len(closed_with_earn),
                "n_profitable_before_earnings": int((closed_with_earn["pre_earn_plpc"] > 0).sum()),
                "median_earnings_reaction_pct": None if closed_with_earn["earnings_reaction_pct"].isna().all()
                    else round(float(closed_with_earn["earnings_reaction_pct"].median()), 2),
            },
            context={"json": args.json, "n_closed": len(closed), "n_open": len(opened)},
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
