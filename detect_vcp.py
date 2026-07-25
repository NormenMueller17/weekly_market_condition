from __future__ import annotations
import pandas as pd
import numpy as np
import math


# ─────────────────────────────────────────────────────────────────────────────
# Adaptive VCP-Detektion (Minervini)
#
# Historie: Die frühere Version analysierte die vollen `window` (=60) Wochen als
# EINE Basis und teilte sie in Segmente, die über den gesamten Zeitraum monoton
# kontrahieren mussten. Trendführer machen über 60 Wochen aber steigende Hochs und
# Pullbacks weit über 15 % — sie fielen daher IMMER durch. Empirisch: 0 Basen über
# 12.187 (Titel×Woche)-Slices und 0 Signale über die gesamte Signal-Historie.
#
# Neu: `window` ist nur noch der SUCHBEREICH. Es werden mehrere Basislängen
# (base_min..base_max Wochen) getestet; die engste valide Basis DIREKT unter dem
# Pivot gewinnt. Pullbacks werden gegen den Pivot (Basis-Hoch) gemessen, und es
# wird eine echte progressive Kontraktion verlangt (jede Welle flacher als die
# vorige). Kalibriert auf strikte VCP-Geometrie (validiert an CVX/AMZN-Basen).
# ─────────────────────────────────────────────────────────────────────────────


def _check_waves(
    base_data: pd.DataFrame,
    pivot: float,
    n: int,
    max_pullback: float,
    min_contraction: float,
    max_final_range: float,
    min_bars_per_wave: int = 2,
) -> float | None:
    """Prüft ob `base_data` mit `n` Wellen eine gültige, progressiv kontrahierende
    VCP-Struktur unter `pivot` zeigt. Rückgabe: Range der letzten Welle (Anteil des
    Pivots) bei Erfolg, sonst None.
    """
    nb = len(base_data)
    seg_len = nb // n
    if seg_len < max(2, min_bars_per_wave):
        return None

    lows, highs, spread, vols = [], [], [], []
    for s in range(n):
        start = s * seg_len
        end = nb if s == n - 1 else (s + 1) * seg_len
        seg = base_data.iloc[start:end]
        lo, hi = float(seg["Low"].min()), float(seg["High"].max())
        lows.append(lo)
        highs.append(hi)
        spread.append((hi - lo) / pivot)
        vols.append(float(seg["Volume"].mean()))

    lows = np.array(lows)
    highs = np.array(highs)
    spread = np.array(spread)
    vols = np.array(vols)
    x = np.arange(n)

    # Basistiefe: tiefster Punkt nicht mehr als max_pullback unter dem Pivot
    if (pivot - lows.min()) / pivot > max_pullback:
        return None
    # Höhere Tiefs (Tiefs dürfen im Trend nicht fallen)
    if np.std(lows) > 0 and np.polyfit(x, lows, 1)[0] < 0:
        return None
    # Highs nicht steigend (Pivot deckelt als Widerstand)
    if np.std(highs) > 0 and np.polyfit(x, highs, 1)[0] > pivot * 0.002:
        return None
    # Kontraktion: letzte Welle enger als die erste
    if spread[0] <= 0 or spread[-1] / spread[0] > min_contraction:
        return None
    # Letzte Welle absolut eng
    if spread[-1] > max_final_range:
        return None
    # Progressive Kontraktion: jede Welle flacher als die vorige (mit kleiner Toleranz)
    if not all(spread[i] <= spread[i - 1] * 1.02 for i in range(1, n)):
        return None
    # Volumen-Trockenfall
    if vols[0] > 0 and vols[-1] / vols[0] >= 0.90:
        return None

    return float(spread[-1])


