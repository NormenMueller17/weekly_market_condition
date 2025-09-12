import pandas as pd
import yfinance as yf
from data_sources import get_universe


def compute_minervini_template(df: pd.DataFrame) -> dict:
    """Berechnet die 7 Minervini-Kriterien für ein Kurs-DataFrame mit OHLCV-Daten."""

    # Wochenaggregation
    dfw = df.resample("W-FRI").agg({
        "Close": "last",
        "High": "max",
        "Low": "min",
        "Volume": "sum"
    }).dropna()

    close = dfw["Close"]
    high = dfw["High"]
    low = dfw["Low"]
    volume = dfw["Volume"]

    # Gleitende Durchschnitte
    sma10 = close.rolling(10).mean()
    sma30 = close.rolling(30).mean()
    sma40 = close.rolling(40).mean()

    sma10_rising = len(sma10.dropna()) > 1 and sma10.iloc[-1] > sma10.iloc[-2]
    sma30_rising = len(sma30.dropna()) > 1 and sma30.iloc[-1] > sma30.iloc[-2]
    sma40_rising = len(sma40.dropna()) > 1 and sma40.iloc[-1] > sma40.iloc[-2]
    ma_order = len(sma40.dropna()) > 0 and sma10.iloc[-1] > sma30.iloc[-1] and sma30.iloc[-1] > sma40.iloc[-1]

    # 52W-Regel
    if len(close) >= 52:
        high_52w = high.rolling(52).max().iloc[-1]
        low_52w = low.rolling(52).min().iloc[-1]
        tt_range_ok = (close.iloc[-1] >= 1.30 * low_52w) and (close.iloc[-1] >= 0.75 * high_52w)
    else:
        tt_range_ok = False

    # RS-Trend
    sma13 = close.rolling(13).mean()
    rs_trend = len(sma13.dropna()) > 0 and close.iloc[-1] > sma13.iloc[-1]

    # Volumen-Breakout
    if len(volume.dropna()) > 20:
        vol_breakout = volume.iloc[-1] > volume.rolling(20).mean().iloc[-1] * 1.5
    else:
        vol_breakout = False

    # NEU: Wochenvergleich Close
    if len(close) >= 2:
        weekly_momentum = close.iloc[-1] > close.iloc[-2]
    else:
        weekly_momentum = False

    criteria = {
        "SMA10W steigend": sma10_rising,
        "SMA30W steigend": sma30_rising,
        "SMA40W steigend": sma40_rising,
        "MA-Ordnung 10>30>40": ma_order,
        "52W Range OK": tt_range_ok,
        "RS-Trend ↑": rs_trend,
        "Vol-Breakout": vol_breakout,
        "Close > Vorwoche": weekly_momentum,
    }
    score = sum(int(v) for v in criteria.values())

    return {"score": score, **criteria}


def screen_universe_minervini(min_score: int = 5) -> pd.DataFrame:
    """Screening des Universums nach Minervini. Liefert alle Aktien mit >= min_score."""

    tickers = get_universe()
    results = {}

    for t in tickers:
        try:
            df = yf.download(t, period="2y", interval="1d", progress=False)

            if df.empty:
                print(f"{t}: keine Daten")
                continue

            # MultiIndex abfangen (falls mehrere Ticker zurückkommen)
            if isinstance(df.columns, pd.MultiIndex):
                if t in df.columns.levels[1]:
                    df = df.xs(t, axis=1, level=1)
                else:
                    print(f"{t}: MultiIndex ohne {t}")
                    continue

            # Fehlende Spalten auffüllen
            required = ["Close", "High", "Low", "Volume"]
            if not set(required).issubset(df.columns):
                if "Adj Close" in df.columns:
                    df["Close"] = df["Adj Close"]
                if "High" not in df.columns:
                    df["High"] = df["Close"]
                if "Low" not in df.columns:
                    df["Low"] = df["Close"]
                if "Volume" not in df.columns:
                    df["Volume"] = 0

            res = compute_minervini_template(df)
            results[t] = res

        except Exception as e:
            print(f"Fehler bei {t}: {e}")
            continue

    if not results:
        return pd.DataFrame()

    df_results = pd.DataFrame(results).T
    if "score" not in df_results.columns:
        return pd.DataFrame()

    leaders = df_results[df_results["score"] >= min_score].sort_values("score", ascending=False)
    return leaders
