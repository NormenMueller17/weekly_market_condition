"""Quartalsumsatz und -gewinn aus der SEC-EDGAR-Schnittstelle (kostenlos, ohne Schluessel).

Warum (2026-10-03): Yahoo liefert nur 5 Quartale; fuer TTM-Wachstum gegenueber
dem Vorjahr braucht man 8. EDGAR liefert ~7 Jahre auf einmal -- und zu jeder
Zahl den Meldetag, also einen echten Zeitpunktstand fuer Backtests.

Grenzen:
  * Nur US-GAAP-Emittenten mit 10-Q. Auslandsemittenten (IMOS, SU, HAFN ...)
    melden nach IFRS oder nur halbjaehrlich -- fuer die bleibt Yahoo der Ersatz.
  * Dasselbe Umsatz-Konzept heisst je Firma/Jahr verschieden; wir probieren eine
    Rangliste durch und nehmen je Quartal den ersten Treffer.
  * Q4 steht nirgends einzeln im 10-Q und wird als Geschaeftsjahr minus Q1-Q3
    abgeleitet (nur wenn alle drei Quartale vorliegen).

SEC verlangt einen User-Agent mit Kontakt und max. 10 Anfragen/Sekunde. Den
Kontakt setzt man ueber die Umgebungsvariable SEC_USER_AGENT.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

import quarterly_store

_HERE = Path(__file__).parent
FETCH_LOG = _HERE / "docs" / "data" / "edgar_fetched.json"
CIK_CACHE = _HERE / ".cache" / "sec_company_tickers.json"

USER_AGENT = os.environ.get("SEC_USER_AGENT", "")
_MIN_INTERVAL = 0.2          # 5 Anfragen/Sekunde, weit unter dem Limit von 10
KEEP_QUARTERS = 12           # 8 fuer TTM + Reserve; haelt die CSV klein

# Rangliste: erster Treffer je Quartal gewinnt
_REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
            "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]
_NET_INCOME = ["NetIncomeLoss", "ProfitLoss"]
_EPS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"]

_last_call = [0.0]


def _get(url: str) -> requests.Response:
    if "@" not in USER_AGENT:
        raise RuntimeError("SEC_USER_AGENT fehlt: die SEC lehnt Anfragen ohne Kontaktadresse ab "
                           "(z.B. SEC_USER_AGENT='weekly-market-condition name@example.com').")
    wait = _MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)


def load_cik_map(max_age_days: int = 7) -> dict[str, int]:
    """Ticker -> CIK, lokal gecacht (die Datei aendert sich selten)."""
    if CIK_CACHE.exists() and time.time() - CIK_CACHE.stat().st_mtime < max_age_days * 86400:
        data = json.loads(CIK_CACHE.read_text(encoding="utf-8"))
    else:
        r = _get("https://www.sec.gov/files/company_tickers.json")
        r.raise_for_status()
        data = r.json()
        CIK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CIK_CACHE.write_text(json.dumps(data), encoding="utf-8")
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def _days(a: str, b: str) -> int:
    return (pd.Timestamp(a) - pd.Timestamp(b)).days


def _quarters(entries: list[dict]) -> dict[str, dict]:
    """Einzelquartale aus den SEC-Rohfakten: period_end -> {val, filed}.

    Drei-Monats-Werte (80-100 Tage) gelten direkt. Q4 = Jahreswert minus die drei
    Quartale desselben Geschaeftsjahres. `filed` ist die ERSTE Meldung (ab dann war
    die Zahl oeffentlich), der Wert stammt aus der LETZTEN (Korrekturen gelten).
    """
    by_period: dict[tuple, list[dict]] = {}
    for e in entries:
        if "start" not in e or "end" not in e or e.get("val") is None:
            continue
        by_period.setdefault((e["start"], e["end"]), []).append(e)

    def _pick(group):
        first = min(group, key=lambda x: x["filed"])
        last = max(group, key=lambda x: x["filed"])
        return {"val": float(last["val"]), "filed": first["filed"]}

    quarters, annuals = {}, []
    for (start, end), group in by_period.items():
        d = _days(end, start)
        if 80 <= d <= 100:
            quarters[end] = {**_pick(group), "start": start}
        elif 350 <= d <= 380:
            annuals.append((start, end, _pick(group)))

    for start, end, fy in annuals:
        if any(abs(_days(end, q)) <= 5 for q in quarters):
            continue                                # Q4 wurde direkt gemeldet
        inside = [q for q, v in quarters.items()
                  if _days(v["start"], start) >= -5 and _days(end, q) >= 60]
        if len(inside) == 3:
            q4 = fy["val"] - sum(quarters[q]["val"] for q in inside)
            quarters[end] = {"val": q4, "filed": fy["filed"], "start": None}
    return quarters


def _concept(facts: dict, names: list[str], unit: str) -> dict[str, dict]:
    """Je Quartal der erste Treffer aus der Konzept-Rangliste."""
    merged: dict[str, dict] = {}
    for name in names:
        entries = facts.get(name, {}).get("units", {}).get(unit)
        if not entries:
            continue
        for end, v in _quarters(entries).items():
            merged.setdefault(end, v)
    return merged


def fetch_rows(ticker: str, cik: int) -> list[dict]:
    """Quartalszeilen (quarterly_store-Format) aus EDGAR; leer wenn nicht US-GAAP."""
    r = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    if r.status_code != 200:
        return []
    facts = r.json().get("facts", {}).get("us-gaap", {})
    revenue = _concept(facts, _REVENUE, "USD")
    if not revenue:
        return []
    income = _concept(facts, _NET_INCOME, "USD")
    eps = _concept(facts, _EPS, "USD/shares")
    rows = []
    for end in sorted(revenue, reverse=True)[:KEEP_QUARTERS]:
        rows.append({
            "ticker": ticker, "period_end": end, "source": "edgar",
            "filed": revenue[end]["filed"],
            "revenue": revenue[end]["val"],
            "net_income": income.get(end, {}).get("val"),
            "eps": eps.get(end, {}).get("val"),
        })
    return rows


def _load_log() -> dict[str, str]:
    try:
        return json.loads(FETCH_LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def refresh(tickers, stale_days: int = 14, max_calls: int | None = None,
            today: date | None = None) -> dict:
    """Quartalsdaten fuer `tickers` aus EDGAR holen und in den Speicher schreiben.

    Tickers, die in den letzten `stale_days` Tagen schon abgefragt wurden, werden
    uebersprungen -- die Zahlen aendern sich nur mit einer neuen Quartalsmeldung.
    Auslandsemittenten ohne US-GAAP-Daten werden ebenfalls vermerkt, damit sie
    nicht bei jedem Lauf erneut ins Leere fragen.
    """
    today = today or date.today()
    cik_map = load_cik_map()
    log = _load_log()
    stats = {"abgefragt": 0, "uebersprungen": 0, "ohne_sec": 0, "ohne_usgaap": 0, "zeilen": 0}
    rows: list[dict] = []

    for t in dict.fromkeys(tickers):
        last = log.get(t)
        if last and (today - date.fromisoformat(last)).days < stale_days:
            stats["uebersprungen"] += 1
            continue
        if max_calls is not None and stats["abgefragt"] >= max_calls:
            break
        cik = cik_map.get(t.upper().replace("-", "."))
        if cik is None:
            stats["ohne_sec"] += 1
            log[t] = today.isoformat()
            continue
        try:
            got = fetch_rows(t, cik)
        except Exception as exc:
            print(f"[EDGAR] {t}: {exc}")
            continue                      # nicht vermerken -> naechster Lauf versucht es erneut
        stats["abgefragt"] += 1
        log[t] = today.isoformat()
        if not got:
            stats["ohne_usgaap"] += 1
        rows.extend(got)

    stats["zeilen"] = len(rows)
    if rows:
        stats["store"] = quarterly_store.upsert(rows, today)
    FETCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    FETCH_LOG.write_text(json.dumps(dict(sorted(log.items())), indent=0), encoding="utf-8")
    return stats


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="EDGAR-Quartalsdaten holen")
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--leaders-history", action="store_true",
                    help="alle Titel aus docs/data/leaders_diagnostic_*.json")
    ap.add_argument("--stale-days", type=int, default=14)
    ap.add_argument("--max-calls", type=int)
    a = ap.parse_args()
    tk = list(a.tickers)
    if a.leaders_history:
        for f in sorted((_HERE / "docs" / "data").glob("leaders_diagnostic_*.json")):
            tk += list(json.loads(f.read_text(encoding="utf-8")))
    print(refresh(tk, a.stale_days, a.max_calls))
