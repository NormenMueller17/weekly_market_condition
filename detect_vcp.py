from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


def detect_vcp(
    df: pd.DataFrame,
    window: int = 180,
    n_segments: int = 4,
    max_high_drift: float = 0.08,
    min_contraction: float = 0.5,
    max_last_pullback: float = 0.12,
    max_close_to_resistance: float = 0.03,
    ) -> bool:
    """
    Variante B: VCP über grobe Segment-Analyse („Kisten“)
    ----------------------------------------------------
    Idee:
      1. Nur die letzten `window` Handelstage betrachten.
      2. Einen groben Widerstand aus den höchsten Schlusskursen bestimmen.
      3. Das Zeitfenster in `n_segments` Segmente teilen.
         Für jedes Segment:
            - tiefster Kurs (min Low)
            - höchster Kurs (max High)
            - Durchschnittsvolumen
      4. Prüfen, ob:
            - die Segment-Spannen (High-Low) deutlich kleiner werden
              (mind. `1 - min_contraction` Reduktion von Segment 1 → letztes)
            - die Tiefs höher kommen (Pullbacks werden flacher)
            - die Pullbacks nicht zu tief werden
            - das Volumen in den späteren Segmenten abnimmt
            - der Schlusskurs am Ende nah am Widerstand liegt
    Die Funktion liefert nur einen boolschen Wert zurück: True = VCP erkannt.
    """

    # --- Basisprüfungen ----------------------------------------------------- #
    if df is None or df.empty:
        return False

    cols_needed = {"Close", "High", "Low", "Volume"}
    if not cols_needed.issubset(df.columns):
        # Daten sind nicht vollständig genug
        return False

    if len(df) < max(window, n_segments * 10):
        # Zu wenig Historie um Muster zu erkennen
        return False

    # Nur das Analysefenster betrachten
    df_win = df.tail(window).copy()

    close = pd.to_numeric(df_win["Close"], errors="coerce")
    high = pd.to_numeric(df_win["High"], errors="coerce")
    low = pd.to_numeric(df_win["Low"], errors="coerce")
    vol = pd.to_numeric(df_win["Volume"], errors="coerce")

    # Falls viele NaNs vorhanden sind, abbrechen
    if close.isna().mean() > 0.2 or high.isna().mean() > 0.2:
        return False

    # --- 1. Widerstand bestimmen ------------------------------------------- #
    # Näherung: oberes 90-Perzentil der Schlusskurse im Fenster
    res_level = float(close.quantile(0.9))
    if not math.isfinite(res_level) or res_level <= 0:
        return False

    # Letzter Schlusskurs sollte in der Nähe des Widerstands liegen
    last_close = float(close.iloc[-1])
    if not math.isfinite(last_close):
        return False

    dist_to_res = abs(last_close / res_level - 1.0)
    if dist_to_res > max_close_to_resistance:
        # Kurs ist zu weit weg vom "Deckel" – typischerweise kein fertiges VCP
        return False

    # --- 2. Fenster in Segmente aufteilen ---------------------------------- #
    n = len(df_win)
    seg_len = n // n_segments
    if seg_len < 5:
        # zu kurze Segmente
        return False

    seg_lows = []
    seg_highs = []
    seg_spreads = []
    seg_vol = []

    for i in range(n_segments):
        start = i * seg_len
        end = n if i == n_segments - 1 else (i + 1) * seg_len
        seg = df_win.iloc[start:end]

        seg_low = float(seg["Low"].min())
        seg_high = float(seg["High"].max())
        seg_volume = float(seg["Volume"].mean())

        if not (math.isfinite(seg_low) and math.isfinite(seg_high)):
            return False

        seg_lows.append(seg_low)
        seg_highs.append(seg_high)
        seg_spreads.append((seg_high - seg_low) / res_level)
        seg_vol.append(seg_volume)

    seg_lows = np.array(seg_lows)
    seg_spreads = np.array(seg_spreads)
    seg_vol = np.array(seg_vol)

    # --- 3. Pullbacks & Spannen prüfen ------------------------------------- #

    # 3a) Pullback-Tiefe je Segment (Abstand low zum Widerstand)
    pullbacks = (res_level - seg_lows) / res_level  # ~"Tiefe" unterhalb Widerstand

    # Pullbacks dürfen nicht zu tief sein (z.B. > 12 %)
    if np.any(pullbacks > max_last_pullback * 1.5):  # etwas Toleranz
        return False

    # Die Pullbacks sollten im Zeitverlauf tendenziell flacher werden:
    # d.h. pullbacks[0] > pullbacks[1] > ... (mit Toleranz)
    # Wir prüfen einfach, ob es eine negative lineare Regression gibt
    x = np.arange(n_segments)
    if np.std(pullbacks) > 0:
        slope_pb = np.polyfit(x, pullbacks, 1)[0]
        if slope_pb >= 0:
            # Pullbacks werden nicht kleiner
            return False

    # 3b) Spreads sollten sinken (Volatilität wird geringer)
    if seg_spreads[0] <= 0:
        return False

    contraction_ratio = seg_spreads[-1] / seg_spreads[0]
    if contraction_ratio > min_contraction:
        # z.B. min_contraction=0.5 => letztes Segment max. 50% der ursprünglichen Spanne
        return False

    if np.std(seg_spreads) > 0:
        slope_spread = np.polyfit(x, seg_spreads, 1)[0]
        if slope_spread >= 0:
            # Spreads werden nicht kleiner
            return False

    # --- 4. Volumenverhalten ------------------------------------------------ #
    # Volumen im Verlauf sollte im Mittel abnehmen
    if np.std(seg_vol) > 0 and seg_vol[0] > 0:
        slope_vol = np.polyfit(x, seg_vol, 1)[0]
        if slope_vol >= 0:
            # Volumen nimmt nicht ab
            return False

    # Letztes Segment sollte klar unter dem mittleren Volumen liegen
    overall_vol_mean = float(vol.mean())
    if seg_vol[-1] > overall_vol_mean:
        return False

    # --- Wenn alle Filter durchlaufen sind, werten wir es als VCP ---------- #
    return True
