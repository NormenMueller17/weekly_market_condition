"""
ampel.py — Drei-Ampeln-System für den Zertifikate-Scanner.

Jede Ampel-Funktion ist eine reine Funktion (kein State, keine Seiteneffekte).
Neue Kriterien können in den jeweiligen _check_*-Hilfsfunktionen ergänzt werden,
ohne die Datenklassen oder den Report zu ändern.

Ampel 1 — Marktampel  (Türsteher: blockiert Neukäufe)
Ampel 2 — Einzelampel (Wächter:   pro gehaltener Position)
Ampel 3 — Zeitampel   (Uhr:       Optionsschein-Restlaufzeit)
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

# Root-Verzeichnis im Pfad, damit indicators importiert werden kann
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from indicators import ema, rsi, macd, adx, williams_r


# ── Enum ─────────────────────────────────────────────────────────────────────

class Ampel(str, Enum):
    GRUEN = "gruen"
    GELB  = "gelb"
    ROT   = "rot"

    @property
    def emoji(self) -> str:
        return {"gruen": "🟢", "gelb": "🟡", "rot": "🔴"}[self.value]

    @property
    def label(self) -> str:
        return {"gruen": "GRÜN", "gelb": "GELB", "rot": "ROT"}[self.value]


# ── Ergebnis-Datenklassen ────────────────────────────────────────────────────

@dataclass
class MarktampelResult:
    status: Ampel
    ema_fast_above_slow: bool
    index_above_200ma: bool
    vix: float
    ema_distance_pct: float
    # Erweiterungspunkt: weitere Details als Dict speichern
    details: dict = field(default_factory=dict)

    @property
    def neukauf_erlaubt(self) -> bool:
        return self.status == Ampel.GRUEN

    def aktion(self) -> str:
        if self.status == Ampel.GRUEN:
            return "Neue Longs erlaubt"
        if self.status == Ampel.GELB:
            return "Keine Neukäufe — Bestand halten"
        return "Keine Neukäufe — Risiko reduzieren"


@dataclass
class EinzelampelResult:
    status: Ampel
    above_ma50: bool
    adx_val: float
    adx_ok: bool
    macd_positive: bool
    rsi_val: float
    rsi_ok: bool
    details: dict = field(default_factory=dict)

    def aktion(self) -> str:
        if self.status == Ampel.GRUEN:
            return "Trend intakt — halten"
        if self.status == Ampel.GELB:
            return "Trend schwächt — Stopp enger setzen"
        return "Trend gebrochen — aussteigen"


@dataclass
class ZeitampelResult:
    status: Ampel
    restlaufzeit_monate: float
    faelligkeitsdatum: str
    roll_pruefen: bool
    details: dict = field(default_factory=dict)

    def aktion(self) -> str:
        if self.status == Ampel.GRUEN:
            return "Laufzeit ausreichend — kein Handlungsbedarf"
        if self.status == Ampel.GELB:
            return "Roll-Prüfung empfohlen"
        return "Rollen oder aussteigen"


@dataclass
class AmpelKombination:
    """Fasst alle drei Ampeln zusammen und leitet eine Gesamtempfehlung ab."""
    markt: MarktampelResult
    einzel: Optional[EinzelampelResult]
    zeit: Optional[ZeitampelResult]

    @property
    def gesamtaktion(self) -> str:
        m = self.markt.status
        e = self.einzel.status if self.einzel else None
        z = self.zeit.status if self.zeit else None

        # Sofortiger Ausstieg
        if e == Ampel.ROT and m == Ampel.ROT:
            return "⚠️ SOFORT AUSSTEIGEN — beide Ampeln rot"
        if e == Ampel.ROT:
            return "Aussteigen — Einzelampel rot"

        # Markt-basierte Aktionen
        if m == Ampel.ROT and e == Ampel.GRUEN:
            return "Halten mit engem Stopp — kein Aufstocken"
        if m == Ampel.GELB and e == Ampel.GRUEN:
            return "Halten — Stopp enger, keine Aufstockung"
        if m == Ampel.GRUEN and e == Ampel.GRUEN:
            if z == Ampel.ROT:
                return "Rollen oder aussteigen — Laufzeit kritisch"
            if z == Ampel.GELB:
                return "Roll-Kandidat prüfen — Trend intakt"
            return "Halten — kein Handlungsbedarf"

        return "Beobachten"

    def to_dict(self) -> dict:
        return {
            "markt_status": self.markt.status.value,
            "einzel_status": self.einzel.status.value if self.einzel else None,
            "zeit_status": self.zeit.status.value if self.zeit else None,
            "gesamtaktion": self.gesamtaktion,
        }


# ── Ampel 1: Marktampel ──────────────────────────────────────────────────────

def compute_marktampel(
    index_weekly: pd.Series,
    vix_weekly: pd.Series,
    rules: dict,
) -> MarktampelResult:
    """
    Berechnet die Marktampel aus wöchentlichen Indexdaten und VIX.

    Parameters
    ----------
    index_weekly : pd.Series  — wöchentliche Close-Preise des Index (^GSPC)
    vix_weekly   : pd.Series  — wöchentliche Close-Werte des VIX (^VIX)
    rules        : dict       — Sektion "marktampel" aus rules.json
    """
    fast  = int(rules.get("ema_fast", 10))
    slow  = int(rules.get("ema_slow", 50))
    ma_l  = int(rules.get("ma_long", 200))
    vix_g = float(rules.get("vix_green_max", 20))
    vix_r = float(rules.get("vix_red_min", 25))
    ema_yellow_pct = float(rules.get("ema_distance_yellow_pct", 1.0))

    ema_f = ema(index_weekly, fast)
    ema_s = ema(index_weekly, slow)
    ma200 = index_weekly.rolling(ma_l).mean()

    current_close = float(index_weekly.iloc[-1])
    current_ema_f = float(ema_f.iloc[-1])
    current_ema_s = float(ema_s.iloc[-1])
    current_ma200 = float(ma200.iloc[-1]) if not np.isnan(ma200.iloc[-1]) else current_close

    ema_above = current_ema_f > current_ema_s
    above_200 = current_close > current_ma200
    ema_dist_pct = abs(current_ema_f - current_ema_s) / current_ema_s * 100

    current_vix = float(vix_weekly.iloc[-1]) if len(vix_weekly) > 0 else 20.0
    vix_low  = current_vix <= vix_g
    vix_high = current_vix >= vix_r

    # ── Ampel-Logik ──────────────────────────────────────────────────────────
    # ROT: EMA10 unter EMA50, Index unter 200-MA, VIX >= 25
    if not ema_above and not above_200 and vix_high:
        status = Ampel.ROT
    # GELB: Signale gemischt oder EMA-Abstand < 1%
    elif (not ema_above or not above_200 or not vix_low) or ema_dist_pct < ema_yellow_pct:
        status = Ampel.GELB
    # GRUEN: alle drei Bedingungen erfüllt
    else:
        status = Ampel.GRUEN

    return MarktampelResult(
        status=status,
        ema_fast_above_slow=ema_above,
        index_above_200ma=above_200,
        vix=current_vix,
        ema_distance_pct=round(ema_dist_pct, 2),
        details={
            "index_close": round(current_close, 2),
            "ema_fast": round(current_ema_f, 2),
            "ema_slow": round(current_ema_s, 2),
            "ma200": round(current_ma200, 2),
            "vix_gruen_schwelle": vix_g,
            "vix_rot_schwelle": vix_r,
        },
    )


# ── Ampel 2: Einzelampel ─────────────────────────────────────────────────────

def compute_einzelampel(
    weekly: pd.DataFrame,
    rules: dict,
) -> EinzelampelResult:
    """
    Bewertet eine einzelne Aktienposition.

    Parameters
    ----------
    weekly : pd.DataFrame  — OHLCV-Wochendaten (Spalten: Open/High/Low/Close/Volume)
    rules  : dict          — Sektion "einzelampel" aus rules.json
    """
    ma_len    = int(rules.get("ma_trend", 50))
    adx_min   = float(rules.get("adx_min", 25))
    adx_side  = float(rules.get("adx_sideways", 20))
    rsi_min   = float(rules.get("rsi_min", 45))
    rsi_max   = float(rules.get("rsi_max", 65))

    close = weekly["Close"] if "Close" in weekly.columns else weekly["close"]
    high  = weekly["High"]  if "High"  in weekly.columns else weekly["high"]
    low   = weekly["Low"]   if "Low"   in weekly.columns else weekly["low"]

    ma50_series = close.rolling(ma_len).mean()
    current_close = float(close.iloc[-1])
    current_ma50  = float(ma50_series.iloc[-1]) if not np.isnan(ma50_series.iloc[-1]) else current_close

    above_ma50 = current_close > current_ma50

    adx_series = adx(high, low, close)
    current_adx = float(adx_series.iloc[-1]) if not np.isnan(adx_series.iloc[-1]) else 0.0
    adx_ok = current_adx >= adx_min

    macd_line, signal_line, _ = macd(close)
    macd_positive = float(macd_line.iloc[-1]) > 0

    rsi_series = rsi(close)
    current_rsi = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0
    rsi_ok = rsi_min <= current_rsi <= rsi_max

    # Zähle erfüllte Kriterien
    criteria_ok = sum([above_ma50, adx_ok, macd_positive, rsi_ok])
    criteria_total = 4

    # ── Ampel-Logik ──────────────────────────────────────────────────────────
    # ROT: Wochenschlusskurs unter 50-MA ODER MACD unter Nulllinie
    if not above_ma50 or not macd_positive:
        status = Ampel.ROT
    # GELB: 1-2 Indikatoren grenzwertig
    elif criteria_ok < criteria_total - 1:
        status = Ampel.GELB
    else:
        status = Ampel.GRUEN

    return EinzelampelResult(
        status=status,
        above_ma50=above_ma50,
        adx_val=round(current_adx, 1),
        adx_ok=adx_ok,
        macd_positive=macd_positive,
        rsi_val=round(current_rsi, 1),
        rsi_ok=rsi_ok,
        details={
            "close": round(current_close, 2),
            "ma50": round(current_ma50, 2),
            "criteria_ok": criteria_ok,
            "criteria_total": criteria_total,
        },
    )


# ── Ampel 3: Zeitampel ───────────────────────────────────────────────────────

def compute_zeitampel(
    faelligkeitsdatum: str,
    rules: dict,
    heute: Optional[date] = None,
) -> ZeitampelResult:
    """
    Bewertet die verbleibende Laufzeit eines Optionsscheins.

    Parameters
    ----------
    faelligkeitsdatum : str   — ISO-Format "YYYY-MM-DD"
    rules             : dict  — Sektion "zeitampel" aus rules.json
    heute             : date  — Überschreibbar für Tests
    """
    gruen_min = float(rules.get("green_min_months", 9))
    gelb_min  = float(rules.get("yellow_min_months", 5))

    if heute is None:
        heute = date.today()

    try:
        faelligkeit = datetime.strptime(faelligkeitsdatum, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        # Ungültiges Datum → konservativ als ROT behandeln
        return ZeitampelResult(
            status=Ampel.ROT,
            restlaufzeit_monate=0.0,
            faelligkeitsdatum=str(faelligkeitsdatum),
            roll_pruefen=True,
            details={"fehler": "Ungültiges Datum"},
        )

    restlaufzeit_tage = (faelligkeit - heute).days
    restlaufzeit_monate = restlaufzeit_tage / 30.44  # durchschnittliche Monatslänge

    if restlaufzeit_monate >= gruen_min:
        status = Ampel.GRUEN
    elif restlaufzeit_monate >= gelb_min:
        status = Ampel.GELB
    else:
        status = Ampel.ROT

    roll_pruefen = status in (Ampel.GELB, Ampel.ROT)

    return ZeitampelResult(
        status=status,
        restlaufzeit_monate=round(restlaufzeit_monate, 1),
        faelligkeitsdatum=faelligkeitsdatum,
        roll_pruefen=roll_pruefen,
        details={
            "faelligkeit": faelligkeitsdatum,
            "restlaufzeit_tage": restlaufzeit_tage,
            "gruen_schwelle_monate": gruen_min,
            "gelb_schwelle_monate": gelb_min,
        },
    )


# ── Kombination ──────────────────────────────────────────────────────────────

def combine(
    markt: MarktampelResult,
    einzel: Optional[EinzelampelResult] = None,
    zeit: Optional[ZeitampelResult] = None,
) -> AmpelKombination:
    return AmpelKombination(markt=markt, einzel=einzel, zeit=zeit)
