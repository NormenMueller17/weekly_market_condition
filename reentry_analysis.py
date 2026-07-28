"""Messt, was ein Wiedereinstieg nach dem Ausstoppen gebracht haette.

Anlass (2026-07-28): 16 von 17 geschlossenen Journal-Trades endeten am Stop, und
jeder von ihnen bei <= +1,6 %. Der einzige Trade, der NICHT am Stop endete,
machte +73,8 %. Vier Wochen nach dem Stop standen dieselben Titel im Median
+17,8 % (Alpha +12,8 pp, 78 % positiv), nach acht Wochen +26,3 % (Alpha +26,6,
100 % positiv) — siehe journal_stop_analysis.py.

Die Titelauswahl war also richtig, das Timing falsch. Minervinis Antwort darauf
ist kein weiterer Stop, sondern der Wiedereinstieg: dieselbe Aktie wird nach dem
Ausstoppen erneut gekauft, sobald sie den Pivot zurueckerobert — notfalls drei-
bis viermal, bis der Ausbruch haelt.

Wir blockieren den Wiedereinstieg nicht aktiv (nur OFFENE Positionen werden aus
den Kandidaten gefiltert). Er passiert nur faktisch nie, weil `Vol-Breakout` und
`Close > Vorwoche` nach einem Zusammenbruch wochenlang nicht feuern und der
Titel, wenn er endlich requalifiziert, den Zug schon verpasst hat.

Dieses Tool simuliert die Regel, bevor sie gebaut wird:

  1. Entry nach Systemregel, Stop = max(min_stop_pct, stop_atr_mult x ATR)
  2. Nach dem Ausstoppen: erster Tag, an dem der Kurs den urspruenglichen
     Pivot + Puffer wieder ueberschreitet (Buy-Stop-Semantik, daher High)
  3. Dort erneut kaufen, gleicher Stop-Mechanismus, bis `--max-versuche`
     erschoepft ist oder der Horizont endet
  4. Vergleich gegen "ein Versuch, danach nie wieder"

Bewertet wird in Depot-Prozentpunkten: Unter Risk-first-Sizing traegt jeder
Versuch nur `min(max_risk/stop_pct, max_pos_pct)` an Position, ein zweiter
Versuch kostet also erneut Risikobudget. Die reine Prozentrendite je Versuch
waere irrefuehrend.

  python reentry_analysis.py                       # Backtest-Stichprobe
  python reentry_analysis.py --max-versuche 3 --cooldown 5
  python reentry_analysis.py --sweep
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from atr_stop_analysis import (
    _sizing_rules,
    atr_pct_at,
    load_ohlc,
    system_stop_pct,
)
from experiment_log import log_experiment

TRADING_DAYS_PER_WEEK = 5


def _rules() -> dict:
    """Stop-Parameter aus rules.json — dieselbe Quelle wie signal_generator."""
    try:
        f = json.loads((Path(__file__).parent / "rules.json")
                       .read_text(encoding="utf-8")).get("filters", {})
    except Exception:
        f = {}
    return {
        "min_stop_pct":  f.get("min_stop_pct",  10.0) / 100.0,
        "stop_atr_mult": f.get("stop_atr_mult",  2.0),
        "max_stop_pct":  f.get("max_stop_pct",  20.0) / 100.0,
        "buffer":        f.get("buy_stop_buffer_pct", 0.1) / 100.0,
    }


def _daily_window(oh: pd.DataFrame, start: pd.Timestamp,
                  days: int) -> pd.DataFrame:
    """Tages-OHLC ab (exklusive) `start` fuer `days` Handelstage."""
    idx = pd.to_datetime(oh.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    d = oh.copy()
    d.index = idx
    return d[d.index > pd.Timestamp(start)].iloc[:days]


def _above_ma(oh: pd.DataFrame, when: pd.Timestamp, window: int = 50) -> bool:
    """Schliesst der Kurs am Tag `when` ueber seinem `window`-Tage-Schnitt?

    Proxy fuer "die Aufwaertsstruktur haelt noch" beim Wiedereinstieg. Strikt
    nur mit Daten BIS zu diesem Tag.
    """
    idx = pd.to_datetime(oh.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    sub = oh[idx <= pd.Timestamp(when)]
    if len(sub) < window:
        return True  # fail-open: zu kurze Historie ist kein Trendbruch
    close = pd.to_numeric(sub["Close"], errors="coerce").dropna()
    if len(close) < window:
        return True
    return bool(float(close.iloc[-1]) > float(close.iloc[-window:].mean()))


def simulate(oh: pd.DataFrame, entry_date: pd.Timestamp, pivot: float,
             atr_pct: float, horizon_days: int, r: dict,
             max_versuche: int, cooldown: int,
             require_ma50: bool = False) -> dict:
    """Simuliert Versuch 1..n auf Tagesdaten.

    Rueckgabe je Trade: Liste der Versuche mit Einstieg, Ausgang und Ergebnis,
    plus die Depot-Beitraege mit und ohne Wiedereinstieg.
    """
    fut = _daily_window(oh, entry_date, horizon_days)
    if fut.empty:
        return {}

    high = fut["High"].astype(float)
    low = fut["Low"].astype(float)
    close = fut["Close"].astype(float)

    versuche: list[dict] = []
    # Versuch 1 startet am Signaltag zum Pivot (der Buy-Stop ist dort gefuellt).
    entry_px = pivot
    start_i = 0
    atr_now = atr_pct

    while len(versuche) < max_versuche and start_i < len(fut):
        stop_frac = system_stop_pct(atr_now, r["stop_atr_mult"],
                                    r["min_stop_pct"], r["max_stop_pct"])
        if not np.isfinite(stop_frac):
            break
        stop_px = entry_px * (1 - stop_frac)

        seg_low = low.iloc[start_i:]
        hit = np.where(seg_low.to_numpy() <= stop_px)[0]

        if len(hit):
            exit_i = start_i + int(hit[0])
            versuche.append({
                "entry_i": start_i, "entry_px": entry_px,
                "exit_i": exit_i, "exit_px": stop_px,
                "ret": -stop_frac * 100, "stop_frac": stop_frac,
                "gestoppt": True,
            })
            # Wiedereinstieg: erster Tag nach Cooldown, an dem der
            # URSPRUENGLICHE Pivot + Puffer wieder ueberschritten wird.
            trigger = pivot * (1 + r["buffer"])
            scan_from = exit_i + 1 + cooldown
            if scan_from >= len(fut):
                break
            seg_high = high.iloc[scan_from:]
            back = np.where(seg_high.to_numpy() >= trigger)[0]
            if not len(back):
                break
            cand = scan_from + int(back[0])
            # Optional: Aufwaertsstruktur muss beim Wiedereinstieg noch halten.
            # Beim ersten Rueckerobern des Pivots pruefen — spaetere Versuche
            # nicht nachzuschleifen waere Rosinenpickerei.
            if require_ma50 and not _above_ma(oh, fut.index[cand]):
                break
            start_i = cand
            entry_px = trigger
            # ATR am Wiedereinstiegstag neu messen: nach einem Zusammenbruch
            # ist sie hoeher, der Stop also weiter. Mit der ATR vom
            # Original-Signal waere der zweite Stop zu eng und die Regel
            # kuenstlich benachteiligt.
            atr_re = atr_pct_at(oh, fut.index[start_i])
            if np.isfinite(atr_re):
                atr_now = atr_re
        else:
            versuche.append({
                "entry_i": start_i, "entry_px": entry_px,
                "exit_i": len(fut) - 1, "exit_px": float(close.iloc[-1]),
                "ret": float(close.iloc[-1] / entry_px - 1) * 100,
                "stop_frac": stop_frac, "gestoppt": False,
            })
            break

    if not versuche:
        return {}

    max_risk, max_pos = _sizing_rules()["risk"], _sizing_rules()["pos"]

    def contrib(v: dict) -> float:
        pos = min(max_risk / v["stop_frac"], max_pos)
        return pos * v["ret"]

    return {
        "n_versuche": len(versuche),
        "ohne_reentry_pp": contrib(versuche[0]),
        "mit_reentry_pp": sum(contrib(v) for v in versuche),
        "ohne_reentry_ret": versuche[0]["ret"],
        "erster_gestoppt": versuche[0]["gestoppt"],
        "letzter_gestoppt": versuche[-1]["gestoppt"],
        "gewinn_am_ende": versuche[-1]["ret"] if not versuche[-1]["gestoppt"] else 0.0,
        "versuche": versuche,
    }


def analyse(entries: pd.DataFrame, ohlc: dict[str, pd.DataFrame],
            horizon_weeks: int, r: dict, max_versuche: int,
            cooldown: int) -> pd.DataFrame:
    horizon_days = horizon_weeks * TRADING_DAYS_PER_WEEK
    rows = []
    for _, e in entries.iterrows():
        sym = e["ticker"]
        oh = ohlc.get(sym)
        if oh is None:
            continue
        d = pd.Timestamp(e["date"])
        atr = atr_pct_at(oh, d)
        if not np.isfinite(atr):
            continue
        res = simulate(oh, d, float(e["entry_px"]), atr, horizon_days, r,
                       max_versuche, cooldown)
        if not res:
            continue
        rows.append({"ticker": sym, "date": e["date"], "atr_pct": atr,
                     **{k: v for k, v in res.items() if k != "versuche"}})
    return pd.DataFrame(rows)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=".cache/vcp_forward.csv")
    ap.add_argument("--horizon", type=int, default=13, help="Wochen")
    ap.add_argument("--max-versuche", type=int, default=3)
    ap.add_argument("--cooldown", type=int, default=3,
                    help="Handelstage Wartezeit nach dem Stop")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    r = _rules()
    entries = pd.read_csv(args.csv).drop_duplicates(subset=["ticker", "date"])
    ohlc = load_ohlc(sorted(entries["ticker"].unique()), args.refresh)

    if args.sweep:
        rows = []
        for mv in (1, 2, 3, 4):
            for cd in (0, 3, 5, 10):
                res = analyse(entries, ohlc, args.horizon, r, mv, cd)
                if res.empty:
                    continue
                rows.append({
                    "versuche": mv, "cooldown": cd, "n": len(res),
                    "ø_versuche": res.n_versuche.mean(),
                    "endet_im_gewinn_%": (~res.letzter_gestoppt).mean() * 100,
                    "depot_pp": res.mit_reentry_pp.mean(),
                })
        sw = pd.DataFrame(rows)
        print(f"\nSweep Wiedereinstieg ({args.horizon}W, n={len(entries)} Entries)")
        print("depot_pp = Summe aller Versuche in Depot-Prozentpunkten je Signal\n")
        print(sw.sort_values("depot_pp", ascending=False).round(2).to_string(index=False))
        return 0

    res = analyse(entries, ohlc, args.horizon, r, args.max_versuche, args.cooldown)
    if res.empty:
        print("[ERROR] Keine auswertbaren Entries.")
        return 1

    gestoppt = res[res.erster_gestoppt]
    print(f"\n{len(res)} Signale, Horizont {args.horizon}W, "
          f"max {args.max_versuche} Versuche, Cooldown {args.cooldown} Tage")
    print(f"Stop-Regel: max({r['min_stop_pct'] * 100:.0f} %, "
          f"{r['stop_atr_mult']}x ATR), Cap {r['max_stop_pct'] * 100:.0f} %\n")

    print(f"Versuch 1 ausgestoppt: {len(gestoppt)} von {len(res)} "
          f"({len(gestoppt) / len(res) * 100:.0f} %)")
    print(f"davon kam der Kurs zum Pivot zurueck: "
          f"{(gestoppt.n_versuche > 1).sum()} "
          f"({(gestoppt.n_versuche > 1).mean() * 100:.0f} %)")
    print(f"Versuche je Signal Ø {res.n_versuche.mean():.2f}\n")

    print(f"{'':28}{'ohne Re-Entry':>16}{'mit Re-Entry':>16}")
    print("-" * 60)
    print(f"{'Depotbeitrag Ø (pp)':28}{res.ohne_reentry_pp.mean():>16.3f}"
          f"{res.mit_reentry_pp.mean():>16.3f}")
    print(f"{'endet im Gewinn':28}"
          f"{(~res.erster_gestoppt).mean() * 100:>15.0f}%"
          f"{(~res.letzter_gestoppt).mean() * 100:>15.0f}%")

    delta = res.mit_reentry_pp.mean() - res.ohne_reentry_pp.mean()
    print(f"\nDifferenz: {delta:+.3f} pp je Signal "
          f"({delta / abs(res.ohne_reentry_pp.mean()) * 100:+.0f} % relativ)"
          if res.ohne_reentry_pp.mean() else f"\nDifferenz: {delta:+.3f} pp")

    # Nur die Teilmenge, in der die Regel ueberhaupt greift
    aktiv = res[res.n_versuche > 1]
    if not aktiv.empty:
        print(f"\nNur die {len(aktiv)} Signale mit tatsaechlichem Wiedereinstieg:")
        print(f"  ohne: {aktiv.ohne_reentry_pp.mean():+.3f} pp   "
              f"mit: {aktiv.mit_reentry_pp.mean():+.3f} pp   "
              f"({aktiv.mit_reentry_pp.mean() - aktiv.ohne_reentry_pp.mean():+.3f} pp)")
        print(f"  am Ende im Gewinn: "
              f"{(~aktiv.letzter_gestoppt).mean() * 100:.0f} %")

    log_experiment(
        tool="reentry_analysis",
        params={"horizon_weeks": args.horizon, "max_versuche": args.max_versuche,
                "cooldown_days": args.cooldown, **{k: v for k, v in r.items()}},
        metrics={
            "n": len(res),
            "erster_gestoppt_pct": float(res.erster_gestoppt.mean() * 100),
            "rueckkehr_pct": float((gestoppt.n_versuche > 1).mean() * 100)
            if not gestoppt.empty else 0.0,
            "versuche_mean": float(res.n_versuche.mean()),
            "depot_pp_ohne": float(res.ohne_reentry_pp.mean()),
            "depot_pp_mit": float(res.mit_reentry_pp.mean()),
            "gewinn_ohne_pct": float((~res.erster_gestoppt).mean() * 100),
            "gewinn_mit_pct": float((~res.letzter_gestoppt).mean() * 100),
        },
        context={"csv": args.csv},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
