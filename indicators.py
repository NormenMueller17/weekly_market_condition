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

# ── Zertifikate-Indikatoren (additiv, keine Änderung am bestehenden Code) ──────

def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average Directional Index (Wilder-Smoothing)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    dm_plus  = high.diff().clip(lower=0)
    dm_minus = (-low.diff()).clip(lower=0)
    # Nur die jeweils dominierende Seite zählt
    mask = dm_plus >= dm_minus
    dm_plus  = dm_plus.where(mask, 0.0)
    dm_minus = dm_minus.where(~mask, 0.0)
    alpha = 1.0 / length
    atr_s  = tr.ewm(alpha=alpha, adjust=False).mean()
    dip_s  = dm_plus.ewm(alpha=alpha, adjust=False).mean()
    dim_s  = dm_minus.ewm(alpha=alpha, adjust=False).mean()
    di_plus  = 100 * dip_s / atr_s.replace(0, np.nan)
    di_minus = 100 * dim_s / atr_s.replace(0, np.nan)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Williams %R — Werte zwischen -100 (überverkauft) und 0 (überkauft)."""
    hh = high.rolling(length).max()
    ll = low.rolling(length).min()
    return -100 * (hh - close) / (hh - ll).replace(0, np.nan)


def hv(close: pd.Series, length: int = 30) -> pd.Series:
    """Historische Volatilität (annualisiert in %) über `length` Perioden."""
    log_ret = np.log(close / close.shift(1))
    # Wochendaten: 52 Wochen/Jahr; Tagesdaten: 252 Tage/Jahr — Caller gibt passende Series
    periods_per_year = 52 if length <= 60 else 252
    return log_ret.rolling(length).std() * np.sqrt(periods_per_year) * 100


def beta(stock_close: pd.Series, market_close: pd.Series, length: int = 52) -> float:
    """Beta einer Aktie vs. Markt über die letzten `length` Perioden."""
    s_ret = stock_close.pct_change().dropna()
    m_ret = market_close.pct_change().dropna()
    common = s_ret.index.intersection(m_ret.index)
    if len(common) < max(10, length // 3):
        return np.nan
    sr = s_ret.loc[common].tail(length).values
    mr = m_ret.loc[common].tail(length).values
    cov_matrix = np.cov(sr, mr)
    var_market = cov_matrix[1, 1]
    return float(cov_matrix[0, 1] / var_market) if var_market != 0 else np.nan


def momentum(close: pd.Series, length: int = 12) -> pd.Series:
    """Momentum-Oszillator: Rate of Change in % über `length` Perioden."""
    return (close / close.shift(length) - 1) * 100


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
