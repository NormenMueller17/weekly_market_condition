"""Prueft, ob die Umsatzhuerde mit TTM-Wachstum statt Quartals-YoY besser waere.

Anlass (2026-10-03): IMOS bestand die 20-%-Huerde mit +28,7 % (letztes Quartal
gegen Vorjahresquartal), TTM gegen TTM waere nur +18,7 % gewesen. Der Nutzer
vermutet, TTM glaette Saisonzyklen besser. Vorher war das nicht pruefbar, weil
Yahoo nur 5 Quartale liefert; `quarterly_store` (EDGAR, mit Meldetag) macht es
jetzt zeitpunktgenau moeglich.

Methode (ohne Vorausschau): Je Journal-Trade nur Quartale, die am Einstiegstag
schon gemeldet waren (`filed <= entry_date`).
  quartal_yoy = Umsatz juengstes Quartal / Umsatz 4 Quartale davor - 1
  ttm_yoy     = Summe der letzten 4 Quartale / Summe der 4 davor - 1
Verglichen werden beide gegen das reale Ergebnis (realized/unrealized P&L %).

  python ttm_revenue_analysis.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import quarterly_store

HURDLE = 20.0


def quarter_yoy(df: pd.DataFrame, ticker: str, asof: str) -> float | None:
    q = df[(df["ticker"] == ticker)].dropna(subset=["revenue"]).copy()
    q = q[q["filed"].fillna(q["first_seen"]) <= asof].sort_values("period_end", ascending=False)
    if len(q) < 5:
        return None
    new, old = float(q.iloc[0]["revenue"]), float(q.iloc[4]["revenue"])
    return None if old <= 0 else (new / old - 1.0) * 100.0


def main() -> int:
    df = quarterly_store.load()
    if df.empty:
        print("Keine Quartalsdaten -- zuerst `python sec_edgar.py --leaders-history` laufen lassen.")
        return 1
    tr = json.loads(Path("docs/data/trades.json").read_text(encoding="utf-8"))
    rows = []
    for kind in ("closed", "open"):
        for r in tr[kind]:
            pl = r.get("realized_plpc") if kind == "closed" else r.get("unrealized_plpc")
            rows.append({"symbol": r["symbol"], "entry": r["entry_date"], "pl": pl})
    T = pd.DataFrame(rows)
    T["q_yoy"] = [quarter_yoy(df, s, e) for s, e in zip(T.symbol, T.entry)]
    T["ttm_yoy"] = [quarterly_store.ttm_growth(s, asof=e, df=df) for s, e in zip(T.symbol, T.entry)]

    print(f"Trades gesamt: {len(T)} | mit Quartals-YoY: {T.q_yoy.notna().sum()} | "
          f"mit TTM: {T.ttm_yoy.notna().sum()}")
    V = T.dropna(subset=["q_yoy", "ttm_yoy", "pl"]).copy()
    print(f"Vergleichbar (beides vorhanden): {len(V)}\n")
    if V.empty:
        return 0

    V["q_ok"] = V.q_yoy >= HURDLE
    V["ttm_ok"] = V.ttm_yoy >= HURDLE
    print(V.sort_values("entry")[["symbol", "entry", "q_yoy", "ttm_yoy", "pl"]]
          .round(1).to_string(index=False), "\n")

    def _grp(mask, label):
        g = V[mask]
        if g.empty:
            print(f"  {label:<34} n=0")
            return
        print(f"  {label:<34} n={len(g):>2}  Ø {g.pl.mean():+6.1f} %  "
              f"Median {g.pl.median():+6.1f} %  Treffer {(g.pl > 0).mean():.0%}")

    print(f"Huerde {HURDLE:.0f} % -- Ergebnis je Gruppe:")
    _grp(V.q_ok & V.ttm_ok, "beide bestanden")
    _grp(V.q_ok & ~V.ttm_ok, "nur Quartal bestanden (TTM faellt)")
    _grp(~V.q_ok & V.ttm_ok, "nur TTM bestanden")
    _grp(~V.q_ok & ~V.ttm_ok, "beide durchgefallen")
    print()
    for col in ("q_yoy", "ttm_yoy"):
        rho = V[[col, "pl"]].corr(method="spearman").iloc[0, 1]
        print(f"  Spearman {col:<8} vs. Ergebnis: {rho:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
