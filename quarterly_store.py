"""Quartalsdaten (Umsatz, Nettogewinn, EPS) dauerhaft sammeln.

Anlass (2026-10-03): Yahoo liefert nur die letzten 5 Quartale. Fuer TTM-Wachstum
gegenueber dem Vorjahr braucht man 8, und fuer Backtests ohne Vorausschau muss
man wissen, was zum Kaufzeitpunkt BEKANNT war. Beides geht nur mit einer
eigenen Historie, die bei jedem Lauf fortgeschrieben wird.

Format: eine CSV im Repo (docs/data/quarterly_fundamentals.csv), eine Zeile je
(ticker, period_end). Bewusst CSV statt SQLite: Der Workflow committet docs/
automatisch, und eine Textdatei bleibt im Diff lesbar und rebase-faehig, eine
Binaerdatei kollidiert bei parallelen Commits. Bei ~20-30k Zeilen ist die
Datei klein genug, um sie bei jedem Lauf komplett neu zu schreiben.

Spalten:
  ticker, period_end      Schluessel
  revenue, net_income, eps  Werte in Berichtswaehrung (fuer Wachstum egal)
  source                  "edgar" (SEC, amtlich) oder "yahoo" (Ersatz fuer Auslandsemittenten)
  filed                   Meldetag bei der SEC (nur edgar) -- echter Zeitpunktstand
  first_seen              Erster Lauf, in dem Yahoo das Quartal lieferte
                          (Yahoo nennt nur das Quartalsende, nicht den Meldetag;
                          first_seen ist die ehrliche Naeherung fuer "bekannt ab")
  last_updated            Letzte Aenderung eines Wertes
  revisions               Wie oft Yahoo den Umsatz nachtraeglich korrigiert hat
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

STORE_PATH = Path(__file__).parent / "docs" / "data" / "quarterly_fundamentals.csv"
COLUMNS = ["ticker", "period_end", "revenue", "net_income", "eps",
           "source", "filed", "first_seen", "last_updated", "revisions"]

# Quellen-Rangfolge: EDGAR (amtlich, mit Meldetag) schlaegt Yahoo. Yahoo bleibt
# fuer Auslandsemittenten, die keine US-GAAP-Quartalsdaten bei der SEC haben.
_SOURCE_RANK = {"edgar": 2, "yahoo": 1}
# Dieselbe Quartalszahl heisst bei Yahoo und SEC oft um ein paar Tage verschieden
# (52/53-Wochen-Jahre, z.B. 27.06. vs. 30.06.) -- als gleiches Quartal behandeln
_SAME_QUARTER_DAYS = 10

_REVENUE_ROWS = ["Total Revenue", "TotalRevenue", "Operating Revenue", "OperatingRevenue", "Revenue"]
_INCOME_ROWS  = ["Net Income", "NetIncome", "Net Income Common Stockholders"]
_EPS_ROWS     = ["Diluted EPS", "Basic EPS", "Earnings Per Share"]

# Umsatzkorrekturen unter dieser Schwelle sind Rundung, keine Revision
_REVISION_TOLERANCE = 0.005


def _row(stmt: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    for name in candidates:
        if name in stmt.index:
            return pd.to_numeric(stmt.loc[name], errors="coerce")
    return None


def rows_from_stmt(ticker: str, stmt) -> list[dict]:
    """Quartalszeilen aus yfinance' `quarterly_income_stmt` (Spalten = Quartalsenden)."""
    if stmt is None or getattr(stmt, "empty", True):
        return []
    rev, inc, eps = (_row(stmt, c) for c in (_REVENUE_ROWS, _INCOME_ROWS, _EPS_ROWS))
    out = []
    for col in stmt.columns:
        vals = {k: (None if s is None or pd.isna(s.get(col)) else float(s.get(col)))
                for k, s in (("revenue", rev), ("net_income", inc), ("eps", eps))}
        if vals["revenue"] is None and vals["eps"] is None:
            continue  # leere Randspalte
        out.append({"ticker": ticker, "period_end": pd.Timestamp(col).date().isoformat(),
                    "source": "yahoo", **vals})
    return out


