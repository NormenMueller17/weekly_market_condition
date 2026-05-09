"""
scanner.py — Kern-Screener des Zertifikate-Moduls.

Drei-Ebenen-Filter für Einstiegskandidaten:
  Ebene 1 — Trendqualität     (alle müssen erfüllt sein)
  Ebene 2 — Pullback-Erkennung (Score 0-100)
  Ebene 3 — Wiederanlauf-Bestätigung (mind. 2 von 3)

Neue Kriterien: Ebene-Funktionen erweitern oder neue _check_ebene*-Helfer hinzufügen.
Der Rückgabe-Dict jedes Kandidaten ist bewusst flach gehalten, damit report.py
und zukünftige Erweiterungen direkt auf alle Felder zugreifen können.
"""
from __future__ import annotations

import sys
import os
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from indicators import ema, rsi, macd, adx, williams_r, hv, beta, momentum


# ── Öffentliche API ──────────────────────────────────────────────────────────

def screen_universe_full(
    weekly_data: dict[str, pd.DataFrame],
    spy_weekly: pd.Series,
    rules: dict,
) -> list[dict]:
    """
    Screent das gesamte Universum OHNE Filter.
    Gibt alle Titel mit ihren 12 Metrik-Flags zurück, sortiert absteigend
    nach Anzahl erfüllter Kriterien.
    """
    e_rules = rules.get("einstieg", {})
    s_rules = rules.get("scoring", {})
    results = []

    for ticker, df in weekly_data.items():
        result = _screen_single_full(ticker, df, spy_weekly, e_rules, s_rules)
        if result is not None:
            results.append(result)

    results.sort(key=lambda x: x["kriterien_erfuellt"], reverse=True)
    return results


def screen_kandidaten(
    weekly_data: dict[str, pd.DataFrame],
    spy_weekly: pd.Series,
    rules: dict,
) -> list[dict]:
    """
    Screent das gesamte Universum und gibt eine nach Score sortierte Liste
    von Kandidaten zurück.

    Parameters
    ----------
    weekly_data : dict[ticker -> OHLCV-DataFrame]
    spy_weekly  : pd.Series  — SPY Weekly Close (für Beta-Berechnung)
    rules       : dict       — vollständiges rules.json
    """
    e_rules = rules.get("einstieg", {})
    s_rules = rules.get("scoring", {})
    kandidaten = []

    for ticker, df in weekly_data.items():
        result = _screen_single(ticker, df, spy_weekly, e_rules, s_rules)
        if result is not None:
            kandidaten.append(result)

    kandidaten.sort(key=lambda x: x["score"], reverse=True)
    max_k = int(rules.get("report", {}).get("max_kandidaten", 15))
    return kandidaten[:max_k]


