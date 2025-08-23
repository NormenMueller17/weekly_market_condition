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