def load(path: Path = STORE_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype={"ticker": str, "period_end": str, "source": str,
                                  "filed": str, "first_seen": str, "last_updated": str})
    if "source" not in df.columns:
        df["source"] = "yahoo"          # Altbestand stammt aus der Yahoo-Zeit
    return df.reindex(columns=COLUMNS)


def upsert(rows: list[dict], today: date | None = None, path: Path = STORE_PATH) -> dict:
    """Neue Quartale anhaengen, bekannte aktualisieren. Gibt Zaehler zurueck."""
    stats = {"neu": 0, "geaendert": 0, "revidiert": 0}
    if not rows:
        return stats
    today_s = (today or date.today()).isoformat()
    df = load(path)
    records = df.to_dict("records")
    by_ticker: dict[str, list[int]] = {}
    for i, rec in enumerate(records):
        by_ticker.setdefault(rec["ticker"], []).append(i)

    def _find(ticker: str, period_end: str) -> int | None:
        target = pd.Timestamp(period_end)
        for i in by_ticker.get(ticker, []):
            if abs((pd.Timestamp(records[i]["period_end"]) - target).days) <= _SAME_QUARTER_DAYS:
                return i
        return None

    for r in rows:
        r = {"source": "yahoo", **r}
        i = _find(r["ticker"], r["period_end"])
        if i is None:
            records.append({**{c: None for c in COLUMNS}, **r,
                            "first_seen": today_s, "last_updated": today_s, "revisions": 0})
            by_ticker.setdefault(r["ticker"], []).append(len(records) - 1)
            stats["neu"] += 1
            continue
        old = records[i]
        old_rank = _SOURCE_RANK.get(str(old.get("source")), 0)
        new_rank = _SOURCE_RANK.get(r["source"], 0)
        if new_rank < old_rank:
            continue                       # Yahoo ueberschreibt nie EDGAR
        changed = False
        if new_rank > old_rank:            # EDGAR loest Yahoo-Zeile ab (Datum der SEC gilt)
            old.update({"source": r["source"], "period_end": r["period_end"],
                        "filed": r.get("filed")})
            changed = True
        elif r.get("filed") and old.get("filed") != r["filed"]:
            old["filed"] = r["filed"]
        for col in ("revenue", "net_income", "eps"):
            new = r.get(col)
            if new is None:
                continue  # Luecke ueberschreibt nie einen bekannten Wert
            prev = old.get(col)
            if prev is None or pd.isna(prev) or float(prev) != new:
                if col == "revenue" and prev is not None and not pd.isna(prev) and prev:
                    if abs(new - prev) / abs(prev) > _REVISION_TOLERANCE:
                        old["revisions"] = int(old.get("revisions") or 0) + 1
                        stats["revidiert"] += 1
                old[col] = new
                changed = True
        if changed:
            old["last_updated"] = today_s
            stats["geaendert"] += 1

    out = pd.DataFrame(records, columns=COLUMNS).sort_values(["ticker", "period_end"])
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return stats


def ttm_growth(ticker: str, asof: str | None = None, df: pd.DataFrame | None = None) -> float | None:
    """TTM-Umsatz gegen TTM-Umsatz des Vorjahres in %, None solange < 8 Quartale.

    asof (ISO-Datum): nur Quartale, die an dem Tag schon in der Datenbank
    standen (first_seen <= asof) -- fuer Backtests ohne Vorausschau.
    Die acht Quartale muessen lueckenlos aufeinander folgen (je ~3 Monate).
    """
    df = load() if df is None else df
    q = df[df["ticker"] == ticker].dropna(subset=["revenue"])
    if asof:
        known = q["filed"].fillna(q["first_seen"])   # Meldetag, sonst erster Abruf
        q = q[known <= asof]
    q = q.sort_values("period_end", ascending=False).head(8)
    if len(q) < 8:
        return None
    ends = pd.to_datetime(q["period_end"]).tolist()
    if any(not 75 <= (a - b).days <= 105 for a, b in zip(ends, ends[1:])):
        return None  # Luecke in der Folge -- lieber nichts als eine falsche TTM
    rev = q["revenue"].tolist()
    now, before = sum(rev[:4]), sum(rev[4:])
    return None if before <= 0 else (now / before - 1.0) * 100.0