def check_ausstieg(
    weekly: pd.DataFrame,
    rules: dict,
) -> dict:
    """
    Prüft Ausstiegs-Signale für eine gehaltene Position.
    Gibt ein Dict mit bool-Flags und einer Empfehlung zurück.
    Kann direkt in portfolio.py oder report.py genutzt werden.
    """
    a = rules.get("ausstieg", {})
    ma_len   = int(a.get("ma_exit", 50))
    adx_side = float(a.get("adx_sideways", 20))
    rsi_ob   = float(a.get("rsi_overbought", 70))
    wr_ob    = float(a.get("williams_r_overbought", -20))

    close = _col(weekly, "Close")
    high  = _col(weekly, "High")
    low   = _col(weekly, "Low")

    ma50 = close.rolling(ma_len).mean()
    unter_ma50 = float(close.iloc[-1]) < float(ma50.iloc[-1])

    macd_line, signal_line, _ = macd(close)
    macd_unter_null = float(macd_line.iloc[-1]) < 0
    macd_kreuz_unter = (
        float(macd_line.iloc[-1]) < float(signal_line.iloc[-1])
        and float(macd_line.iloc[-2]) >= float(signal_line.iloc[-2])
    ) if len(macd_line) >= 2 else False

    adx_val = float(adx(high, low, close).iloc[-1])
    adx_schwach = adx_val < adx_side and not _trending(close)

    rsi_val = float(rsi(close).iloc[-1])
    wr_val  = float(williams_r(high, low, close).iloc[-1])
    gewinnmitnahme = rsi_val > rsi_ob and wr_val > wr_ob

    sofort_ausstieg = (unter_ma50 and macd_kreuz_unter) or (unter_ma50 and macd_unter_null)

    flags = {
        "unter_ma50":       unter_ma50,
        "macd_unter_null":  macd_unter_null,
        "macd_kreuz_unter": macd_kreuz_unter,
        "adx_schwach":      adx_schwach,
        "gewinnmitnahme":   gewinnmitnahme,
        "sofort_ausstieg":  sofort_ausstieg,
        "rsi":              round(rsi_val, 1),
        "williams_r":       round(wr_val, 1),
        "adx":              round(adx_val, 1),
    }

    if sofort_ausstieg:
        flags["empfehlung"] = "⚠️ Sofortiger Ausstieg"
    elif unter_ma50:
        flags["empfehlung"] = "Unter 50-MA — Ausstieg prüfen"
    elif gewinnmitnahme:
        flags["empfehlung"] = "Gewinnmitnahme erwägen"
    elif adx_schwach:
        flags["empfehlung"] = "ADX schwach — beobachten"
    else:
        flags["empfehlung"] = "Trend intakt"

    return flags


# ── Interne Screening-Logik ───────────────────────────────────────────────────

def _screen_single_full(
    ticker: str,
    weekly: pd.DataFrame,
    spy_weekly: pd.Series,
    e_rules: dict,
    s_rules: dict,
) -> Optional[dict]:
    """Screent einen Titel ohne Filter — gibt immer ein Dict zurück (sofern genug Daten)."""
    try:
        close = _col(weekly, "Close")
        high  = _col(weekly, "High")
        low   = _col(weekly, "Low")
        vol   = _col(weekly, "Volume")
    except KeyError:
        return None

    if len(close) < 52:
        return None

    e1 = e_rules.get("ebene1", {})
    e2 = e_rules.get("ebene2", {})
    e3 = e_rules.get("ebene3", {})

    is_recovery = _has_recovery_path(close, e1, e2)
    e1_r = _check_ebene1(close, high, low, e1, skip_perf=is_recovery)
    e2_r = _check_ebene2_full(close, high, low, spy_weekly, e2, e1_rules=e1)
    e3_r = _check_ebene3(close, high, low, vol, e3)

    # E2 gilt als bestanden wenn entweder Pullback-Pfad ODER Recovery-Pfad aktiv
    e2_pullback_ok = e2_r["pullback_ok"] and e2_r["hv_ok"]
    e2_ok = e2_pullback_ok or e2_r["e2_recovery"]

    flags = [
        e1_r["ueber_ma200"],
        e1_r["ueber_ma50"],
        e1_r["adx_ok"],
        e1_r["perf_ok"],
        e2_ok,
        e2_r["rsi_ok"],
        e2_r["williams_ok"],
        e2_r["hv_ok"],
        e2_r["beta_ok"],
        e3_r["macd_dreht"],
        e3_r["momentum_positiv"],
        e3_r["volumen_ok"],
    ]

    return {
        "ticker":             ticker,
        "kriterien_erfuellt": sum(flags),
        "close":              round(float(close.iloc[-1]), 2),
        "e1_ma200":    e1_r["ueber_ma200"],
        "e1_ma50":     e1_r["ueber_ma50"],
        "e1_adx":      e1_r["adx_ok"],
        "e1_perf":     e1_r["perf_ok"],
        "adx_val":     round(e1_r["adx"], 1),
        "perf_52w":    round(e1_r["perf_52w_pct"], 1),
        "e2_pullback": e2_ok,
        "e2_rsi":      e2_r["rsi_ok"],
        "e2_williams": e2_r["williams_ok"],
        "e2_hv":       e2_r["hv_ok"],
        "e2_beta":     e2_r["beta_ok"],
        "e2_recovery": e2_r["e2_recovery"],
        "pullback_pct": round(e2_r["pullback_pct"], 1),
        "rsi_val":     round(e2_r["rsi"], 1),
        "williams_val": round(e2_r["williams_r"], 1),
        "hv30_val":    round(e2_r["hv30"], 1),
        "beta_val":    round(e2_r["beta"], 2),
        "e3_macd":     e3_r["macd_dreht"],
        "e3_momentum": e3_r["momentum_positiv"],
        "e3_volumen":  e3_r["volumen_ok"],
    }


