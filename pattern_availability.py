"""Wie viele Titel pro Woche hätten überhaupt ein Muster?

Kernfrage hinter `require_pattern` (rules.json). Die Signalhistorie
(`docs/data/signals_meta_*.json`) zeigt: 72 von 72 Signalen aus April–Juli 2026
hatten `pattern: "–"`. Mit `require_pattern=true` hätte das System in vier
Monaten NULL Trades gemacht — die Einstellung ist also keine Design-Präferenz,
sondern eine Notwendigkeit, solange die Mustererkennung nichts liefert.

Seit `a7b625a`/`6d978bc` ist `detect_vcp` repariert (adaptives Basisfenster,
Ausbruchsvolumen am Ausbruchstag). `detect_launchpad` hat dagegen noch beide
Fehler, die in `detect_vcp` behoben wurden:
  * `max(letzte 3 Bars) >= factor * base_avg` — das Maximum mehrerer Bars gegen
    einen Durchschnitt, der Surge kann also aus einer Woche ohne Preisausbruch
    stammen
  * gemessen auf Wochenbasis; eine starke Ausbruchskerze verwässert mit vier
    ruhigen Tagen zu unauffälligem Wochenvolumen

Dieses Tool misst walk-forward, wie viele Titel je Woche ein VCP- bzw.
Launchpad-Entry hätten — roh und nach RS-Filter. Ergebnis ist die
Entscheidungsgrundlage dafür, ob `require_pattern=true` überhaupt handelbar wäre.

  python pattern_availability.py --weeks-back 26
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from detect_vcp import detect_vcp
from experiment_log import log_experiment
from launchpad_detection import detect_launchpad
from vcp_universe_check import (
    MIN_BARS,
    compute_rs_matrix,
    load_daily,
    load_history,
    trim_daily,
)

_HIST: dict[str, pd.DataFrame] = {}
_DAILY: dict[str, pd.DataFrame] = {}
_RS: dict[str, pd.Series] = {}
_WEEKS = 0


def _init(hist, daily, rs, weeks):
    global _HIST, _DAILY, _RS, _WEEKS
    _HIST, _DAILY, _RS, _WEEKS = hist, daily, rs, weeks


def _scan(ticker: str) -> list[tuple]:
    df = _HIST[ticker]
    daily = _DAILY.get(ticker)
    rs = _RS.get(ticker)
    out = []
    n = len(df)
    for k in range(_WEEKS):
        end = n - k
        if end < MIN_BARS:
            break
        sl = df.iloc[:end]
        rs_now = None
        if rs is not None:
            v = rs.get(sl.index[-1])
            if v is not None and pd.notna(v):
                rs_now = float(v)
        try:
            v = detect_vcp(sl, window=60, daily_df=daily)
            lp = detect_launchpad(sl)
        except Exception:
            continue
        vcp_e = bool(v.get("Entry_Signal"))
        lp_e = bool(lp.get("Launchpad_Entry"))
        if vcp_e or lp_e or v.get("VCP") or lp.get("Launchpad"):
            out.append((k, sl.index[-1], ticker, bool(v.get("VCP")), vcp_e,
                        bool(lp.get("Launchpad")), lp_e,
                        rs_now if rs_now is not None else float("nan")))
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weeks-back", type=int, default=26)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--min-rs", type=float, default=70.0)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()

    hist_all = load_history(156, None, False)
    rs_matrix = compute_rs_matrix(hist_all)
    hist = hist_all if args.limit is None else dict(list(hist_all.items())[:args.limit])
    usable = {t: d for t, d in hist.items() if len(d) >= MIN_BARS}
    daily = trim_daily(load_daily(sorted(usable), False), args.weeks_back)
    rs = {t: rs_matrix[t] for t in usable if t in rs_matrix.columns}
    print(f"[DATA] {len(usable)} Titel × {args.weeks_back} Wochen\n")

    t0 = time.time()
    rows: list[tuple] = []
    tickers = sorted(usable)
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init,
                             initargs=({t: usable[t] for t in tickers},
                                       {t: daily[t] for t in tickers if t in daily},
                                       rs, args.weeks_back)) as pool:
        for i, res in enumerate(pool.map(_scan, tickers, chunksize=25), 1):
            rows.extend(res)
            if i % 500 == 0:
                print(f"  … {i}/{len(tickers)} ({time.time() - t0:.0f}s)")

    df = pd.DataFrame(rows, columns=["week_offset", "date", "ticker", "vcp",
                                     "vcp_entry", "lp", "lp_entry", "rs"])
    w = args.weeks_back
    print(f"\n[SCAN] {len(tickers)} Titel in {time.time() - t0:.0f}s\n")

    print("=" * 68)
    print(f"MUSTER-VERFUEGBARKEIT   {len(tickers)} Titel × {w} Wochen")
    print("=" * 68)
    print(f"{'':<28}{'gesamt':>10}{'/Woche':>10}{'RS>=70/W':>12}")
    print("-" * 68)
    for label, col in (("VCP Basis erkannt", "vcp"),
                       ("VCP Entry (Ausbruch)", "vcp_entry"),
                       ("Launchpad Basis", "lp"),
                       ("Launchpad Entry", "lp_entry")):
        n = int(df[col].sum()) if not df.empty else 0
        n_rs = int((df[col] & (df["rs"] >= args.min_rs)).sum()) if not df.empty else 0
        print(f"{label:<28}{n:>10}{n / w:>10.2f}{n_rs / w:>12.2f}")

    any_entry = df["vcp_entry"] | df["lp_entry"] if not df.empty else pd.Series(dtype=bool)
    n_any = int(any_entry.sum()) if not df.empty else 0
    n_any_rs = int((any_entry & (df["rs"] >= args.min_rs)).sum()) if not df.empty else 0
    print("-" * 68)
    print(f"{'IRGENDEIN Entry':<28}{n_any:>10}{n_any / w:>10.2f}{n_any_rs / w:>12.2f}")
    print("=" * 68)
    print(f"\nDie letzte Spalte ist die relevante: mit require_pattern=true und dem")
    print(f"RS-Hartfilter (>={args.min_rs:.0f}) blieben {n_any_rs / w:.2f} Kandidaten pro Woche")
    print("uebrig — VOR den weiteren elf Hartfiltern des Kauffilter-Stacks.")

    log_experiment(
        tool="pattern_availability",
        params={"weeks_back": w, "min_rs": args.min_rs},
        metrics={"vcp_entry_per_week": float(df["vcp_entry"].sum()) / w if not df.empty else 0.0,
                 "lp_entry_per_week": float(df["lp_entry"].sum()) / w if not df.empty else 0.0,
                 "any_entry_per_week": n_any / w,
                 "any_entry_rs_per_week": n_any_rs / w},
        context={"universe": len(tickers)},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
