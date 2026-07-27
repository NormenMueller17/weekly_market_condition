"""Forward-Return-Test für VCP-Entries: misst, ob die Signale Geld verdienen.

Der Full-Universe-Check (`vcp_universe_check.py`) misst nur HÄUFIGKEIT und
RS-Güte. Der Sweep vom 2026-07-27 zeigte, dass die Kalibrierung die Menge der
Basen um Faktor 2,5 steuert, die RS-Mischung aber praktisch nicht (RS-Median
56–61 über alle Kombinationen). Damit ist offen, ob die zusätzlichen Basen einer
lockereren Kalibrierung überhaupt profitabel sind.

Dieses Tool nimmt die Entry-Signale eines Scans und misst je Horizont:
  * Rendite des Titels ab Signal
  * Alpha gegen SPY über denselben Zeitraum
  * Trefferquote (Anteil Alpha > 0)
  * Stop-Quote: Anteil der Trades, die vor Ende des Horizonts unter den
    Initial-Stop fallen (Minervini-typisch −8 % auf Wochentief-Basis)

Einstiegsannahme: Kauf zum SCHLUSS der Signalwoche. Das Signal steht erst am
Wochenschluss fest, ein früherer Einstieg wäre Look-ahead. Der reale Einstieg am
Montag darauf kann davon abweichen — die Zahlen sind daher optimistisch für
Titel mit Montagslücke nach oben und umgekehrt.

Nur lesend, kein Alpaca.

Beispiele
---------
  python vcp_forward_test.py --compare            # aktuelle vs. Sweep-Favorit
  python vcp_forward_test.py --limit 800
  python vcp_forward_test.py --max-final-range 0.12 --min-contraction 0.80
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from config import SETTINGS
from data_sources import load_weekly_history
from vcp_universe_check import (
    MIN_BARS,
    CACHE_TTL_DAYS,
    Params,
    _apply_limit,
    compute_rs_matrix,
    load_daily,
    load_history,
    run_scan,
    trim_daily,
)

BENCH = "SPY"
STOP_PCT = 0.08          # Minervini-Initial-Stop, auf Wochentief gemessen


# ─────────────────────────────────────────────────────────────────────────────
def load_benchmark(weeks: int, refresh: bool) -> pd.DataFrame | None:
    """SPY-Wochenserie — eigener Cache, das Universum enthält keine ETFs."""
    d = Path(SETTINGS.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"vcp_bench_{BENCH}_{weeks}w.pkl"

    if path.exists() and not refresh:
        age = (time.time() - path.stat().st_mtime) / 86400
        if age <= CACHE_TTL_DAYS:
            with path.open("rb") as fh:
                return pickle.load(fh)

    hist = load_weekly_history([BENCH], weeks=weeks)
    bench = hist.get(BENCH)
    if bench is None or bench.empty:
        print(f"[WARN] {BENCH} konnte nicht geladen werden — Alpha entfällt.")
        return None
    with path.open("wb") as fh:
        pickle.dump(bench, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return bench


def _bench_return(bench: pd.Series, date: pd.Timestamp, horizon: int) -> float:
    """SPY-Rendite über `horizon` Wochen ab der Woche von `date`."""
    pos = bench.index.searchsorted(date)
    if pos >= len(bench) or pos + horizon >= len(bench):
        return float("nan")
    return float(bench.iloc[pos + horizon] / bench.iloc[pos] - 1.0)


def forward_returns(hist: dict[str, pd.DataFrame], entries: pd.DataFrame,
                    bench: pd.DataFrame | None, horizons: tuple[int, ...]) -> pd.DataFrame:
    """Ergänzt jede Entry-Zeile um Rendite, Alpha und Stop-Treffer je Horizont.

    Zeilen, deren Horizont über das Ende der Historie hinausragt, bekommen NaN —
    sie werden später je Horizont einzeln ausgefiltert, damit ein kurzer Horizont
    nicht Datenpunkte verliert, die nur dem langen fehlen.
    """
    bench_close = None
    if bench is not None:
        bench_close = bench["Close"].astype(float)
        bench_close.index = pd.to_datetime(bench_close.index)

    rows = []
    for _, r in entries.iterrows():
        df = hist.get(r["ticker"])
        if df is None:
            continue
        idx = pd.to_datetime(df.index)
        date = pd.Timestamp(r["date"])
        pos = int(idx.searchsorted(date))
        if pos >= len(df):
            continue

        close = df["Close"].astype(float)
        low = df["Low"].astype(float)
        entry_px = float(close.iloc[pos])
        if not np.isfinite(entry_px) or entry_px <= 0:
            continue

        out = {"ticker": r["ticker"], "date": date, "rs": r.get("rs"),
               "vol_ratio": r.get("vol_ratio"), "entry_px": entry_px}

        for h in horizons:
            if pos + h < len(close):
                ret = float(close.iloc[pos + h] / entry_px - 1.0)
                # Stop-Treffer: Wochentief im Haltezeitraum unter dem Initial-Stop
                worst = float(low.iloc[pos + 1:pos + h + 1].min())
                stopped = bool(worst <= entry_px * (1 - STOP_PCT))
            else:
                ret, stopped = float("nan"), None

            out[f"ret_{h}w"] = ret
            out[f"stop_{h}w"] = stopped
            if bench_close is not None and np.isfinite(ret):
                b = _bench_return(bench_close, date, h)
                out[f"alpha_{h}w"] = ret - b if np.isfinite(b) else float("nan")
            else:
                out[f"alpha_{h}w"] = float("nan")

        rows.append(out)

    return pd.DataFrame(rows)


def summarize_returns(fr: pd.DataFrame, horizons: tuple[int, ...],
                      label: str) -> list[dict]:
    """Kennzahlen je Horizont. Median als Hauptmaß — die Rendite­verteilung von
    Ausbruchssignalen ist rechtsschief, der Mittelwert hängt an wenigen Ausreißern.
    """
    out = []
    for h in horizons:
        col, acol, scol = f"ret_{h}w", f"alpha_{h}w", f"stop_{h}w"
        sub = fr[fr[col].notna()]
        if sub.empty:
            out.append({"label": label, "h": h, "n": 0})
            continue
        alpha = sub[acol].dropna()
        stops = sub[scol].dropna()
        out.append({
            "label": label,
            "h": h,
            "n": len(sub),
            "ret_med": sub[col].median() * 100,
            "ret_mean": sub[col].mean() * 100,
            "win_pct": (sub[col] > 0).mean() * 100,
            "alpha_med": alpha.median() * 100 if len(alpha) else float("nan"),
            "alpha_win_pct": (alpha > 0).mean() * 100 if len(alpha) else float("nan"),
            "stop_pct": stops.mean() * 100 if len(stops) else float("nan"),
        })
    return out


def print_summary(rows: list[dict], horizons: tuple[int, ...]) -> None:
    print("\n" + "=" * 94)
    print("FORWARD-RETURN-TEST — Einstieg zum Schluss der Signalwoche")
    print(f"Alpha gegen {BENCH}; Stop-Quote = Wochentief ≤ −{STOP_PCT:.0%} "
          "im Haltezeitraum")
    print("=" * 94)
    print(f"{'Kalibrierung':<34} {'H':>3} {'n':>5} {'Ret-Med':>8} {'Ret-Ø':>8} "
          f"{'Gewinn':>7} {'Alpha-Med':>10} {'Alpha>0':>8} {'Stop':>6}")
    print("-" * 94)
    for r in rows:
        if not r.get("n"):
            print(f"{r['label']:<34} {r['h']:>3}W {0:>5}   — keine auswertbaren Entries")
            continue
        print(f"{r['label']:<34} {r['h']:>3}W {r['n']:>5} "
              f"{r['ret_med']:>7.2f}% {r['ret_mean']:>7.2f}% {r['win_pct']:>6.0f}% "
              f"{r['alpha_med']:>9.2f}% {r['alpha_win_pct']:>7.0f}% {r['stop_pct']:>5.0f}%")
    print("=" * 94)


# ─────────────────────────────────────────────────────────────────────────────
def run_one(hist, daily, rs_sub, bench, params: Params, weeks_back: int,
            workers: int, horizons: tuple[int, ...], min_rs: float,
            label: str) -> tuple[list[dict], pd.DataFrame]:
    df, _ = run_scan(hist, params, weeks_back, workers, quiet=True,
                     daily=daily, rs=rs_sub)
    if df.empty:
        return [{"label": label, "h": h, "n": 0} for h in horizons], pd.DataFrame()

    entries = df[df["entry"]]
    if min_rs > 0:
        entries = entries[entries["rs"] >= min_rs]
    print(f"  {label:<34} {len(df):5d} Basen → {len(entries):4d} Entries"
          f"{f' (RS≥{min_rs:.0f})' if min_rs > 0 else ''}")
    if entries.empty:
        return [{"label": label, "h": h, "n": 0} for h in horizons], pd.DataFrame()

    fr = forward_returns(hist, entries, bench, horizons)
    return summarize_returns(fr, horizons, label), fr


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weeks-back", type=int, default=78,
                    help="Walk-forward-Fenster (Default 78 = 1,5J; der laengste "
                         "Horizont braucht Zukunft, daher grosszuegig)")
    ap.add_argument("--history-weeks", type=int, default=156)
    ap.add_argument("--horizons", type=str, default="4,8,13")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--min-rs", type=float, default=70.0,
                    help="Entries unter dieser RS ignorieren (Produktionsfilter, "
                         "Default 70; 0 = alle)")
    ap.add_argument("--compare", action="store_true",
                    help="Aktuelle Kalibrierung gegen den Sweep-Favoriten")
    ap.add_argument("--csv", type=str, default=None)

    ap.add_argument("--min-contraction", type=float, default=0.70)
    ap.add_argument("--max-pullback", type=float, default=0.20)
    ap.add_argument("--max-final-range", type=float, default=0.08)
    ap.add_argument("--max-vol-dryup", type=float, default=0.90)
    ap.add_argument("--min-breakout-vol-ratio", type=float, default=1.40)
    args = ap.parse_args()

    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())

    hist_all = load_history(args.history_weeks, None, args.refresh)
    if not hist_all:
        print("[ERROR] Keine Historie geladen.")
        return 1
    rs_matrix = compute_rs_matrix(hist_all)

    hist = _apply_limit(hist_all, args.limit)
    usable = {t: d for t, d in hist.items() if len(d) >= MIN_BARS}
    daily = trim_daily(load_daily(sorted(usable), args.refresh), args.weeks_back)
    rs_sub = rs_matrix[[t for t in usable if t in rs_matrix.columns]]
    bench = load_benchmark(args.history_weeks, args.refresh)
    print(f"[DATA] {len(usable)} Titel, {len(daily)} Tagesserien, "
          f"Benchmark {BENCH}: {'ok' if bench is not None else 'FEHLT'}\n")

    if args.compare:
        cands = [
            (Params(max_final_range=0.08, min_contraction=0.70, max_vol_dryup=0.90,
                    min_breakout_vol_ratio=args.min_breakout_vol_ratio),
             "aktuell (fr=0.08 con=0.70 dry=0.90)"),
            (Params(max_final_range=0.12, min_contraction=0.80, max_vol_dryup=0.90,
                    min_breakout_vol_ratio=args.min_breakout_vol_ratio),
             "Sweep-Favorit (fr=0.12 con=0.80)"),
            (Params(max_final_range=0.12, min_contraction=0.80, max_vol_dryup=1.00,
                    min_breakout_vol_ratio=args.min_breakout_vol_ratio),
             "max. Menge (fr=0.12 con=0.80 dry=aus)"),
        ]
    else:
        cands = [(Params(
            max_final_range=args.max_final_range,
            min_contraction=args.min_contraction,
            max_pullback=args.max_pullback,
            max_vol_dryup=args.max_vol_dryup,
            min_breakout_vol_ratio=args.min_breakout_vol_ratio,
        ), "eigene Parameter")]

    print(f"[SCAN] {len(cands)} Kalibrierung(en) × {len(usable)} Titel "
          f"× {args.weeks_back} Wochen")
    rows, frames = [], []
    for p, label in cands:
        r, fr = run_one(usable, daily, rs_sub, bench, p, args.weeks_back,
                        args.workers, horizons, args.min_rs, label)
        rows.extend(r)
        if not fr.empty:
            fr["calib"] = label
            frames.append(fr)

    print_summary(rows, horizons)

    if frames:
        allfr = pd.concat(frames, ignore_index=True)
        if args.csv:
            allfr.to_csv(args.csv, index=False)
            print(f"\n[CSV] {len(allfr)} Entries → {args.csv}")

    print("\nLesehilfe: Alpha-Median und Alpha>0 sind die entscheidenden Spalten —")
    print("eine hohe Rohrendite in einem steigenden Markt sagt fuer sich nichts.")
    print("Bei n < 30 pro Zeile ist die Rangfolge zwischen Kalibrierungen nicht")
    print("belastbar; dann eher als Plausibilitaetscheck lesen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
