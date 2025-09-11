import pandas as pd
import yfinance as yf
from data_sources import get_universe

def compute_minervini_template(df: pd.DataFrame) -> dict:
    """ Berechnet 7 Kriterien nach Minervini für ein Kurs-DataFrame. """
    dfw = df.resample("W-FRI").agg({
        "Close": "last",
        "High": "max",
        "Low": "min",
        "Volume": "sum"
    }).dropna()

    close = dfw["Close"]; high = dfw["High"]; low = dfw["Low"]; volume = dfw["Volume"]

    sma10 = close.rolling(10).mean()
    sma30 = close.rolling(30).mean()
    sma40 = close.rolling(40).mean()

    sma10_rising = sma10.iloc[-1] > sma10.iloc[-2] if len(sma10) > 1 else False
    sma30_rising = sma30.iloc[-1] > sma30.iloc[-2] if len(sma30) > 1 else False
    sma40_rising = sma40.iloc[-1] > sma40.iloc[-2] if len(sma40) > 1 else False
    ma_order = sma10.iloc[-1] > sma30.iloc[-1] and sma30.iloc[-1] > sma40.iloc[-1] if len(sma40.dropna()) else False

    high_52w = high.rolling(52).max().iloc[-1]
    low_52w  = low.rolling(52).min().iloc[-1]
    tt_range_ok = (close.iloc[-1] >= 1.30 * low_52w) and (close.iloc[-1] >= 0.75 * high_52w)

    sma13 = close.rolling(13).mean()
    rs_trend = close.iloc[-1] > sma13.iloc[-1] if len(sma13.dropna()) else False

    vol_breakout = volume.iloc[-1] > volume.rolling(20).mean().iloc[-1] * 1.5 if len(volume.dropna()) else False

    criteria = {
        "SMA10W steigend": sma10_rising,
        "SMA30W steigend": sma30_rising,
        "SMA40W steigend": sma40_rising,
        "MA-Ordnung 10>30>40": ma_order,
        "52W Range OK": tt_range_ok,
        "RS-Trend ↑": rs_trend,
        "Vol-Breakout": vol_breakout,
    }
    score = sum(int(v) for v in criteria.values())

    return {"score": score, **criteria}


def screen_universe_minervini(min_score: int = 5) -> pd.DataFrame:
    tickers = get_universe()
    results = {}
    for t in tickers:
        try:
            df = yf.download(t, period="2y", interval="1d", progress=False)
            if df.empty:
                print(f"{t}: keine Daten")
                continue

            # Manche Ticker haben nur 'Adj Close'
            if not set(["Close", "High", "Low", "Volume"]).issubset(df.columns):
                if "Adj Close" in df.columns:
                    df["Close"] = df["Adj Close"]
                    df["High"] = df["Close"]
                    df["Low"] = df["Close"]
                    df["Volume"] = 0
                else:
                    print(f"{t}: unvollständige Spalten {df.columns}")
                    continue

            res = compute_minervini_template(df)
            results[t] = res
        except Exception as e:
            print(f"Fehler bei {t}: {e}")
            continue

    if not results:
        return pd.DataFrame()  # leer zurückgeben, statt Fehler

    df_results = pd.DataFrame(results).T
    if "score" not in df_results.columns:
        return pd.DataFrame()

    leaders = df_results[df_results["score"] >= min_score].sort_values("score", ascending=False)
    return leaders

    df_results = pd.DataFrame(results).T
    leaders = df_results[df_results["score"] >= min_score].sort_values("score", ascending=False)
    return leaders