def _check_ebene2_full(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    spy_close: pd.Series,
    rules: dict,
    e1_rules: dict | None = None,
) -> dict:
    """Wie _check_ebene2, aber ohne Early-Return — liefert immer alle Flags."""
    pb_min   = float(rules.get("pullback_min_pct", 5))
    pb_max   = float(rules.get("pullback_max_pct", 15))
    rsi_min  = float(rules.get("rsi_min", 40))
    rsi_max  = float(rules.get("rsi_max", 65))
    wr_min   = float(rules.get("williams_r_min", -80))
    wr_max   = float(rules.get("williams_r_max", -60))
    hv_max   = float(rules.get("hv30_max", 25))
    beta_max = float(rules.get("beta_max", 0.9))
    rec_weeks  = int(rules.get("recovery_ma_cross_weeks", 8))
    ma_long_w  = int((e1_rules or {}).get("ma_long", 40))

    current      = float(close.iloc[-1])
    recent_high  = float(high.tail(13).max())
    pullback_pct = (recent_high - current) / recent_high * 100 if recent_high > 0 else 0.0

    rsi_val = float(rsi(close).iloc[-1])
    wr_val  = float(williams_r(high, low, close).iloc[-1])
    hv30    = float(hv(close, 30).iloc[-1]) if not np.isnan(hv(close, 30).iloc[-1]) else 99.0
    b_val   = beta(close, spy_close, 52) if len(spy_close) >= 10 else np.nan

    # Recovery-Pfad prüfen
    e2_recovery = False
    if len(close) >= ma_long_w + rec_weeks:
        ma_series    = close.rolling(ma_long_w).mean()
        window_close = close.iloc[-(rec_weeks + 1):]
        window_ma    = ma_series.iloc[-(rec_weeks + 1):]
        for i in range(len(window_close) - 1):
            if (float(window_close.iloc[i]) < float(window_ma.iloc[i])
                    and float(window_close.iloc[i + 1]) >= float(window_ma.iloc[i + 1])):
                e2_recovery = True
                break

    return {
        "pullback_ok": pb_min <= pullback_pct <= pb_max,
        "rsi_ok":      rsi_min <= rsi_val <= rsi_max,
        "williams_ok": wr_min <= wr_val <= wr_max,
        "hv_ok":       hv30 < hv_max,
        "beta_ok":     (not np.isnan(b_val)) and float(b_val) < beta_max,
        "e2_recovery": e2_recovery,
        "pullback_pct": pullback_pct,
        "rsi":          rsi_val,
        "williams_r":   wr_val,
        "hv30":         hv30,
        "beta":         float(b_val) if not np.isnan(b_val) else 99.0,
    }


