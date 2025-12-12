import pandas as pd

def detect_vcp(df: pd.DataFrame, window: int = 20,
               min_waves: int = 2, max_waves: int = 4,
               volume_factor: float = 0.8,
               breakout_buffer: float = 0.01) -> dict:
    """
    Erkennung eines Volatility Contraction Patterns (VCP) nach Minervini-Logik.

    Args:
        df: DataFrame mit ['High','Low','Close','Volume']
        window: Anzahl Perioden für Analyse (z.B. 20 Wochen)
        min_waves: minimale Kontraktionswellen
        max_waves: maximale Kontraktionswellen
        volume_factor: erwartete Volumenreduktion (z.B. 0.8 = 20% Rückgang)
        breakout_buffer: Puffer oberhalb Breakout-Level (z.B. 0.01 = 1%)

    Returns:
        dict mit Infos:
        {
          "VCP": True/False,
          "Waves": int,
          "Breakout_Level": float,
          "Entry_Signal": True/False
        }
    """

    if df is None or df.empty or len(df) < window:
        return {"VCP": False, "Waves": 0, "Breakout_Level": None, "Entry_Signal": False}

    recent = df[-window:].copy()
    recent['Range'] = recent['High'] - recent['Low']

    # 1. Kontraktionswellen über lokale Range-Peaks
    ranges = recent['Range'].values
    waves = []
    for i in range(1, len(ranges) - 1):
        if ranges[i] > ranges[i-1] and ranges[i] > ranges[i+1]:
            waves.append(ranges[i])

    contracting = all(waves[i] > waves[i+1] for i in range(len(waves)-1)) if len(waves) >= 2 else False

    # 2. Volumen-Kontraktion prüfen (letztes ØVol < erstes ØVol * Faktor)
    vol = recent['Volume'].rolling(3).mean().dropna().values
    volume_contracting = len(vol) > 1 and vol[-1] < vol[0] * volume_factor

    # 3. Breakout-Level = höchster Schlusskurs im Fenster
    breakout_level = recent['Close'].max()

    # 4. Entry-Signal prüfen: aktueller Schlusskurs > Breakout_Level * (1+buffer)
    last_close = recent['Close'].iloc[-1]
    entry_signal = last_close > breakout_level * (1 + breakout_buffer)

    # 5. Ergebnis zurückgeben
    vcp_detected = contracting and volume_contracting and min_waves <= len(waves) <= max_waves

    return {
        "VCP": vcp_detected,
        "Waves": len(waves),
        "Breakout_Level": breakout_level,
        "Entry_Signal": entry_signal
    }

