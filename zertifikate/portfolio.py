"""
portfolio.py — Liest und analysiert das Zertifikate-Portfolio.

Das Portfolio wird in docs/data/zertifikate_trades.json gespeichert.
Schreib-Operationen erfolgen über das GitHub Pages UI (portfolio.html).
Dieses Modul ist read-only: es lädt Positionen und berechnet Status-Metriken.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

TRADES_PATH = Path("docs/data/zertifikate_trades.json")

# ── Schema-Vorlage (für Dokumentation und portfolio.html) ────────────────────

EMPTY_PORTFOLIO: dict = {
    "version": 1,
    "last_updated": "",
    "open": [],
    "closed": [],
}

TRADE_SCHEMA = {
    # Pflichtfelder beim Kauf
    "id":                   "UUID (automatisch)",
    "basiswert":            "Ticker-Symbol, z.B. NVDA",
    "company":              "Unternehmensname",
    "schein_isin":          "ISIN des Optionsscheins, z.B. DE000XS123456",
    "schein_name":          "Produktname des Optionsscheins",
    "kauf_datum":           "YYYY-MM-DD",
    "kauf_kurs_schein":     "Kaufkurs des OS in EUR",
    "kauf_kurs_basiswert":  "Kurs des Basiswerts am Kauftag",
    "anzahl":               "Anzahl Optionsscheine",
    "investiert":           "kauf_kurs_schein × anzahl",
    "strike":               "Strike des Optionsscheins",
    "hebel_kauf":           "Hebel zum Kaufzeitpunkt (ca. 3)",
    "restlaufzeit_kauf_monate": "Restlaufzeit in Monaten zum Kaufzeitpunkt (ca. 18)",
    "faelligkeitsdatum":    "YYYY-MM-DD — Fälligkeitsdatum des OS",
    "notizen":              "Freitext",
    # Felder beim Verkauf (für closed)
    "verkauf_datum":        "YYYY-MM-DD",
    "verkauf_kurs_schein":  "Verkaufskurs des OS in EUR",
    "verkauf_kurs_basiswert": "Kurs des Basiswerts am Verkaufstag",
    "verkauf_erloes":       "verkauf_kurs_schein × anzahl",
    "gewinn_verlust":       "verkauf_erloes - investiert",
    "gewinn_verlust_pct":   "(gewinn_verlust / investiert) × 100",
    "verkauf_grund":        "z.B. Zeitwert-Rollen / Gewinnmitnahme / Stopp / Ausstieg",
}


# ── Laden ─────────────────────────────────────────────────────────────────────

def load_portfolio(path: Path = TRADES_PATH) -> dict:
    """Gibt das Portfolio-Dict zurück. Bei fehlender Datei: leeres Portfolio."""
    if not path.exists():
        return {**EMPTY_PORTFOLIO, "last_updated": date.today().isoformat()}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Fehlende Keys mit Defaults auffüllen (Rückwärtskompatibilität)
        data.setdefault("open", [])
        data.setdefault("closed", [])
        data.setdefault("version", 1)
        return data
    except Exception as exc:
        print(f"[PORTFOLIO] Fehler beim Laden: {exc}")
        return {**EMPTY_PORTFOLIO, "last_updated": date.today().isoformat()}


def save_portfolio(portfolio: dict, path: Path = TRADES_PATH) -> None:
    """Speichert das Portfolio-Dict als JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    portfolio["last_updated"] = date.today().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)
    print(f"[PORTFOLIO] Gespeichert: {path}")


# ── Positionsanalyse ──────────────────────────────────────────────────────────