def _screen_single(
    ticker: str,
    weekly: pd.DataFrame,
    spy_weekly: pd.Series,
    e_rules: dict,
    s_rules: dict,
) -> Optional[dict]:
    """Screent einen einzelnen Titel. Gibt None zurück, wenn er nicht qualifiziert."""
    close = _col(weekly, "Close")
    high  = _col(weekly, "High")
    low   = _col(weekly, "Low")
    vol   = _col(weekly, "Volume")

    if len(close) < 52:
        return None

    # ── Ebene 1: Trendqualität (alle müssen passen) ──────────────────────────
    e1 = e_rules.get("ebene1", {})
    e2 = e_rules.get("ebene2", {})
    # Pre-Check Pfad B: Recovery-Kandidaten dürfen negative 52W-Perf haben
    is_recovery_candidate = _has_recovery_path(close, e1, e2)
    e1_result = _check_ebene1(close, high, low, e1, skip_perf=is_recovery_candidate)
    if not e1_result["bestanden"]:
        return None

    # ── Ebene 2: Pullback-Erkennung (Score 0-100) ────────────────────────────
    e2_result = _check_ebene2(close, high, low, spy_weekly, e2, e1_rules=e1)
    if e2_result["score"] == 0:
        return None

    # ── Ebene 3: Wiederanlauf-Bestätigung (mind. 2 von 3) ───────────────────
    e3 = e_rules.get("ebene3", {})
    e3_result = _check_ebene3(close, high, low, vol, e3)
    min_confirmed = int(e3.get("min_confirmed", 2))
    if e3_result["confirmed"] < min_confirmed:
        return None

    score = _compute_score(e2_result["score"], e3_result["confirmed"], s_rules)

    rsi_min  = float(e2.get("rsi_min", 40))
    rsi_max  = float(e2.get("rsi_max", 65))
    wr_min   = float(e2.get("williams_r_min", -80))
    wr_max   = float(e2.get("williams_r_max", -60))
    beta_max = float(e2.get("beta_max", 0.9))

    return {
        "ticker":          ticker,
        "score":           score,
        # Ebene-1-Details (alle immer True für Kandidaten)
        "close":           round(float(close.iloc[-1]), 2),
        "ma50":            round(float(close.rolling(50).mean().iloc[-1]), 2),
        "ma200":           round(float(close.rolling(200).mean().iloc[-1]), 2),
        "perf_52w_pct":    round(e1_result["perf_52w_pct"], 1),
        "adx":             round(e1_result["adx"], 1),
        # Ebene-2-Details
        "pullback_pct":    round(e2_result["pullback_pct"], 1),
        "rsi":             round(e2_result["rsi"], 1),
        "williams_r":      round(e2_result["williams_r"], 1),
        "hv30":            round(e2_result["hv30"], 1),
        "beta":            round(e2_result["beta"], 2),
        "e2_score":        round(e2_result["score"], 1),
        # Optimal-Range-Flags (für Grün-Einfärbung im Report)
        "e2_rsi_ok":       rsi_min <= e2_result["rsi"] <= rsi_max,
        "e2_williams_ok":  wr_min  <= e2_result["williams_r"] <= wr_max,
        "e2_beta_ok":      not np.isnan(e2_result["beta"]) and e2_result["beta"] < beta_max,
        # Ebene-2-Modus
        "e2_mode":         e2_result.get("mode", "pullback"),
        # Ebene-3-Details
        "e3_confirmed":    e3_result["confirmed"],
        "e3_macd_dreht":   e3_result["macd_dreht"],
        "e3_momentum_pos": e3_result["momentum_positiv"],
        "e3_volumen_ok":   e3_result["volumen_ok"],
        # Optionsschein-Empfehlung
        "os_empfehlung":   _empfehle_optionsschein(float(close.iloc[-1])),
    }


def _has_recovery_path(
    close: pd.Series,
    e1_rules: dict,
    e2_rules: dict,
) -> bool:
    """
    Schnell-Check: hat der Titel den langen MA in den letzten N Wochen von unten
    durchbrochen? Wird vor E1 aufgerufen, um E1.4 für Recovery-Kandidaten zu überspringen.
    """
    ma_long_w = int(e1_rules.get("ma_long", 40))
    rec_weeks = int(e2_rules.get("recovery_ma_cross_weeks", 8))
    if len(close) < ma_long_w + rec_weeks:
        return False
    ma_series    = close.rolling(ma_long_w).mean()
    window_close = close.iloc[-(rec_weeks + 1):]
    window_ma    = ma_series.iloc[-(rec_weeks + 1):]
    for i in range(len(window_close) - 1):
        if (float(window_close.iloc[i]) < float(window_ma.iloc[i])
                and float(window_close.iloc[i + 1]) >= float(window_ma.iloc[i + 1])):
            return True
    return False


