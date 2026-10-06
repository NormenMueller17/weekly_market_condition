"""Erkennt Kaufkandidaten, fuer die eine Uebernahme laeuft oder kuerzlich abgeschlossen wurde.

Quelle ist die SEC-Einreichungsliste (data.sec.gov/submissions) -- kostenlos,
ohne Schluessel. Ausgewertet werden nur Formulare, die das ZIEL einer Uebernahme
einreicht. Bewusst NICHT: 8-K Item 2.01 (kommt auch vom Kaeufer) und S-4 (Kaeufer
gibt eigene Aktien aus).

  PREM14A / DEFM14A  Fusionsvertrag, Aktionaersabstimmung (Ziel)
  SC 14D9            Stellungnahme des Ziels zu einem Uebernahmeangebot
  SC 13E3            Going-Private-Transaktion

Grenzen: Auslandsemittenten ohne US-Meldepflicht sind nicht abgedeckt; ein Angebot,
das nie eingereicht wird, erkennt die Methode nicht.

Verwendung: Der Wochenlauf reicht die Kandidaten an generate_signals() weiter,
das verwirft sie mit Begruendung. Die Abfrage ist pro Titel einmal je Woche
gecacht (docs/data/takeover_check.json).
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import requests

import sec_edgar

TARGET_FORMS = ("PREM14A", "DEFM14A", "SC 14D9", "SC 13E3")
LOOKBACK_DAYS = 365          # laenger zurueck = Deal laengst abgeschlossen oder aufgegeben
CACHE_DAYS = 7
CACHE_FILE = Path(__file__).parent / "docs" / "data" / "takeover_check.json"


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(dict(sorted(cache.items())), indent=0), encoding="utf-8")


def _treffer(ticker: str, cik: int, today: date) -> dict | None:
    """Juengste Ziel-Einreichung im Lookback, oder None."""
    r = sec_edgar._get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if r.status_code != 200:
        return None
    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    cutoff = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    best = None
    for form, d in zip(forms, dates):
        if form in TARGET_FORMS and d >= cutoff and (best is None or d > best["date"]):
            best = {"form": form, "date": d}
    return best


def pruefe(tickers, today: date | None = None) -> dict[str, str]:
    """Ticker -> Begruendung (z. B. 'DEFM14A, 2026-09-30') fuer Titel mit laufender
    oder juengster Uebernahme. Titel ohne Treffer fehlen im Ergebnis.
    """
    today = today or date.today()
    cik_map = sec_edgar.load_cik_map()
    cache = _load_cache()
    out: dict[str, str] = {}
    geaendert = False

    for t in dict.fromkeys(tickers):
        entry = cache.get(t)
        if entry and (today - date.fromisoformat(entry["checked"])).days < CACHE_DAYS:
            hit = entry.get("hit")
        else:
            cik = cik_map.get(t.upper().replace("-", "."))
            if cik is None:
                hit = None            # kein SEC-Eintrag (Auslandsemittent): kein Befund
            else:
                try:
                    h = _treffer(t, cik, today)
                except Exception as exc:
                    print(f"[UEBERNAHME] {t}: Abfrage fehlgeschlagen ({exc}) -- Titel nicht geprueft")
                    continue          # nicht cachen, naechster Lauf versucht es erneut
                hit = f"{h['form']}, {h['date']}" if h else None
            cache[t] = {"checked": today.isoformat(), "hit": hit}
            geaendert = True
        if hit:
            out[t] = hit

    if geaendert:
        _save_cache(cache)
    return out


if __name__ == "__main__":
    import sys
    res = pruefe(sys.argv[1:])
    for t, why in res.items():
        print(f"{t}: {why}")
    print(f"{len(res)} von {len(sys.argv) - 1} mit Uebernahme-Einreichung")