def detect_vcp(
    df: pd.DataFrame,
    window: int = 45,
    base_min: int = 7,
    base_max: int = 30,
    max_close_to_resistance: float = 0.05,
    min_contraction: float = 0.70,
    max_pullback: float = 0.20,
    max_final_range: float = 0.08,
    waves_to_try: tuple[int, ...] = (4, 3),
) -> dict:
    """
    Adaptive VCP-Detektion nach Minervini.

    Schritte:
      1. Stage-2-Prior-Trend: Kurs > MA20 > MA50 und MA50 steigt seit 10 Bars
      2. Adaptive Basissuche über base_min..base_max Wochen; Pivot = Basis-Hoch
      3. Pro Basislänge: Wellen (n ∈ waves_to_try) müssen progressiv kontrahieren
         (höhere Tiefs, fallende/flache Highs, jede Welle flacher als die vorige,
         Volumen trocknet aus), Basistiefe ≤ max_pullback unter Pivot
      4. Die engste valide Basis direkt unter dem Pivot gewinnt
      5. Entry_Signal: Kurs > Pivot·1.005 UND Volumen-Surge (letzte 4 Bars ≥ 1.40× Base-Avg)

    Args:
      window: SUCHBEREICH (max. berücksichtigte Basislänge; base_max cappt zusätzlich)

    Returns
    -------
    dict: VCP (bool), Waves (int), Entry_Signal (bool),
          Breakout_Level (float|None), Breakout_Volume (bool), Base_Weeks (int)
    """
    result = {
        "VCP": False,
        "Waves": 0,
        "Entry_Signal": False,
        "Breakout_Level": None,
        "Breakout_Volume": False,
        "Base_Weeks": 0,
    }

    if df is None or df.empty:
        return result
    if not {"Close", "High", "Low", "Volume"}.issubset(df.columns):
        return result

    df = df.dropna().copy()
    if len(df) < 55:
        return result

    full_close = df["Close"].astype(float)
    last_close = float(full_close.iloc[-1])
    if not math.isfinite(last_close):
        return result

    # 1. Stage-2-Prior-Trend (Uptrend-Kontext)
    ma20 = full_close.rolling(20).mean()
    ma50 = full_close.rolling(50).mean()
    if last_close < ma20.iloc[-1] or ma20.iloc[-1] < ma50.iloc[-1]:
        return result
    if len(ma50.dropna()) >= 10 and float(ma50.iloc[-1]) < float(ma50.iloc[-10]):
        return result

    # 2.–4. Adaptive Basissuche: engste valide Basis direkt unter dem Pivot
    best = None  # (final_range, L, n, pivot)
    for L in range(base_min, min(base_max, window) + 1):
        seg = df.tail(L + 1)          # +1 für den aktuellen (Breakout-)Bar
        base_data = seg.iloc[:-1]     # Basis ohne aktuellen Bar
        if len(base_data) < base_min:
            continue
        pivot = float(base_data["High"].max())
        if pivot <= 0:
            continue
        # Pivot muss Widerstand SEIN, den der Kurs noch nicht weit überschritten hat
        rel = (pivot - last_close) / pivot
        if rel > max_close_to_resistance or rel < -0.06:
            continue
        for n in waves_to_try:
            final_range = _check_waves(
                base_data, pivot, n, max_pullback, min_contraction, max_final_range
            )
            if final_range is not None:
                cand = (final_range, L, n, pivot)
                if best is None or cand[0] < best[0]:
                    best = cand
                break

    if best is None:
        return result

    final_range, L, n, pivot = best

    # 5. Ausbruchs-Volumen und Entry
    vol = df["Volume"].astype(float)
    vol_base_avg = float(vol.tail(L + 1).mean())
    vol_last_max = float(vol.iloc[-4:].max())
    breakout_vol_surge = vol_base_avg > 0 and vol_last_max >= vol_base_avg * 1.40
    price_breakout = last_close > pivot * 1.005

    return {
        "VCP": True,
        "Waves": n,
        "Entry_Signal": bool(price_breakout and breakout_vol_surge),
        "Breakout_Level": pivot,
        "Breakout_Volume": bool(breakout_vol_surge),
        "Base_Weeks": L,
    }
