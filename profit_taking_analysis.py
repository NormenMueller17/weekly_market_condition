"""Misst, was die Fast-Mover-Sperre kostet.

Befund 2026-07-28 aus dem Tradetagebuch: Die sechs als Fast Mover markierten
Trades erreichten im Median +48,0 % und wurden bei -0,0 % geschlossen — sie
gaben 49,9 Prozentpunkte ab. Die uebrigen elf erreichten im Median nur +2,7 %.

Mechanik (`exit_manager.check_profit_taking`):
  * Regel 2 markiert einen Titel als Fast Mover, wenn er +20 % innerhalb von
    drei Wochen schafft, und sperrt Teilverkaeufe fuer acht Wochen.
  * Regel 3c setzt den ATR-Trailing-Stop erst, wenn `partial_1_done` gesetzt
    ist — also NIE waehrend der Sperre.
  * Uebrig bleibt der Breakeven-Stop aus Regel 1 (Stop = Einstandspreis).

Ein Titel, der +50 % laeuft und dann zurueckkommt, wird damit bei exakt 0 %
geschlossen. Genau das ist fuenfmal passiert. Die Kopplung von Regel 3c an
`partial_1_done` ist ein Implementierungsartefakt, keine O'Neill-Regel: O'Neill
haelt den Titel acht Wochen, er laesst ihn nicht ungeschuetzt.

Dieses Werkzeug simuliert auf den echten Journal-Trades:
  A  aktuell   — waehrend der Sperre nur Breakeven-Stop
  B  Vorschlag — ATR-Trailing sobald der Breakeven-Trigger erreicht ist,
                 unabhaengig von Teilverkaeufen; Stop nie unter Breakeven

  python profit_taking_analysis.py
  python profit_taking_analysis.py --sweep
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from experiment_log import log_experiment
from vcp_universe_check import load_history


def _pt_rules() -> dict:
    try:
        r = json.loads((Path(__file__).parent / "rules.json")
                       .read_text(encoding="utf-8"))
        return r.get("profit_taking", {})
    except Exception:
        return {}


def _segment(oh: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    idx = pd.to_datetime(oh.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    d = oh.copy()
    d.index = idx
    return d[(d.index >= pd.Timestamp(start)) & (d.index <= pd.Timestamp(end))]


def _atr(seg: pd.DataFrame, window: int = 10) -> pd.Series:
    high, low, close = seg["High"], seg["Low"], seg["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(window).mean()


def simulate(seg: pd.DataFrame, entry: float, pt: dict, atr_mult: float,
             partials: bool, trailing: bool,
             giveback: float | None = None) -> float:
    """Geblendete Rendite in % ueber Teilverkaeufe und Restposition.

    Varianten:
      partials=False, trailing=False  → Ist-Zustand waehrend der
          Fast-Mover-Sperre: nur der Breakeven-Stop schuetzt.
      partials=True                   → Teilverkaeufe nach Power of Three
      trailing=True                   → zusaetzlich ATR-Trailing (Wochen-ATR10)
      giveback=0.5                    → Stop haelt die Haelfte des bisher
          erreichten Hoechstgewinns fest. Volatilitaetsunabhaengig und darum
          auf kleiner Stichprobe robuster als ein ATR-Multiplikator.
    """
    if seg.empty:
        return 0.0

    be_trigger = pt.get("breakeven_trigger_pct", 10.0)
    p1_trigger = pt.get("partial_1_trigger_pct", 20.0)
    p1_frac    = pt.get("partial_1_qty_frac",    0.333)
    p2_trigger = pt.get("partial_2_trigger_pct", 40.0)
    p2_frac    = pt.get("partial_2_qty_frac",    0.333)

    atr = _atr(seg)
    stop: float | None = None
    hoechster_schluss = entry
    hoechster_gewinn = 0.0
    rest = 1.0
    realisiert = 0.0
    p1_done = p2_done = False

    for i in range(len(seg)):
        hoch    = float(seg["High"].iloc[i])
        tief    = float(seg["Low"].iloc[i])
        schluss = float(seg["Close"].iloc[i])

        # Stop des VORBALKENS pruefen, bevor der neue Hoechststand einfliesst —
        # sonst zieht derselbe Balken den Stop nach, der ihn ausloest.
        if stop is not None and tief <= stop and rest > 0:
            return realisiert + rest * (stop / entry - 1) * 100

        gewinn = (hoch / entry - 1) * 100
        hoechster_gewinn = max(hoechster_gewinn, gewinn)

        if partials:
            if not p1_done and gewinn >= p1_trigger:
                kurs = entry * (1 + p1_trigger / 100)
                realisiert += p1_frac * (kurs / entry - 1) * 100
                rest -= p1_frac
                p1_done = True
            if p1_done and not p2_done and gewinn >= p2_trigger:
                kurs = entry * (1 + p2_trigger / 100)
                realisiert += p2_frac * (kurs / entry - 1) * 100
                rest -= p2_frac
                p2_done = True

        if hoechster_gewinn >= be_trigger:
            neu = entry
            if trailing:
                hoechster_schluss = max(hoechster_schluss, schluss)
                a = atr.iloc[i]
                if np.isfinite(a) and a > 0:
                    neu = max(neu, hoechster_schluss - atr_mult * float(a))
            if giveback is not None:
                neu = max(neu, entry * (1 + hoechster_gewinn * giveback / 100))
            stop = neu if stop is None else max(stop, neu)

    if rest <= 0:
        return realisiert
    return realisiert + rest * (float(seg["Close"].iloc[-1]) / entry - 1) * 100


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", default="docs/data/trades.json")
    ap.add_argument("--atr-mult", type=float, default=None,
                    help="Default: trailing_atr_mult aus rules.json")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    pt = _pt_rules()
    be_trigger = pt.get("breakeven_trigger_pct", 10.0)
    atr_mult = args.atr_mult if args.atr_mult is not None else \
        pt.get("trailing_atr_mult", 2.0)

    data = json.loads(Path(args.journal).read_text(encoding="utf-8"))
    # Nicht-Markt-Ausstiege (Delisting) raus — siehe
    # trade_journal.NICHT_MARKT_EXITS.
    import trade_journal
    closed = [t for t in trade_journal.markt_trades(data.get("closed", []))
              if t.get("entry_date") and t.get("exit_date")
              and t.get("entry_price")]
    if not closed:
        print("[PT] Keine auswertbaren Trades.")
        return 1

    # WOCHENbars, nicht Tagesbars: `exit_manager.check_profit_taking` laedt
    # `interval="1wk"` und rechnet ATR10 darauf. Auf Tagesdaten kalibriert waere
    # der Multiplikator rund um den Faktor sqrt(5) zu klein und der simulierte
    # Trailing-Stop viel enger als der echte.
    ohlc = load_history(156, None, False)

    VARIANTEN = {
        "A_ist":     dict(partials=False, trailing=False, giveback=None),
        "B_trail":   dict(partials=False, trailing=True,  giveback=None),
        "C_teilv":   dict(partials=True,  trailing=False, giveback=None),
        "D_teil_tr": dict(partials=True,  trailing=True,  giveback=None),
        "E_gib50":   dict(partials=True,  trailing=False, giveback=0.5),
    }

    def lauf(mult: float) -> pd.DataFrame:
        rows = []
        for t in closed:
            seg = _segment(ohlc.get(t["symbol"], pd.DataFrame()),
                           t["entry_date"], t["exit_date"])
            if seg.empty:
                continue
            e = float(t["entry_price"])
            row = {
                "symbol": t["symbol"],
                "fast": bool(t.get("pt_is_fast_mover")),
                "peak": (float(seg["High"].max()) / e - 1) * 100,
                "ist": t.get("realized_plpc") or 0.0,
            }
            for name, kw in VARIANTEN.items():
                row[name] = simulate(seg, e, pt, mult, **kw)
            rows.append(row)
        return pd.DataFrame(rows)

    namen = list(VARIANTEN)

    if args.sweep:
        print(f"\nSweep ATR-Multiplikator — Ø aller Trades je Variante\n")
        print(f"{'mult':>6}" + "".join(f"{n:>11}" for n in namen))
        print("-" * (6 + 11 * len(namen)))
        for m in (1.5, 2.0, 2.5, 3.0, 4.0):
            r = lauf(m)
            print(f"{m:6.1f}" + "".join(f"{r[n].mean():11.2f}" for n in namen))
        print("\nnur die Fast Mover:\n")
        print(f"{'mult':>6}" + "".join(f"{n:>11}" for n in namen))
        print("-" * (6 + 11 * len(namen)))
        for m in (1.5, 2.0, 2.5, 3.0, 4.0):
            r = lauf(m)
            f = r[r.fast]
            print(f"{m:6.1f}" + "".join(f"{f[n].mean():11.2f}" for n in namen))
        return 0

    res = lauf(atr_mult)
    print(f"\nBreakeven-Trigger {be_trigger:.0f} %, ATR-Multiplikator {atr_mult} "
          f"(Wochen-ATR10)")
    print("A=Ist (Fast-Mover-Sperre)  B=+Trailing  C=+Teilverkaeufe  "
          "D=beides  E=Teilv.+50%-Giveback\n")
    print(f"{'Sym':6}{'FastM':>6}{'Peak%':>8}{'ist%':>8}"
          + "".join(f"{n:>11}" for n in namen))
    print("-" * (28 + 11 * len(namen)))
    for _, r in res.sort_values("peak", ascending=False).iterrows():
        print(f"{r.symbol:6}{('JA' if r.fast else '-'):>6}{r.peak:8.1f}{r.ist:8.1f}"
              + "".join(f"{r[n]:11.1f}" for n in namen))

    fm, uf = res[res.fast], res[~res.fast]
    print(f"\n{'':22}" + "".join(f"{n:>11}" for n in namen))
    print("-" * (22 + 11 * len(namen)))
    for label, g in (("Fast Mover", fm), ("uebrige", uf), ("alle", res)):
        print(f"{label + f' (n={len(g)})':22}"
              + "".join(f"{g[n].mean():11.2f}" for n in namen))

    best = max(namen, key=lambda n: res[n].mean())
    print(f"\nBeste Variante ueber alle Trades: {best} "
          f"({res[best].mean():+.2f} % je Trade gegen {res['A_ist'].mean():+.2f} % "
          f"im Ist-Zustand)")

    log_experiment(
        tool="profit_taking_analysis",
        params={"breakeven_trigger_pct": be_trigger, "trailing_atr_mult": atr_mult,
                "bars": "weekly"},
        metrics={
            "n": len(res), "n_fast": int(res.fast.sum()),
            "peak_median_fast": float(fm.peak.median()) if len(fm) else 0.0,
            **{f"{n}_mean": float(res[n].mean()) for n in namen},
            **{f"{n}_mean_fast": float(fm[n].mean()) if len(fm) else 0.0
               for n in namen},
        },
        context={"journal": args.journal},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
