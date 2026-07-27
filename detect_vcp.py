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
    max_vol_dryup: float = 0.90,
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
    # Volumen-Trockenfall (max_vol_dryup >= 1.0 schaltet ihn ab)
    if max_vol_dryup < 1.0 and vols[0] > 0 and vols[-1] / vols[0] >= max_vol_dryup:
        return None

    return float(spread[-1])


def _naive_index(idx: pd.Index) -> pd.DatetimeIndex:
    """Zeitzonenfreier DatetimeIndex — Wochen- und Tagesserien kommen je nach
    yfinance-Aufruf mal mit, mal ohne tz zurück und wären sonst nicht vergleichbar.
    """
    out = pd.DatetimeIndex(pd.to_datetime(idx))
    if out.tz is not None:
        out = out.tz_localize(None)
    return out


def _daily_breakout_vol_ratio(
    daily: pd.DataFrame,
    week_label: pd.Timestamp,
    pivot: float,
    lookback: int,
) -> float | None:
    """Volumen des AUSBRUCHSTAGS gegen den Ø der `lookback` Handelstage davor.

    Minervini misst den Volumenschub am Tag des Ausbruchs gegen den 50-Tage-
    Durchschnitt. Auf Wochenbasis verwässert eine starke Ausbruchskerze mit vier
    ruhigen Tagen zu einem unauffälligen Wochenvolumen — gemessen lag der Median
    des Wochen-Verhältnisses bei 0,90×, also unter dem Basisdurchschnitt.

    Ausbruchstag = erster Tag der Ausbruchswoche mit Schluss > pivot·1.005.

    `week_label` ist der Index der letzten Wochenkerze. Ob der auf den Wochen-
    anfang zeigt (yfinance `interval="1wk"`) oder auf den Wochenschluss
    (`resample("W-FRI")`), ist egal: verglichen wird der Wochen-PERIODE, nicht das
    Datum. Tage nach der Ausbruchswoche bleiben außen vor, walk-forward ist also
    look-ahead-frei.

    Rückgabe: Verhältnis, oder None wenn die Tagesdaten es nicht hergeben
    (Aufrufer fällt dann auf die Wochenlogik zurück).
    """
    if daily is None or daily.empty:
        return None
    if not {"Close", "Volume"}.issubset(daily.columns):
        return None

    d = daily.copy()
    d.index = _naive_index(d.index)
    d = d[["Close", "Volume"]].apply(pd.to_numeric, errors="coerce").dropna()
    if d.empty:
        return None

    # Wochenperiode statt Datum: macht die Zuordnung labelunabhängig
    day_week = d.index.to_period("W").start_time
    label_week = pd.Period(pd.Timestamp(week_label).tz_localize(None)
                           if pd.Timestamp(week_label).tz is not None
                           else pd.Timestamp(week_label), freq="W").start_time

    hist = d[day_week <= label_week]
    week_days = d[day_week == label_week]
    if week_days.empty:
        return None

    over = (week_days["Close"].to_numpy() > pivot * 1.005)
    if not over.any():
        return None
    pos_in_week = int(np.argmax(over))          # erster Tag über dem Pivot
    p = len(hist) - len(week_days) + pos_in_week

    base = hist["Volume"].iloc[max(0, p - lookback):p]
    if len(base) < 20:                          # zu wenig Vorlauf für einen Ø
        return None
    base_avg = float(base.mean())
    if base_avg <= 0:
        return None
    return float(hist["Volume"].iloc[p]) / base_avg


