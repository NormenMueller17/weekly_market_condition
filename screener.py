import pandas as pd
import yfinance as yf
from data_sources import get_universe

_VOLUME_BREAKOUT_SCORE = 1.0

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
        vol20 = volume.rolling(20).mean()
        vol_breakout = volume.iloc[-1] > vol20.iloc[-1] * _VOLUME_BREAKOUT_SCORE
        vol20_val = vol20.iloc[-1]
        vol_score = volume.iloc[-1] / vol20_val if vol20_val and vol20_val != 0 else float("nan")
    else:
        vol_breakout = False
        vol20_val = float("nan")
        vol_score = float("nan")

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

    return {
        "score": score,
        **criteria,
        "vol20": vol20_val,
        "vol_score": vol_score,
        }


def screen_universe_minervini(universe=None, min_score: int = 6) -> pd.DataFrame:
    """
    Screeningt ein Universum nach Minervini.
    - universe: optionale Iterable von Ticker-Symbolen. Wenn None, wird get_universe() benutzt.
    - min_score: Mindestanzahl erfüllter Kriterien (zusätzlich zum Pflicht-Kriterium 'Vol-Breakout').
    """
    # 1) Universum festlegen
    tickers = list(universe) if universe is not None else list(get_universe())

    results = {}
    for t in tickers:
        try:
            df = yf.download(
                t,
                period="2y",
                interval="1d",
                auto_adjust=False,
                actions=False,
                repair=True,
                progress=False,
                threads=False,
            )

            if df.empty:
                print(f"{t}: keine Daten")
                continue

            # MultiIndex (selten, aber möglich)
            if isinstance(df.columns, pd.MultiIndex):
                try:
                    df = df.xs(t, axis=1, level=1)
                except Exception:
                    print(f"{t}: MultiIndex ohne {t} – übersprungen")
                    continue

            # Fehlende Spalten robuster auffüllen
            required = {"Close", "High", "Low", "Volume"}
            have = set(df.columns)
            if not required.issubset(have):
                if "Adj Close" in df.columns and "Close" not in have:
                    df["Close"] = df["Adj Close"]
                if "High" not in have:
                    df["High"] = df["Close"]
                if "Low" not in have:
                    df["Low"] = df["Close"]
                if "Volume" not in have:
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

    # Bedingung: Vol-Breakout MUSS wahr sein UND zusätzlich mind. 'min_score' weitere Kriterien
    leaders = df_results[
        (df_results["Vol-Breakout"] == True) &
        ((df_results["score"] - df_results["Vol-Breakout"].astype(int)) >= min_score)
    ].sort_values("score", ascending=False)

    return leaders
