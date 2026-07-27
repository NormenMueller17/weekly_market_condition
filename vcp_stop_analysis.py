"""Stop-Analyse für VCP-Entries: wie weit muss der Initial-Stop sein?

Anlass (2026-07-27): Im Forward-Test senkte ein simulierter −8-%-Stop die
Durchschnittsrendite auf allen Kalibrierungen deutlich (13W: 7,63 % → 3,73 %).
Titel, die zwischenzeitlich −8 % reissen, erholen sich offenbar häufig und
liefern danach die grossen Gewinne — die Verteilung ist stark rechtsschief, die
besten 10 % der Trades tragen mehr als die Gesamtsumme.

Wichtig: −8 % ist NICHT die Systemregel. `signal_generator._stop_vcp` setzt den
Stop auf 2× ATR unter dem Breakout-Level (Floor 15 % darunter), gedeckelt durch
`rules.json: max_stop_pct = 20`. Die reale Stop-Weite ist also ATR-abhängig und
in der Regel weiter als 8 %. Dieses Tool misst deshalb die Stop-Weite als
Kontinuum, statt eine einzelne Zahl zu testen.

Zwei Trigger-Arten, weil das eine echte Systementscheidung ist:
  * `low`   — Wochentief unterschreitet den Stop (wie eine echte Stop-Order)
  * `close` — nur der Wochenschluss zählt (kein Intraweek-Whipsaw)

Arbeitet auf den Entries aus `vcp_forward_test.py --csv` und dem vorhandenen
Wochen-Cache: kein erneuter VCP-Scan, Laufzeit Sekunden statt Stunden.

Beispiele
---------
  python vcp_stop_analysis.py --csv .cache/vcp_forward.csv
  python vcp_stop_analysis.py --horizon 8 --trigger close
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from experiment_log import log_experiment
from vcp_universe_check import load_history

STOP_LEVELS = (0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25)


def build_paths(entries: pd.DataFrame, hist: dict[str, pd.DataFrame],
                horizon: int) -> pd.DataFrame:
    """Ergänzt je Entry den Kursverlauf nach dem Einstieg.

    mae = tiefster Punkt relativ zum Einstieg (Maximum Adverse Excursion),
    mfe = höchster Punkt. Aus beiden lassen sich beliebige Stop- und
    Gewinnmitnahme-Schwellen offline auswerten, ohne neu zu scannen.
    """
    rows = []
    for _, r in entries.iterrows():
        df = hist.get(r["ticker"])
        if df is None:
            continue
        idx = pd.to_datetime(df.index)
        pos = int(idx.searchsorted(pd.Timestamp(r["date"])))
        if pos + horizon >= len(df):
            continue

        entry_px = float(r["entry_px"])
        if not np.isfinite(entry_px) or entry_px <= 0:
            continue

        fwd_low = df["Low"].astype(float).iloc[pos + 1:pos + horizon + 1]
        fwd_high = df["High"].astype(float).iloc[pos + 1:pos + horizon + 1]
        fwd_close = df["Close"].astype(float).iloc[pos + 1:pos + horizon + 1]
        if fwd_low.empty:
            continue

        rows.append({
            "ticker": r["ticker"], "date": r["date"], "calib": r.get("calib"),
            "entry_px": entry_px,
            "ret": float(fwd_close.iloc[-1] / entry_px - 1.0),
            "mae": float(fwd_low.min() / entry_px - 1.0),
            "mae_close": float(fwd_close.min() / entry_px - 1.0),
            "mfe": float(fwd_high.max() / entry_px - 1.0),
        })
    return pd.DataFrame(rows)


def stop_table(paths: pd.DataFrame, trigger: str) -> pd.DataFrame:
    """Ergebnis je Stop-Weite. Annahme: Ausstieg exakt zum Stop-Preis.

    Das ist optimistisch — bei einer Kurslücke unter den Stop wird schlechter
    ausgeführt. Die Richtung des Ergebnisses wird dadurch nicht besser.
    """
    mae_col = "mae" if trigger == "low" else "mae_close"
    out = []
    for s in STOP_LEVELS:
        hit = paths[mae_col] <= -s
        realized = np.where(hit, -s, paths["ret"])
        survivors = paths.loc[~hit, "ret"]
        out.append({
            "stop_pct": s * 100,
            "getriggert_pct": hit.mean() * 100,
            "ret_mean": realized.mean() * 100,
            "ret_median": float(np.median(realized)) * 100,
            "gewinn_pct": (realized > 0).mean() * 100,
            "ueberlebende_ret_mean": survivors.mean() * 100 if len(survivors) else np.nan,
        })
    out.append({
        "stop_pct": np.nan,          # Referenz ohne Stop
        "getriggert_pct": 0.0,
        "ret_mean": paths["ret"].mean() * 100,
        "ret_median": paths["ret"].median() * 100,
        "gewinn_pct": (paths["ret"] > 0).mean() * 100,
        "ueberlebende_ret_mean": paths["ret"].mean() * 100,
    })
    return pd.DataFrame(out)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=".cache/vcp_forward.csv",
                    help="Entries aus vcp_forward_test.py --csv")
    ap.add_argument("--horizon", type=int, default=13)
    ap.add_argument("--trigger", choices=("low", "close", "both"), default="both")
    ap.add_argument("--calib", default=None, help="nur eine Kalibrierung auswerten")
    ap.add_argument("--history-weeks", type=int, default=156)
    args = ap.parse_args()

    entries = pd.read_csv(args.csv)
    if args.calib:
        entries = entries[entries["calib"].str.contains(args.calib, case=False, na=False)]
    if entries.empty:
        print("[ERROR] Keine Entries in der CSV (Filter zu eng?).")
        return 1

    hist = load_history(args.history_weeks, None, False)
    paths = build_paths(entries, hist, args.horizon)
    if paths.empty:
        print("[ERROR] Keine auswertbaren Pfade — Horizont zu lang?")
        return 1

    print(f"\n{len(paths)} Entries mit {args.horizon}W Zukunft "
          f"({entries['calib'].nunique()} Kalibrierung(en))")
    print(f"MAE (Wochentief):  Median {paths.mae.median() * 100:6.1f} %   "
          f"Q25 {paths.mae.quantile(.25) * 100:6.1f} %")
    print(f"MFE (Wochenhoch):  Median {paths.mfe.median() * 100:6.1f} %   "
          f"Q75 {paths.mfe.quantile(.75) * 100:6.1f} %")

    triggers = ("low", "close") if args.trigger == "both" else (args.trigger,)
    for trig in triggers:
        tbl = stop_table(paths, trig)
        label = ("Wochentief (echte Stop-Order)" if trig == "low"
                 else "Wochenschluss (kein Intraweek-Whipsaw)")
        print(f"\n{'=' * 78}\nStop-Trigger: {label}   |   Horizont {args.horizon}W")
        print("=" * 78)
        print(f"{'Stop':>7} {'getriggert':>11} {'Ø Rendite':>11} {'Median':>9} "
              f"{'Gewinner':>9} {'Ø der Ueberlebenden':>20}")
        print("-" * 78)
        for _, r in tbl.iterrows():
            name = "kein" if pd.isna(r.stop_pct) else f"-{r.stop_pct:.0f}%"
            print(f"{name:>7} {r.getriggert_pct:>10.0f}% {r.ret_mean:>10.2f}% "
                  f"{r.ret_median:>8.2f}% {r.gewinn_pct:>8.0f}% "
                  f"{r.ueberlebende_ret_mean:>19.2f}%")

        best = tbl.dropna(subset=["stop_pct"]).sort_values("ret_mean").iloc[-1]
        log_experiment(
            tool="vcp_stop_analysis",
            params={"horizon": args.horizon, "trigger": trig,
                    "calib": args.calib or "alle"},
            metrics={"n": len(paths),
                     "best_stop_pct": float(best.stop_pct),
                     "best_ret_mean": float(best.ret_mean),
                     "no_stop_ret_mean": float(tbl.iloc[-1].ret_mean),
                     "mae_median": float(paths.mae.median() * 100),
                     "mfe_median": float(paths.mfe.median() * 100)},
            context={"csv": args.csv, "entries": len(entries)},
        )

    print("\nLesehilfe: 'Ø der Ueberlebenden' zeigt, was die nicht ausgestoppten")
    print("Trades gebracht haetten. Liegt dieser Wert deutlich ueber der Rendite")
    print("MIT Stop, kostet der Stop mehr Gewinner als er Verluste begrenzt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