def _check_ebene1(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    rules: dict,
    skip_perf: bool = False,
) -> dict:
    """
    Ebene 1 — Trendqualität: alle müssen bestehen.
    skip_perf=True: E1.4 (52W-Performance) wird für Recovery-Kandidaten übersprungen,
    da nach einem Crash die 52W-Performance systembedingt negativ ist.
    """
    ma_long = int(rules.get("ma_long", 200))
    ma_mid  = int(rules.get("ma_mid", 50))
    adx_min = float(rules.get("adx_min", 25))
    perf_min = float(rules.get("performance_52w_min_pct", 0))

    current = float(close.iloc[-1])

    ma200_val = float(close.rolling(ma_long).mean().iloc[-1])
    ma50_val  = float(close.rolling(ma_mid).mean().iloc[-1])

    ueber_ma200 = current > ma200_val if not np.isnan(ma200_val) else False
    ueber_ma50  = current > ma50_val  if not np.isnan(ma50_val)  else False

    adx_val = float(adx(high, low, close).iloc[-1])
    adx_ok  = not np.isnan(adx_val) and adx_val >= adx_min

    perf_52w = (current / float(close.iloc[-52]) - 1) * 100 if len(close) >= 52 else 0.0
    perf_ok  = True if skip_perf else perf_52w >= perf_min

    bestanden = ueber_ma200 and ueber_ma50 and adx_ok and perf_ok

    return {
        "bestanden":    bestanden,
        "ueber_ma200":  ueber_ma200,
        "ueber_ma50":   ueber_ma50,
        "adx":          adx_val,
        "adx_ok":       adx_ok,
        "perf_52w_pct": perf_52w,
        "perf_ok":      perf_ok,
    }