def detect_vcp(
    df: pd.DataFrame,
    window: int = 45,
    base_min: int = 7,
    base_max: int = 30,
    max_close_to_resistance: float = 0.05,
    min_contraction: float = 0.70,
    max_pullback: float = 0.20,
    max_final_range: float = 0.08,
    max_vol_dryup: float = 0.90,
    min_breakout_vol_ratio: float = 1.40,
    daily_df: pd.DataFrame | None = None,
    daily_vol_lookback: int = 50,
    rs_score: float | None = None,
    min_rs_score: float = 0.0,
    waves_to_try: tuple[int, ...] = (4, 3),
) -> dict:
    """
    Adaptive VCP-Detektion nach Minervini.

    Schritte:
      0. RS-Vorfilter (optional): nur Trendführer werden überhaupt auf eine Basis
         geprüft. Ohne ihn selektiert die enge Geometrie (max_final_range plus
         Volumen-Trockenfall) bevorzugt Low-Vol-Defensivwerte — gemessen lag deren
         Volumen am Ausbruchstag im Median bei 0,99× des 50-Tage-Ø, also ohne
         jede Expansion.
      1. Stage-2-Prior-Trend: Kurs > MA20 > MA50 und MA50 steigt seit 10 Bars
      2. Adaptive Basissuche über base_min..base_max Wochen; Pivot = Basis-Hoch
      3. Pro Basislänge: Wellen (n ∈ waves_to_try) müssen progressiv kontrahieren
         (höhere Tiefs, fallende/flache Highs, jede Welle flacher als die vorige,
         Volumen trocknet aus), Basistiefe ≤ max_pullback unter Pivot
      4. Die engste valide Basis direkt unter dem Pivot gewinnt
      5. Entry_Signal: Kurs > Pivot·1.005 UND Volumen-Surge
         ≥ min_breakout_vol_ratio. Bevorzugt auf TAGESBASIS gemessen
         (Ausbruchstag vs. Ø der letzten `daily_vol_lookback` Handelstage);
         ohne `daily_df` ersatzweise Ausbruchswoche vs. Basis-Ø.

    Args:
      window: SUCHBEREICH (max. berücksichtigte Basislänge; base_max cappt zusätzlich)
      max_vol_dryup: geforderter Volumen-Trockenfall über die Basis (letzte Welle
        / erste Welle muss darunter liegen). 1.0 schaltet das Kriterium ab.
      min_breakout_vol_ratio: Volumenschwelle des Ausbruchs gegen den Vergleichs-Ø
      daily_df: optionale Tages-OHLCV-Serie desselben Titels, aus der `df`
        aggregiert wurde. Nur die Ausbruchswoche wird daraus gelesen; Zeilen nach
        dem letzten Wochenbar werden ignoriert, walk-forward bleibt also sauber.
      rs_score: O'Neil-RS-Perzentil (1–99) des Titels ZUM ZEITPUNKT des letzten
        Bars. Querschnittsgröße, die der Aufrufer über das Universum berechnen
        muss — `detect_vcp` sieht nur einen Titel.
      min_rs_score: Schwelle für `rs_score`. 0 (Default) schaltet den Vorfilter
        ab; ohne übergebenen `rs_score` greift er ohnehin nicht.

    Returns
    -------
    dict: VCP (bool), Waves (int), Entry_Signal (bool),
          Breakout_Level (float|None), Breakout_Volume (bool),
          Breakout_Vol_Ratio (float), Breakout_Vol_Basis ("daily"|"weekly"),
          Base_Weeks (int)
    """
    result = {
        "VCP": False,
        "Waves": 0,
        "Entry_Signal": False,
        "Breakout_Level": None,
        "Breakout_Volume": False,
        "Breakout_Vol_Ratio": 0.0,
        "Breakout_Vol_Basis": "weekly",
        "Base_Weeks": 0,
    }

    if df is None or df.empty:
        return result
    if not {"Close", "High", "Low", "Volume"}.issubset(df.columns):
        return result

    # 0. RS-Vorfilter — vor der Basissuche, spart auch Rechenzeit
    if min_rs_score > 0:
        if rs_score is None or not math.isfinite(float(rs_score)):
            return result
        if float(rs_score) < min_rs_score:
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
                base_data, pivot, n, max_pullback, min_contraction,
                max_final_range, max_vol_dryup
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
    price_breakout = last_close > pivot * 1.005

    # Bevorzugt Tagesbasis: Volumen des Ausbruchstags gegen den Ø der letzten
    # `daily_vol_lookback` Handelstage davor.
    vol_ratio, vol_basis = None, "weekly"
    if daily_df is not None:
        vol_ratio = _daily_breakout_vol_ratio(
            daily_df, _naive_index(df.index)[-1], pivot, daily_vol_lookback
        )
        if vol_ratio is not None:
            vol_basis = "daily"

    # Fallback ohne Tagesdaten: Volumen der AUSBRUCHSWOCHE (letzter Bar) gegen den
    # Durchschnitt der Basis OHNE diese Woche — dieselbe Abgrenzung wie bei der
    # Basisgeometrie oben (`base_data = seg.iloc[:-1]`).
    if vol_ratio is None:
        vol = df["Volume"].astype(float)
        vol_base_avg = float(vol.iloc[-(L + 1):-1].mean())
        vol_ratio = float(vol.iloc[-1]) / vol_base_avg if vol_base_avg > 0 else 0.0

    breakout_vol_surge = vol_ratio >= min_breakout_vol_ratio

    return {
        "VCP": True,
        "Waves": n,
        "Entry_Signal": bool(price_breakout and breakout_vol_surge),
        "Breakout_Level": pivot,
        "Breakout_Volume": bool(breakout_vol_surge),
        "Breakout_Vol_Ratio": float(vol_ratio),
        "Breakout_Vol_Basis": vol_basis,
        "Base_Weeks": L,
    }
