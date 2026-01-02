from __future__ import annotations
import pandas as pd
import numpy as np
import math


def detect_vcp(
    df: pd.DataFrame,
    window: int = 60,
    n_segments: int = 4,
    max_close_to_resistance: float = 0.04,   # war 0.03
    min_contraction: float = 0.65,           # war 0.55
    max_pullback: float = 0.20,              # war 0.15
    ) -> dict:
    """
    Verbesserte VCP-Detektion (Variante B) nach Minervini.
    Rückgabe ist IMMER ein Dictionary, damit screener.py stabil bleibt.
    
    Parameters
    ----------
    df : pd.DataFrame
        OHLCV-Daten (Close, High, Low, Volume)
    window : int
        Anzahl der Bars für die VCP-Base-Analyse
    n_segments : int
        Anzahl der Segmente für Pullback-/Kontraktions-Analyse
    max_close_to_resistance : float
        Max. Abstand des aktuellen Kurses zum Widerstand (5% = 0.05)
    min_contraction : float
        Min. Volatilitätskontraktion (70% = 0.70 bedeutet: letzte Spread max. 70% der ersten)
    max_pullback : float
        Max. erlaubter Pullback pro Segment (30% = 0.30)
        
    Returns
    -------
    dict
        {
            "VCP": bool,
            "Waves": int,
            "Entry_Signal": bool,
            "Breakout_Level": float | None
    """

    # --- Standard-Result falls etwas schief geht ---
    result = {
        "VCP": False,
        "Waves": 0,
        "Entry_Signal": False,
        "Breakout_Level": None,
    }

    # --- Basischecks ---
    if df is None or df.empty:
        return result

    required = {"Close", "High", "Low", "Volume"}
    if not required.issubset(df.columns):
        return result

    df = df.dropna().copy()
    if len(df) < window:
        return result

    # Letzte „window“-Bars extrahieren
    data = df.tail(window).copy()
    close = data["Close"].astype(float)
    high = data["High"].astype(float)
    low = data["Low"].astype(float)
    vol = data["Volume"].astype(float)

    last_close = float(close.iloc[-1])
    if not math.isfinite(last_close):
        return result

    # -----------------------------------------------------------------------
    # 1) Widerstands-/Pivotlevel bestimmen (oberes 90. Perzentil)
    # -----------------------------------------------------------------------
    resistance = float(close.quantile(0.9))
    if not math.isfinite(resistance) or resistance <= 0:
        return result

    # Kurs muss in Breakout-Nähe sein (Variante B erlaubt 3 % Puffer)
    rel_dist = abs(last_close / resistance - 1.0)
    if rel_dist > max_close_to_resistance:
        return result

    # -----------------------------------------------------------------------
    # 2) Trend muss intakt sein (kein Downtrend!)
    # -----------------------------------------------------------------------
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()

    if (
        last_close < ma20.iloc[-1]
        or ma20.iloc[-1] < ma50.iloc[-1]
        or ma20.iloc[-1] < ma20.iloc[-5]
        or ma50.iloc[-1] < ma50.iloc[-5]
    ):
        return result  # kein Trend, kein VCP

    # -----------------------------------------------------------------------
    # 3) Base in Segmente teilen
    # -----------------------------------------------------------------------
    n = len(close)
    seg_len = n // n_segments
    if seg_len < 5:
        return result

    seg_lows, seg_highs, seg_spread, seg_volume = [], [], [], []

    for s in range(n_segments):
        start = s * seg_len
        end = n if s == n_segments - 1 else (s + 1) * seg_len
        seg = data.iloc[start:end]

        low_s = float(seg["Low"].min())
        high_s = float(seg["High"].max())
        spread = (high_s - low_s) / resistance
        vol_s = float(seg["Volume"].mean())

        seg_lows.append(low_s)
        seg_highs.append(high_s)
        seg_spread.append(spread)
        seg_volume.append(vol_s)

    seg_lows = np.array(seg_lows)
    seg_spread = np.array(seg_spread)
    seg_volume = np.array(seg_volume)

    # -----------------------------------------------------------------------
    # 4) Pullbacks müssen flacher werden (höhere Tiefs)
    # -----------------------------------------------------------------------
    pullbacks = (resistance - seg_lows) / resistance

    # Kein Pullback darf zu tief sein
    if np.any(pullbacks > max_pullback):
        return result

    # Regression: Pullbacks sollten fallenden Trend haben
    x = np.arange(n_segments)
    if np.std(pullbacks) > 0:
        slope_pb = np.polyfit(x, pullbacks, 1)[0]
        if slope_pb >= 0:
            return result

    # -----------------------------------------------------------------------
    # 5) Volatilitätskontraktion
    # -----------------------------------------------------------------------
    if seg_spread[0] <= 0 or seg_spread[-1] / seg_spread[0] > min_contraction:
        return result

    if np.std(seg_spread) > 0:
        slope_spread = np.polyfit(x, seg_spread, 1)[0]
        if slope_spread >= 0:
            return result

    # -----------------------------------------------------------------------
    # 6) Volumenkontraktion
    # -----------------------------------------------------------------------
    if np.std(seg_volume) > 0:
        slope_vol = np.polyfit(x, seg_volume, 1)[0]
        if slope_vol >= 0:
            return result

    # -----------------------------------------------------------------------
    # 7) Breakout-Level & Entry-Signal (Variante B Logik)
    # -----------------------------------------------------------------------
    breakout_level = resistance
    entry_signal = last_close > breakout_level * 1.01  # letzte 1 % über Pivot

    # Variante B: wenn Kurs < Pivot + 3 %, gilt es weiter als VCP
    still_vcp_zone = last_close < breakout_level * 1.03

    vcp_flag = True  # Alle Bedingungen bestanden

    result = {
        "VCP": vcp_flag and still_vcp_zone,   # True trotz Früh-Breakout
        "Waves": n_segments,
        "Entry_Signal": bool(entry_signal),
        "Breakout_Level": breakout_level,
    }

    return result