def enrich_positions(
    portfolio: dict,
    weekly_data: dict,
    ampel_rules: dict,
    zeit_rules: dict,
    ausstieg_rules: dict,
    roll_rules: dict | None = None,
) -> list[dict]:
    """
    Reichert offene Positionen mit aktuellen Ampel-Werten und Ausstiegs-Signalen an.
    Gibt eine Liste von Positions-Dicts zurück, die direkt in report.py genutzt werden.

    Parameters
    ----------
    portfolio      : dict                — Ausgabe von load_portfolio()
    weekly_data    : dict[ticker -> df]  — Wochendaten für alle Ticker
    ampel_rules    : dict                — rules["einzelampel"]
    zeit_rules     : dict                — rules["zeitampel"]
    ausstieg_rules : dict                — rules["ausstieg"]
    roll_rules     : dict                — rules["rollen"]
    """
    from zertifikate.ampel import compute_einzelampel, compute_zeitampel
    from zertifikate.scanner import check_ausstieg

    roll_rules = roll_rules or {}
    hebel_min        = float(roll_rules.get("hebel_min", 2.0))
    tp_halb_pct      = float(roll_rules.get("tp_halb_pct", 40.0))
    tp_empfohlen_pct = float(roll_rules.get("tp_empfohlen_pct", 60.0))

    enriched = []
    for pos in portfolio.get("open", []):
        ticker = pos.get("basiswert", "")
        result = {**pos}

        current_close = None
        if ticker in weekly_data and not weekly_data[ticker].empty:
            df = weekly_data[ticker]
            close_col = next((c for c in df.columns if c.lower() == "close"), None)
            if close_col:
                current_close = float(df[close_col].iloc[-1])

            einzel = compute_einzelampel(df, ampel_rules)
            result["einzelampel"]        = einzel.status.value
            result["einzelampel_aktion"] = einzel.aktion()
            result["einzel_details"]     = {
                "adx":          einzel.adx_val,
                "rsi":          einzel.rsi_val,
                "above_ma50":   einzel.above_ma50,
                "macd_positiv": einzel.macd_positive,
            }
            result["ausstieg"] = check_ausstieg(df, {"ausstieg": ausstieg_rules})
        else:
            result["einzelampel"]        = "unbekannt"
            result["einzelampel_aktion"] = "Keine Daten"
            result["einzel_details"]     = {}
            result["ausstieg"]           = {"empfehlung": "Keine Daten"}

        # ── Take-Profit-Proxy (Basiswert-Performance × Hebel) ─────────────────
        result["tp_signal"] = _calc_tp_signal(
            pos, current_close, tp_halb_pct, tp_empfohlen_pct
        )

        # ── Hebel-Schätzung + Strike-Roll-Check ───────────────────────────────
        hebel_est = _calc_hebel_aktuell(pos, current_close)
        result["hebel_aktuell_est"]  = hebel_est
        result["strike_roll_signal"] = (
            hebel_est is not None and hebel_est < hebel_min
        )

        # ── Zeitampel ─────────────────────────────────────────────────────────
        faelligkeit = pos.get("faelligkeitsdatum", "")
        zeit = compute_zeitampel(faelligkeit, zeit_rules)
        result["zeitampel"]           = zeit.status.value
        result["zeitampel_aktion"]    = zeit.aktion()
        result["restlaufzeit_monate"] = zeit.restlaufzeit_monate
        result["roll_pruefen"]        = zeit.roll_pruefen or result["strike_roll_signal"]

        enriched.append(result)

    return enriched


def _calc_tp_signal(
    pos: dict,
    current_close: float | None,
    tp_halb_pct: float,
    tp_empfohlen_pct: float,
) -> dict:
    """
    Schätzt den Schein-Gewinn über Basiswert-Performance × Hebel.
    Kein echtes Options-Pricing — gibt eine Orientierung, kein exaktes Ergebnis.
    """
    kauf_basiswert = pos.get("kauf_kurs_basiswert")
    hebel          = pos.get("hebel_kauf")

    if not kauf_basiswert or not hebel or not current_close or float(kauf_basiswert) <= 0:
        return {"aktion": "—", "basis_perf_pct": None, "schein_gewinn_est_pct": None}

    basis_perf      = (current_close / float(kauf_basiswert) - 1) * 100
    schein_gewinn   = basis_perf * float(hebel)

    if schein_gewinn >= tp_empfohlen_pct:
        aktion = f"🎯 Teilverkauf empfohlen (~{schein_gewinn:.0f}% est.)"
    elif schein_gewinn >= tp_halb_pct:
        aktion = f"💰 Hälfte nehmen erwägen (~{schein_gewinn:.0f}% est.)"
    elif schein_gewinn <= -30:
        aktion = f"⚠️ Verlust (~{schein_gewinn:.0f}% est.) — Stopp prüfen"
    else:
        aktion = f"Halten (~{schein_gewinn:.0f}% est.)"

    return {
        "aktion":               aktion,
        "basis_perf_pct":       round(basis_perf, 1),
        "schein_gewinn_est_pct": round(schein_gewinn, 1),
    }


def _calc_hebel_aktuell(pos: dict, current_close: float | None) -> float | None:
    """
    Näherung: Hebel sinkt proportional zum Kursanstieg des Basiswerts.
    Hebel_akt ≈ Kurs_kauf × Hebel_kauf / Kurs_aktuell
    """
    kauf_basiswert = pos.get("kauf_kurs_basiswert")
    hebel_kauf     = pos.get("hebel_kauf")
    if not kauf_basiswert or not hebel_kauf or not current_close or float(current_close) <= 0:
        return None
    return round(float(kauf_basiswert) * float(hebel_kauf) / float(current_close), 1)


# ── Statistiken ────────────────────────────────────────────────────────────────

def portfolio_stats(portfolio: dict) -> dict:
    """Berechnet aggregierte Kennzahlen für den Report-Header."""
    open_pos   = portfolio.get("open", [])
    closed_pos = portfolio.get("closed", [])

    investiert_gesamt = sum(p.get("investiert", 0) for p in open_pos)

    realized_pl = [p.get("gewinn_verlust", 0) for p in closed_pos]
    gesamt_pl = sum(realized_pl)
    gewinne = [x for x in realized_pl if x > 0]
    verluste = [x for x in realized_pl if x <= 0]
    win_rate = len(gewinne) / len(closed_pos) * 100 if closed_pos else 0.0

    return {
        "offene_positionen":    len(open_pos),
        "geschlossene_trades":  len(closed_pos),
        "investiert_gesamt":    round(investiert_gesamt, 2),
        "realisierter_pl":      round(gesamt_pl, 2),
        "win_rate_pct":         round(win_rate, 1),
        "anzahl_gewinne":       len(gewinne),
        "anzahl_verluste":      len(verluste),
    }