def _check_ebene2(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    spy_close: pd.Series,
    rules: dict,
    e1_rules: dict | None = None,
) -> dict:
    """
    Ebene 2 — Entweder/Oder:
      Pfad A (Pullback): Kurs 5–15% unter 3M-Hoch + W%R ≤ -50 + HV30 < 25%
      Pfad B (Recovery): Kurs hat den 40W-MA in den letzten N Wochen von unten
                         durchbrochen (MA-Kreuz nach marktbedingtem Rückgang)
    Mindestens einer der Pfade muss True sein.
    """
    pb_min  = float(rules.get("pullback_min_pct", 5))
    pb_max  = float(rules.get("pullback_max_pct", 15))
    rsi_min = float(rules.get("rsi_min", 40))
    rsi_max = float(rules.get("rsi_max", 65))
    wr_min  = float(rules.get("williams_r_min", -80))
    wr_max  = float(rules.get("williams_r_max", -60))
    hv_max  = float(rules.get("hv30_max", 25))
    beta_max = float(rules.get("beta_max", 0.9))
    wr_hard_max   = float(rules.get("williams_r_hard_max", -50))
    rec_weeks     = int(rules.get("recovery_ma_cross_weeks", 8))
    recovery_score_val = float(rules.get("recovery_score", 60))

    ma_long_w = int((e1_rules or {}).get("ma_long", 40))

    current = float(close.iloc[-1])
    recent_high = float(high.tail(13).max())
    pullback_pct = (recent_high - current) / recent_high * 100 if recent_high > 0 else 0.0

    rsi_val = float(rsi(close).iloc[-1])
    wr_val  = float(williams_r(high, low, close).iloc[-1])
    hv30    = float(hv(close, 30).iloc[-1]) if not np.isnan(hv(close, 30).iloc[-1]) else 99.0
    b_val   = beta(close, spy_close, 52) if len(spy_close) >= 10 else np.nan

    def _base(score=0, mode="none"):
        return {
            "score": score, "mode": mode,
            "pullback_pct": pullback_pct, "rsi": rsi_val,
            "williams_r": wr_val, "hv30": hv30,
            "beta": float(b_val) if not np.isnan(b_val) else 99.0,
        }

    # ── Pfad A: klassischer Pullback ─────────────────────────────────────────
    pullback_ok = pb_min <= pullback_pct <= pb_max
    wr_ok       = wr_val <= wr_hard_max
    hv_ok       = hv30 < hv_max
    path_a      = pullback_ok and wr_ok and hv_ok

    # ── Pfad B: Recovery nach marktbedingtem Rückgang ────────────────────────
    path_b = False
    if len(close) >= ma_long_w + rec_weeks:
        ma_series = close.rolling(ma_long_w).mean()
        window_close = close.iloc[-(rec_weeks + 1):]
        window_ma    = ma_series.iloc[-(rec_weeks + 1):]
        for i in range(len(window_close) - 1):
            below_before = float(window_close.iloc[i]) < float(window_ma.iloc[i])
            above_now    = float(window_close.iloc[i + 1]) >= float(window_ma.iloc[i + 1])
            if below_before and above_now:
                path_b = True
                break

    if not path_a and not path_b:
        return _base()

    # Pfad B hat Vorrang beim Modus; wenn beide, läuft A-Scoring
    mode = "pullback" if path_a else "recovery"

    if mode == "recovery":
        return _base(score=recovery_score_val, mode="recovery")

    # Pfad A — Sub-Scores
    sub_scores = {
        "pullback": _score_in_range(pullback_pct, pb_min, pb_max, ideal=(pb_min + pb_max) / 2),
        "rsi":      _score_in_range(rsi_val, rsi_min, rsi_max, ideal=(rsi_min + rsi_max) / 2),
        "williams": _score_in_range(wr_val, wr_min, wr_max, ideal=(wr_min + wr_max) / 2),
        "hv30":     max(0, (1 - hv30 / hv_max) * 100) if hv30 < hv_max * 1.5 else 0,
        "beta":     max(0, (1 - b_val / beta_max) * 100) if not np.isnan(b_val) and b_val < beta_max * 1.5 else 50,
    }
    score = sum(sub_scores.values()) / len(sub_scores)

    return {
        "score":       score,
        "mode":        "pullback",
        "sub_scores":  sub_scores,
        "pullback_pct": pullback_pct,
        "rsi":          rsi_val,
        "williams_r":   wr_val,
        "hv30":         hv30,
        "beta":         float(b_val) if not np.isnan(b_val) else 99.0,
    }


def _check_ebene3(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    vol: pd.Series,
    rules: dict,
) -> dict:
    """
    Ebene 3 — Wiederanlauf-Bestätigung (mind. 2 von 3).

    Kriterien:
      1. MACD dreht nach oben (auch wenn noch leicht negativ)
      2. Momentum-Oszillator wechselt von negativ auf positiv
      3. Bullische Wochenkerze mit überdurchschnittlichem Volumen

    Erweiterung: weiteres Kriterium als bool hinzufügen und `confirmed` erhöhen.
    """
    vol_mult = float(rules.get("volume_multiplier", 1.3))
    mom_len  = int(rules.get("momentum_length", 4))
    macd_lb  = int(rules.get("macd_lookback", 3))

    macd_line, _, _ = macd(close)
    # MACD dreht: aktuell steigend, in letzten `macd_lb` Wochen war Tief
    macd_dreht = (
        len(macd_line) >= macd_lb + 1
        and float(macd_line.iloc[-1]) > float(macd_line.iloc[-2])
        and float(macd_line.iloc[-macd_lb]) <= float(macd_line.iloc[-1])
    )

    from indicators import momentum as mom_indicator
    mom_series = mom_indicator(close, mom_len)
    momentum_positiv = (
        len(mom_series) >= 2
        and float(mom_series.iloc[-1]) > 0
        and float(mom_series.iloc[-2]) <= 0
    )

    vol_avg = float(vol.rolling(20).mean().iloc[-1]) if len(vol) >= 20 else float(vol.mean())
    bullish_candle = float(close.iloc[-1]) > float(close.iloc[-2]) if len(close) >= 2 else False
    volumen_ok = bullish_candle and float(vol.iloc[-1]) >= vol_avg * vol_mult

    confirmed = sum([macd_dreht, momentum_positiv, volumen_ok])

    return {
        "confirmed":         confirmed,
        "macd_dreht":        macd_dreht,
        "momentum_positiv":  momentum_positiv,
        "volumen_ok":        volumen_ok,
    }


