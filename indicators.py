import numpy as np
import pandas as pd

# --- EMA helper ---
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

# --- RSI (14) ---
def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < length + 1:
        return pd.Series([np.nan] * len(series), index=series.index)
    delta = s.diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    roll_up = pd.Series(gain, index=s.index).rolling(length).mean()
    roll_down = pd.Series(loss, index=s.index).rolling(length).mean()
    rs = roll_up / roll_down
    r = 100 - (100 / (1 + rs))
    # auf Originalindex ausrichten
    out = pd.Series(np.nan, index=series.index)
    out.loc[r.index] = r
    return out

# --- MACD (12,26,9) ---
def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

# --- Moving average distance ---
def pct_above_ma(series: pd.Series, length: int) -> pd.Series:
    ma = series.rolling(length).mean()
    return (series - ma) / ma * 100

def compute_index_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Berechnet RSI, MACD, Signal-Linie, 10W-MA und deren Differenzen zum Vorwert.
    Erwartet df mit Format: MultiIndex (date, ticker) und 'close' Spalte.
    """
    results = []

    for ticker in df.columns.get_level_values(1).unique():
        close = df['close', ticker].dropna()

        if len(close) < 30:
            continue

        # RSI & MACD
        rsi_series = rsi(close)
        macd_line, signal_line = macd(close)

        # Aktuelle und Vorwochenwerte
        close_now = close.iloc[-1]
        close_prev = close.iloc[-2] if len(close) > 1 else None
        rsi_now = rsi_series.iloc[-1]
        rsi_prev = rsi_series.iloc[-2] if len(rsi_series) > 1 else None
        macd_now = macd_line.iloc[-1]
        macd_prev = macd_line.iloc[-2]
        signal_now = signal_line.iloc[-1]
        ma10 = close.ewm(span=10, adjust=False).mean().iloc[-1]

        results.append({
            "ticker": ticker,
            "close": close_now,
            "WoW": (close_now - close_prev) / close_prev * 100 if close_prev else None,
            "RSI": rsi_now,
            "Δ RSI": rsi_now - rsi_prev if rsi_prev else None,
            "MACD": macd_now,
            "Signal": signal_now,
            "Δ MACD": macd_now - macd_prev,
            "vs 10W MA": (close_now - ma10) / ma10 * 100,
        })

    return pd.DataFrame(results)
