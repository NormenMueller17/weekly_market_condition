"""company_profile.py — Unternehmensportraet fuer die Kaufsignale im Boersenbrief.

Beantwortet drei Fragen je Signal:
  1. Was macht das Unternehmen?
  2. Wie entwickeln sich Umsatz und Gewinn?
  3. Warum ist es ins Depot gekommen?

Warum ein eigenes Modul und nicht in fetch_quote_data
-----------------------------------------------------
`fetch_quote_data` laeuft fuer alle Leader mit Score >= 6, also je nach Woche
fuer Dutzende Titel. Ein Portraet braucht aber nur, wer tatsaechlich gekauft
wird — das sind null bis drei pro Woche. Die Daten hier zusaetzlich in den
Hauptpfad zu haengen, waere Drosselungsrisiko ohne Gegenwert; die Historie des
Universumslaufs ist an dieser Stelle mahnend genug.

Alle Felder sind optional. Faellt ein Abruf aus, fehlt der Abschnitt — der
Brief wird trotzdem gebaut. Ein Portraet ist Beiwerk; es darf den Versand des
Reports nicht gefaehrden.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd
import yfinance as yf

# Zeilennamen im Income Statement schwanken je nach Datenlieferant.
_UMSATZ_ZEILEN = ["Total Revenue", "TotalRevenue", "OperatingRevenue", "Operating Revenue"]
_GEWINN_ZEILEN = ["Net Income", "NetIncome", "Net Income Common Stockholders",
                  "NetIncomeCommonStockholders"]
_EPS_ZEILEN    = ["Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS"]


def _zeile(stmt: pd.DataFrame, kandidaten: list[str]) -> Optional[pd.Series]:
    if stmt is None or not isinstance(stmt, pd.DataFrame) or stmt.empty:
        return None
    index = {str(i).strip().lower(): i for i in stmt.index}
    for name in kandidaten:
        treffer = index.get(name.strip().lower())
        if treffer is not None:
            s = pd.to_numeric(stmt.loc[treffer], errors="coerce").dropna()
            if not s.empty:
                return s.sort_index()          # aelteste zuerst
    return None


def _kurzfassung(text: str, max_saetze: int = 3, max_zeichen: int = 420) -> str:
    """Die ersten Saetze der Unternehmensbeschreibung.

    yfinance liefert `longBusinessSummary` als Fliesstext von oft 1500+ Zeichen.
    Fuer den Brief reichen die ersten Saetze — wer mehr will, klickt auf den
    StockAnalysis-Link.
    """
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    saetze = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in saetze[:max_saetze]:
        if len(out) + len(s) > max_zeichen:
            break
        out += (" " if out else "") + s
    if not out:
        out = text[:max_zeichen]
    if len(out) < len(text):
        out = out.rstrip(" .") + " …"
    return out


def _reihe(qs: pd.DataFrame, zeilen: list[str], n: int = 5,
           lag: int = 4) -> list[dict]:
    """Letzte `n` Perioden mit Wert und Veraenderung zur Vorjahresperiode.

    `lag` ist der Abstand zur Vergleichsperiode: 4 fuer Quartale (das Quartal
    vor einem Jahr, nicht das Vorquartal — bei saisonalem Geschaeft waere der
    Quartalsvergleich irrefuehrend), 1 fuer Jahre.

    Fehlt die Vergleichsperiode, bleibt `yoy` leer statt geraten. yfinance
    liefert oft nur vier bis fuenf Quartale, dann traegt nur das juengste einen
    Vorjahresvergleich — das ist eine Datengrenze, keine Rechenluecke.
    """
    s = _zeile(qs, zeilen)
    if s is None or len(s) < 2:
        return []
    werte = list(s.items())
    out = []
    for i in range(len(werte) - 1, -1, -1):
        datum, wert = werte[i]
        yoy = None
        if i - lag >= 0:
            vergleich = werte[i - lag][1]
            if vergleich and vergleich > 0:
                yoy = (wert / vergleich - 1) * 100
        out.append({
            "periode": pd.Timestamp(datum).strftime("%Y-%m"),
            "wert":    float(wert),
            "yoy":     yoy,
        })
        if len(out) >= n:
            break
    return list(reversed(out))


def fetch_profile(ticker: str) -> dict:
    """Portraet-Rohdaten fuer einen Titel. Fehlende Felder bleiben leer."""
    profil: dict = {
        "ticker": ticker, "beschreibung": "", "sektor": None, "industrie": None,
        "land": None, "mitarbeiter": None, "webseite": None,
        "umsatz_q": [], "gewinn_q": [], "eps_q": [],
        "umsatz_j": [], "waehrung": None,
    }
    try:
        tkr  = yf.Ticker(ticker)
        info = tkr.info or {}
    except Exception as e:
        print(f"[PROFIL] {ticker}: info nicht abrufbar: {e}")
        return profil

    profil["beschreibung"] = _kurzfassung(info.get("longBusinessSummary", ""))
    profil["sektor"]       = info.get("sector")
    profil["industrie"]    = info.get("industry")
    profil["land"]         = info.get("country")
    profil["webseite"]     = info.get("website")
    profil["waehrung"]     = info.get("financialCurrency") or info.get("currency")
    try:
        n = info.get("fullTimeEmployees")
        profil["mitarbeiter"] = int(n) if n else None
    except (TypeError, ValueError):
        pass

    try:
        qs = getattr(tkr, "quarterly_income_stmt", None)
        if qs is None or (isinstance(qs, pd.DataFrame) and qs.empty):
            hole = getattr(tkr, "get_income_stmt", None)
            qs = hole(freq="quarterly") if callable(hole) else None
        profil["umsatz_q"] = _reihe(qs, _UMSATZ_ZEILEN, lag=4)
        profil["gewinn_q"] = _reihe(qs, _GEWINN_ZEILEN, lag=4)
        profil["eps_q"]    = _reihe(qs, _EPS_ZEILEN, lag=4)
    except Exception as e:
        print(f"[PROFIL] {ticker}: Quartalszahlen nicht abrufbar: {e}")

    try:
        ys = getattr(tkr, "income_stmt", None)
        if ys is None or (isinstance(ys, pd.DataFrame) and ys.empty):
            hole = getattr(tkr, "get_income_stmt", None)
            ys = hole(freq="yearly") if callable(hole) else None
        profil["umsatz_j"] = _reihe(ys, _UMSATZ_ZEILEN, n=4, lag=1)
    except Exception as e:
        print(f"[PROFIL] {ticker}: Jahreszahlen nicht abrufbar: {e}")

    return profil


def fetch_profiles(tickers: list[str]) -> dict:
    """Portraets fuer mehrere Titel. Einzelfehler kippen den Rest nicht."""
    out = {}
    for t in tickers:
        try:
            out[t] = fetch_profile(t)
        except Exception as e:
            print(f"[PROFIL] {t}: uebersprungen ({e})")
    return out


# ── Kaufbegruendung ───────────────────────────────────────────────────────────

def kaufbegruendung(sig) -> list[str]:
    """Warum dieser Titel ins Depot kommt — aus den Zahlen des Signals selbst.

    Kein erzaehlter Text, sondern die Kriterien, die den Kauf ausgeloest haben.
    Wer in einem halben Jahr wissen will, warum eine Position da ist, findet
    hier dieselbe Begruendung, die das Regelwerk benutzt hat.
    """
    g = []
    if sig.rs_score is not None:
        satz = f"Relative Stärke {sig.rs_score:.0f} von 99"
        if sig.rs_delta_4w is not None:
            satz += f" ({sig.rs_delta_4w:+.0f} in vier Wochen)"
        g.append(satz + " — gehört damit zu den stärksten Titeln im Universum.")
    if sig.industry_ranking is not None:
        g.append(f"Industrie auf Rang {sig.industry_ranking} — nur die Top 50 sind zugelassen.")
    if sig.revenue_growth is not None:
        g.append(f"Umsatzwachstum {sig.revenue_growth:+.0f} % gegenüber dem Vorjahr (Hürde: 20 %).")
    if sig.eps_growth_last_q is not None:
        g.append(f"Gewinn je Aktie im letzten Quartal {sig.eps_growth_last_q:+.0f} % "
                 f"gegenüber dem Vorjahresquartal (Hürde: 20 %).")
    if sig.pattern and sig.pattern not in ("–", "-", ""):
        g.append(f"Chartmuster: {sig.pattern}.")
    if sig.dist_52w_high_pct is not None:
        g.append(f"Nur {sig.dist_52w_high_pct:.1f} % unter dem 52-Wochen-Hoch — "
                 f"der Titel führt, statt aufzuholen.")
    if sig.vol_score is not None:
        g.append(f"Ausbruchsvolumen {sig.vol_score:.2f}× über dem 20-Tage-Schnitt.")
    if getattr(sig, "is_reentry", False):
        g.append(f"Wiedereinstieg (Versuch {getattr(sig, 'reentry_attempt', 2)}): der Titel "
                 f"hat den Pivot zurückerobert, an dem er zuvor ausgestoppt wurde.")
    g.append(f"Risiko {sig.risk_on_equity_pct * 100:.2f} % des Depots — Stop bei "
             f"${sig.stop_loss:.2f}, also {sig.stop_loss_pct * 100:.1f} % unter dem Einstieg.")
    return g
