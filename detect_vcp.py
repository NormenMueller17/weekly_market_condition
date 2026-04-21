from __future__ import annotations
import pandas as pd
import numpy as np
import math


def _check_segments(
    data: pd.DataFrame,
    n: int,
    resistance: float,
    max_pullback: float,
    min_contraction: float,
    min_bars_per_wave: int = 3,
) -> tuple[bool, np.ndarray | None]:
    """
    Prüft ob `data` mit `n` Segmenten ein gültiges VCP-Muster zeigt.
    Gibt (True, seg_highs_arr) oder (False, None) zurück.
    """
    n_bars = len(data)
    seg_len = n_bars // n
    if seg_len < max(5, min_bars_per_wave):
        return False, None

    seg_lows, seg_highs, seg_spread, seg_volume = [], [], [], []

    for s in range(n):
        start = s * seg_len
        end = n_bars if s == n - 1 else (s + 1) * seg_len
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
    seg_highs_arr = np.array(seg_highs)
    seg_spread = np.array(seg_spread)
    seg_volume = np.array(seg_volume)
    x = np.arange(n)

    # Tiefs müssen steigen (kleinere Pullbacks)
    pullbacks = (resistance - seg_lows) / resistance
    if np.any(pullbacks > max_pullback):
        return False, None
    if np.std(pullbacks) > 0 and np.polyfit(x, pullbacks, 1)[0] >= 0:
        return False, None

    # Highs müssen fallen (engere Range von oben)
    if np.std(seg_highs_arr) > 0 and np.polyfit(x, seg_highs_arr, 1)[0] >= 0:
        return False, None

    # Volatilitätskontraktion: letzte Spread ≤ 75% der ersten
    if seg_spread[0] <= 0 or seg_spread[-1] / seg_spread[0] > min_contraction:
        return False, None
    if np.std(seg_spread) > 0 and np.polyfit(x, seg_spread, 1)[0] >= 0:
        return False, None

    # Volumenkontraktion: fallender Trend
    if np.std(seg_volume) > 0 and np.polyfit(x, seg_volume, 1)[0] >= 0:
        return False, None

    # Trockenfall: letztes Segment < 80% des ersten (Volumen muss deutlich abnehmen)
    if seg_volume[0] > 0 and seg_volume[-1] / seg_volume[0] >= 0.80:
        return False, None

    return True, seg_highs_arr


def detect_vcp(
    df: pd.DataFrame,
    window: int = 60,
    max_close_to_resistance: float = 0.04,
    min_contraction: float = 0.75,
    max_pullback: float = 0.15,
    min_bars_per_wave: int = 3,
) -> dict:
    """
    VCP-Detektion nach Minervini.

    Schritte:
      1. Pivot = höchstes High der Base (ohne aktuellen Bar)
      2. Stage-2-Prior-Trend: MA20 > MA50 + MA50 steigt seit 20 Bars
      3. Flexible Wellenerkennung: n ∈ {4, 3, 2}, höchstes valides n gewinnt
      4. Pro Welle: Highs fallen, Tiefs steigen, Spread kontrahiert, Volumen trocknet aus
      5. Ausbruchs-Volumen: max. letzte 5 Bars ≥ 1.40× Base-Avg → Pflicht für Entry_Signal

    Returns
    -------
    dict: VCP (bool), Waves (int), Entry_Signal (bool),
          Breakout_Level (float|None), Breakout_Volume (bool)
    """

    result = {
        "VCP": False,
        "Waves": 0,
        "Entry_Signal": False,
        "Breakout_Level": None,
        "Breakout_Volume": False,
    }

    if df is None or df.empty:
        return result

    required = {"Close", "High", "Low", "Volume"}
    if not required.issubset(df.columns):
        return result

    df = df.dropna().copy()
    if len(df) < window:
        return result

    data = df.tail(window).copy()
    close = data["Close"].astype(float)
    high = data["High"].astype(float)
    vol = data["Volume"].astype(float)

    last_close = float(close.iloc[-1])
    if not math.isfinite(last_close):
        return result

    # Pivot = höchstes High der Base OHNE den aktuellen Bar (verhindert, dass ein
    # Breakout-Bar den Pivot nach oben verschiebt und Entry_Signal nie triggert)
    base_high = high.iloc[:-1]
    resistance = float(base_high.max())
    if not math.isfinite(resistance) or resistance <= 0:
        return result

    # Base-Daten für Muster-Analyse: ohne den letzten Bar
    base_data = data.iloc[:-1]

    # Stage-2-Prior-Trend aus vollem DataFrame (erfasst Uptrend vor der Base)
    full_close = df["Close"].astype(float)
    if len(full_close) < 50:
        return result
    ma20 = full_close.rolling(20).mean()
    ma50 = full_close.rolling(50).mean()

    # Basischeck: Kurs > MA20 > MA50
    if last_close < ma20.iloc[-1] or ma20.iloc[-1] < ma50.iloc[-1]:
        return result

    # MA50 muss seit 10 Bars steigen (Stage-2-Bestätigung — kein getarnter Downtrend)
    ma50_valid = ma50.dropna()
    if len(ma50_valid) >= 10 and float(ma50.iloc[-1]) < float(ma50.iloc[-10]):
        return result

    # Längerfristiger Momentum: Kurs muss ≥ 5% über dem Niveau von (window + 30) Bars davor
    # (filtert Erholungsversuche nach großen Kurseinbrüchen heraus)
    lookback_momentum = window + 30
    if len(full_close) >= lookback_momentum:
        prior_price = float(full_close.iloc[-lookback_momentum])
        if prior_price > 0 and last_close < prior_price * 1.05:
            return result

    # Flexible Wellenerkennung auf base_data (ohne aktuellen Bar)
    found_n = None
    found_seg_highs = None

    for n in [4, 3, 2]:
        valid, seg_highs_arr = _check_segments(
            base_data, n, resistance, max_pullback, min_contraction, min_bars_per_wave
        )
        if valid:
            found_n = n
            found_seg_highs = seg_highs_arr
            break

    if found_n is None:
        return result

    # Breakout-Level = Hoch der letzten Kontraktion
    breakout_level = float(found_seg_highs[-1])

    # Kurs muss im Breakout-Fenster sein: max. 4% unter Pivot ODER bereits drüber (max. 5%)
    rel_dist = (breakout_level - last_close) / breakout_level
    if rel_dist > max_close_to_resistance or rel_dist < -0.05:
        return result

    # Ausbruchs-Volumen: max. der letzten 5 Bars muss ≥ 1.40× Base-Durchschnitt sein
    vol_base_avg = float(vol.mean())
    vol_last_max = float(vol.iloc[-5:].max())
    breakout_vol_surge = vol_base_avg > 0 and vol_last_max >= vol_base_avg * 1.40

    # Entry nur wenn Kurs über Pivot UND Volumen-Surge vorhanden
    price_breakout = last_close > breakout_level * 1.005
    entry_signal = price_breakout and breakout_vol_surge

    return {
        "VCP": True,
        "Waves": found_n,
        "Entry_Signal": bool(entry_signal),
        "Breakout_Level": breakout_level,
        "Breakout_Volume": bool(breakout_vol_surge),
    }
