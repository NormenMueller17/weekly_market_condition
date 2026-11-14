"""Erkennt Kaufkandidaten, fuer die eine Uebernahme laeuft oder kuerzlich abgeschlossen wurde.

Quelle ist die SEC-Einreichungsliste (data.sec.gov/submissions) -- kostenlos,
ohne Schluessel. Ausgewertet werden Formulare, die das ZIEL einer Uebernahme
einreicht. Bewusst NICHT: 8-K Item 2.01 (kommt auch vom Kaeufer) und S-4 (Kaeufer
gibt eigene Aktien aus).

  PREM14A / DEFM14A  Fusionsvertrag, Aktionaersabstimmung (Ziel)
  SC 14D9            Stellungnahme des Ziels zu einem Uebernahmeangebot
  SC 13E3            Going-Private-Transaktion

Luecke (gefunden an BMBL/Ithaca, 2026-10-13): Wenn Mehrheitsaktionaere per
schriftlicher Zustimmung statt Abstimmung/Tender zustimmen, entfaellt jedes der
drei Formulare oben -- die einzige Spur ist ein 8-K Item 1.01 ("Entry into a
Material Definitive Agreement") des Ziels selbst. Item 1.01 wird aber auch vom
KAEUFER gemeldet (z.B. SWKS bei der QRVO-Uebernahme, 8-K vom 2026-08-10, dort
zur Finanzierung) und von gewoehnlichen Vertragsabschluessen. Ein solches 8-K
zaehlt daher nur als Treffer, wenn der Dokumenttext die fuer das ZIEL typische
Formulierung enthaelt: "... surviving [the Merger] as a wholly owned subsidiary
of <Kaeufer>" (SUBSIDIARY_PATTERN) -- das steht nur im eigenen 8-K des Ziels,
nie im Finanzierungs-/Strategie-8-K des Kaeufers.

Grenzen: Auslandsemittenten ohne US-Meldepflicht sind nicht abgedeckt; ein Angebot,
das nie eingereicht wird (auch nicht per 8-K), erkennt die Methode nicht.

Verwendung: Der Wochenlauf reicht die Kandidaten an generate_signals() weiter,
das verwirft sie mit Begruendung. Die Abfrage ist pro Titel einmal je Woche
gecacht (docs/data/takeover_check.json).
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

import sec_edgar

TARGET_FORMS = ("PREM14A", "DEFM14A", "SC 14D9", "SC 13E3")
MERGER_8K_ITEM = "1.01"
# Nur das ZIEL beschreibt sich selbst so ("the Company" [= der Registrant]
# "surviving ... as a wholly owned subsidiary of <Erwerber>"). Reicht der
# KAEUFER das 8-K ein (z.B. SWKS zur QRVO-Uebernahme), wird dort nicht "the
# Company" sondern die aufgekaufte Gegenseite ("such subsidiary", "Qorvo", ...)
# zur surviving entity -- "the Company" bleibt in diesem Satz unverbunden mit
# "surviving", daher kein Treffer.
SUBSIDIARY_PATTERN = re.compile(
    r"\bthe Company\b[^.]{0,100}?\bsurviv\w*\b[^.]{0,150}?\bwholly[\s-]owned subsidiary of",
    re.I | re.S,
)
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


def _safe(lst: list, i: int) -> str:
    return lst[i] if i < len(lst) else ""


def _merger_8k_text_hit(cik: int, accession: str, primary_doc: str) -> bool:
    """True, wenn das 8-K-Dokument die Ziel-typische Subsidiary-Formulierung
    enthaelt (siehe Moduldoc) -- unterscheidet Ziel von Kaeufer."""
    if not accession or not primary_doc:
        return False
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{primary_doc}"
    try:
        r = sec_edgar._get(url)
    except Exception:
        return False
    if r.status_code != 200:
        return False
    text = re.sub(r"<[^>]+>", " ", r.text)
    return SUBSIDIARY_PATTERN.search(text) is not None


def _treffer(ticker: str, cik: int, today: date) -> dict | None:
    """Juengste Ziel-Einreichung im Lookback, oder None (siehe Moduldoc fuer die
    8-K-Erweiterung bei Deals per schriftlicher Aktionaers-Zustimmung)."""
    r = sec_edgar._get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if r.status_code != 200:
        return None
    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    cutoff = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()

    best = None
    for i, (form, d) in enumerate(zip(forms, dates)):
        if d < cutoff or (best is not None and d <= best["date"]):
            continue
        if form in TARGET_FORMS:
            best = {"form": form, "date": d}
        elif form == "8-K" and MERGER_8K_ITEM in _safe(items, i).split(","):
            if _merger_8k_text_hit(cik, _safe(accessions, i), _safe(primary_docs, i)):
                best = {"form": "8-K (Item 1.01, Fusionsstichwort)", "date": d}
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