# ── Scoring ───────────────────────────────────────────────────────────────────

def _compute_score(e2_score: float, e3_confirmed: int, s_rules: dict) -> float:
    """
    Gesamtscore 0-100.
    Gewichtung in rules.json/scoring konfigurierbar.
    """
    w2 = float(s_rules.get("ebene2_weight", 0.60))
    w3 = float(s_rules.get("ebene3_weight", 0.40))
    e3_score = min(e3_confirmed / 3.0, 1.0) * 100
    return round(w2 * e2_score + w3 * e3_score, 1)


def _score_in_range(value: float, lo: float, hi: float, ideal: float) -> float:
    """
    Gibt 100 zurück wenn value == ideal, 0 außerhalb [lo, hi].
    Linear interpoliert zwischen Grenzen und Ideal.
    """
    if not (lo <= value <= hi):
        return 0.0
    if value <= ideal:
        return (value - lo) / (ideal - lo) * 100 if ideal > lo else 100.0
    return (hi - value) / (hi - ideal) * 100 if hi > ideal else 100.0


# ── Optionsschein-Empfehlung ─────────────────────────────────────────────────

def _empfehle_optionsschein(kurs_basiswert: float) -> dict:
    """
    Gibt Richtwerte für einen Call-Optionsschein zurück.
    Ziel: Hebel ~3, Laufzeit 1,5 Jahre, Delta 0,6-0,7.

    Da keine echten Warrant-Preise vorliegen, werden typische Parameter
    auf Basis des Basiswertkurses berechnet.
    """
    # Strike ca. 5-10% OTM für Delta ~0,65 bei Laufzeit 18 Monate
    strike_pct = 1.07
    strike     = round(kurs_basiswert * strike_pct, 2)

    from datetime import date
    from dateutil.relativedelta import relativedelta
    laufzeit_datum = date.today() + relativedelta(months=18)

    return {
        "ziel_strike":         strike,
        "ziel_laufzeit":       laufzeit_datum.strftime("%b %Y"),
        "ziel_delta_von":      0.60,
        "ziel_delta_bis":      0.70,
        "ziel_hebel":          3,
        "hinweis": (
            f"Call OS auf {kurs_basiswert:.2f} — Strike ~{strike:.2f} "
            f"(+7%), Laufzeit ~{laufzeit_datum.strftime('%b %Y')}, "
            "Hebel ~3, Delta 0,60-0,70"
        ),
    }


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """Gibt Spalte zurück, case-insensitiv."""
    for c in df.columns:
        if c.lower() == name.lower():
            return df[c]
    raise KeyError(f"Spalte '{name}' nicht in DataFrame (Spalten: {list(df.columns)})")


def _trending(close: pd.Series, window: int = 10) -> bool:
    """Einfacher Trend-Check: Steigt der SMA10 über die letzten 5 Wochen?"""
    ma = close.rolling(window).mean()
    if len(ma.dropna()) < 5:
        return False
    return float(ma.iloc[-1]) > float(ma.iloc[-5])
