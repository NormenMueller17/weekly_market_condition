import pandas as pd
from typing import Dict

from indicators import rsi, macd

# Breadth metrics on a per-universe weekly dict[ticker]->DF with Close, High, Low, Volume

def compute_breadth(weekly_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for t, df in weekly_data.items():
        if df is None or df.empty or "Close" not in df:
            continue
        s = pd.to_numeric(df["Close"], errors="coerce")
        # Jede Zeile bekommt den Ticker als Spalte, Index = Datum
        frame = pd.DataFrame({
            "close": s,
            "ma50": s.rolling(50).mean(),
            "ma200": s.rolling(200).mean(),
            "hh_52w": s.rolling(52).max(),
            "ll_52w": s.rolling(52).min(),
        })
        frame["ticker"] = t
        rows.append(frame)

    if not rows:
        # leeres, aber gültiges Resultat
        return pd.DataFrame({
            "%>50w": [0.0], "%>200w": [0.0], "advancers_wow_%": [0.0],
            "new_highs_52w": [0], "new_lows_52w": [0], "universe_size": [0]
        })

    # Zusammenführen
    panel = pd.concat(rows, axis=0)

    # Index = Datum → vor reset_index benennen, damit die neue Spalte garantiert 'date' heißt
    panel.index.name = "date"
    panel = panel.reset_index()  # hat nun Spalten: date, close, ma50, ma200, hh_52w, ll_52w, ticker

    # Sort & Aggregation je Ticker
    panel = panel.sort_values(["ticker", "date"])
    grp = panel.groupby("ticker", as_index=True)

    def _prev(x: pd.Series):
        return x.iloc[-2] if len(x) > 1 else pd.NA

    agg = grp.agg(
        last_close=("close", "last"),
        prev_close=("close", _prev),
        last_ma50=("ma50", "last"),
        last_ma200=("ma200", "last"),
        last_hh52=("hh_52w", "last"),
        last_ll52=("ll_52w", "last"),
    )

    # Kennzahlen (NaN werden in Vergleichen ignoriert)
    pct_gt_50  = (agg["last_close"] > agg["last_ma50"]).mean() * 100
    pct_gt_200 = (agg["last_close"] > agg["last_ma200"]).mean() * 100
    advancers  = (agg["last_close"] > agg["prev_close"]).mean() * 100
    new_highs  = (agg["last_close"] >= agg["last_hh52"]).sum()
    new_lows   = (agg["last_close"] <= agg["last_ll52"]).sum()
    uni_size   = len(agg)

    return pd.DataFrame({
        "%>50w": [float(pct_gt_50)],
        "%>200w": [float(pct_gt_200)],
        "advancers_wow_%": [float(advancers)],
        "new_highs_52w": [int(new_highs)],
        "new_lows_52w": [int(new_lows)],
        "universe_size": [int(uni_size)],
    })
