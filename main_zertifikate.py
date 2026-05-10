"""
main_zertifikate.py — Entry Point für den Zertifikate-Scanner.

Aufruf:
    python main_zertifikate.py            # Vollständiger Report
    python main_zertifikate.py --dry-run  # Nur Daten laden, kein Report/Mail

Dieser Einstiegspunkt orchestriert:
  1. Universe laden (iShares IWL / Large-Cap US)
  2. Marktdaten holen (^GSPC, ^VIX wöchentlich + SPY für Beta)
  3. Marktampel berechnen
  4. Wochendaten für alle Ticker laden
  5. Portfolio laden und Ampeln für Positionen berechnen
  6. Neukandidaten screenen (nur wenn Markt != ROT)
  7. HTML-Report generieren und speichern
  8. E-Mail versenden
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

# Windows-Konsole auf UTF-8 setzen (verhindert cp1252-Encode-Fehler bei Sonderzeichen)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yfinance as yf
import pandas as pd

# Sicherstellen, dass das Root-Verzeichnis im Pfad ist
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from http_cache import try_enable_yfinance_cache
from emailer import send_email
from data_sources import download_ohlcv_batched
from config import SETTINGS

from zertifikate.universe import load_large_cap_universe, fetch_company_info
from zertifikate.ampel import compute_marktampel, compute_zeitampel, combine, Ampel
from zertifikate.scanner import screen_kandidaten, screen_universe_full
from zertifikate.portfolio import load_portfolio, enrich_positions, portfolio_stats
from zertifikate.report import build_report, save_report, build_regelwerk_page, save_regelwerk

_RULES_PATH = ROOT / "zertifikate" / "rules.json"


def run(dry_run: bool = False) -> None:
    print(f"\n{'='*60}")
    print(f"  Zertifikate-Scanner  —  {date.today().isoformat()}")
    print(f"{'='*60}\n")

    rules = _load_rules()
    try_enable_yfinance_cache()

    report_date = date.today().isoformat()

    # ── 1. Universe laden ─────────────────────────────────────────────────────
    print("[1/7] Lade Universum …")
    tickers, csv_company_info = load_large_cap_universe(rules["universe"])

    # ── 2. Marktdaten (^GSPC + ^VIX) wöchentlich ─────────────────────────────
    print("[2/7] Lade Marktdaten (^GSPC, ^VIX, SPY) …")
    market_raw = _fetch_market_data(rules)
    gspc_close = market_raw.get("gspc")
    vix_close  = market_raw.get("vix")
    spy_close  = market_raw.get("spy")

    if gspc_close is None or vix_close is None:
        print("[FEHLER] Marktdaten konnten nicht geladen werden.")
        sys.exit(1)

    # ── 3. Marktampel ─────────────────────────────────────────────────────────
    print("[3/7] Berechne Marktampel …")
    markt = compute_marktampel(gspc_close, vix_close, rules["marktampel"])
    print(f"  → Marktampel: {markt.status.emoji} {markt.status.label} "
          f"(VIX {markt.vix:.1f}, EMA-Abstand {markt.ema_distance_pct:.1f}%)")

    # ── 4. Wochendaten für alle Ticker ────────────────────────────────────────
    print(f"[4/7] Lade Wochendaten für {len(tickers)} Titel …")
    lookback = int(rules["universe"].get("lookback_weeks", 60))
    chunk    = int(rules["universe"].get("chunk_size", 20))
    weekly_data = download_ohlcv_batched(
        tickers,
        period=f"{lookback}wk",
        interval="1wk",
        chunk_size=chunk,
    )
    print(f"  → {len(weekly_data)} Titel mit Daten.")

    # ── 5. Portfolio laden + Positionen anreichern ────────────────────────────
    print("[5/7] Lade Portfolio und berechne Positions-Ampeln …")
    portfolio = load_portfolio()
    positionen = enrich_positions(
        portfolio,
        weekly_data,
        ampel_rules=rules["einzelampel"],
        zeit_rules=rules["zeitampel"],
        ausstieg_rules=rules["ausstieg"],
    )
    roll_kandidaten = [p for p in positionen if p.get("roll_pruefen")]
    stats = portfolio_stats(portfolio)
    print(f"  → {stats['offene_positionen']} offene Positionen, "
          f"{len(roll_kandidaten)} Roll-Kandidaten.")

    # ── 6. Screenen (nur wenn Marktampel GRÜN) ───────────────────────────────
    # GELB und ROT blockieren Neukäufe — in beiden Fällen kein Screening.
    spy_series = spy_close if spy_close is not None and not spy_close.empty else pd.Series(dtype=float)
    kandidaten: list[dict] = []
    if markt.status == Ampel.GRUEN:
        print(f"[6/7] Screene Kandidaten (Markt {markt.status.label}) …")
        kandidaten = screen_kandidaten(weekly_data, spy_series, rules)
        print(f"  → {len(kandidaten)} Kandidaten nach Drei-Ebenen-Filter.")
    else:
        print(f"[6/7] Marktampel {markt.status.label} — kein Screening.")

    # ── 6b. Universum-Übersicht: alle Titel mit 12 Metriken ─────────────────
    print("[6b] Berechne Universum-Übersicht (alle Large-/Mega-Caps) …")
    universe_all = screen_universe_full(weekly_data, spy_series, rules)
    print(f"  → {len(universe_all)} Titel ausgewertet.")

    print("[6c] Lade Company-Info (Name, Sektor) …")
    company_info = fetch_company_info(
        [t["ticker"] for t in universe_all],
        seed_info=csv_company_info,
    )
    print(f"  → {len(company_info)} Titel mit Info.")

    # ── 7. Report + Regelwerk generieren ─────────────────────────────────────
    print("[7/7] Generiere Report …")
    html = build_report(
        markt, kandidaten, positionen, roll_kandidaten, report_date,
        universe_all=universe_all, company_info=company_info,
    )
    report_path = save_report(html, report_date)
    save_regelwerk(build_regelwerk_page(rules))

    if dry_run:
        print(f"\n[DRY-RUN] Report gespeichert: {report_path}")
        print("[DRY-RUN] Kein E-Mail-Versand.")
        return

    # ── E-Mail ────────────────────────────────────────────────────────────────
    subject = (
        f"{rules['report']['mail_subject']} {report_date} "
        f"— Markt {markt.status.emoji} {markt.status.label} "
        f"| {len(kandidaten)} Kandidaten"
    )
    try:
        send_email(html_body=html, subject_suffix=subject)
        print(f"[MAIL] Report gesendet an {SETTINGS.mail_to}")
    except Exception as exc:
        print(f"[MAIL][WARN] E-Mail fehlgeschlagen: {exc}")

    print(f"\n✅ Fertig — {report_path}\n")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _load_rules() -> dict:
    with open(_RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _fetch_market_data(rules: dict) -> dict[str, pd.Series | None]:
    """Lädt wöchentliche Close-Daten für S&P 500, VIX und SPY."""
    index_ticker = rules["marktampel"].get("index", "^GSPC")
    vix_ticker   = rules["marktampel"].get("vix_ticker", "^VIX")
    tickers      = [index_ticker, vix_ticker, "SPY"]

    result = {"gspc": None, "vix": None, "spy": None}

    for attempt in range(3):
        try:
            raw = yf.download(
                tickers=" ".join(tickers),
                period="5y",
                interval="1wk",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
            )
            if raw.empty:
                raise ValueError("Leerer DataFrame")

            def _extract(ticker: str) -> pd.Series | None:
                try:
                    if isinstance(raw.columns, pd.MultiIndex):
                        # Suche case-insensitiv
                        found = next(
                            (t for t in raw.columns.get_level_values(0).unique()
                             if t.upper() == ticker.upper()),
                            None,
                        )
                        if found is None:
                            return None
                        col = raw[found]
                    else:
                        col = raw
                    close_col = next(
                        (c for c in col.columns if c.lower() == "close"), None
                    )
                    return col[close_col].dropna() if close_col else None
                except Exception:
                    return None

            result["gspc"] = _extract(index_ticker)
            result["vix"]  = _extract(vix_ticker)
            result["spy"]  = _extract("SPY")
            return result

        except Exception as exc:
            print(f"[MARKT] Attempt {attempt+1}/3 fehlgeschlagen: {exc}")
            time.sleep(2 ** attempt)

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zertifikate-Scanner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur Report generieren, keine E-Mail versenden",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
