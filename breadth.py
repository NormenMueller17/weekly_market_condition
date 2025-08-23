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
        rows.append(pd.DataFrame({
            "ticker": t,
            "close": s,
            "ma50": s.rolling(50).mean(),
            "ma200": s.rolling(200).mean(),
            "hh_52w": s.rolling(52).max(),
            "ll_52w": s.rolling(52).min(),
        }))

    if not rows:
        # leeres Ergebnis, aber sauber zurückgeben
        return pd.DataFrame({
            "%>50w": [0.0], "%>200w": [0.0], "advancers_wow_%": [0.0],
            "new_highs_52w": [0], "new_lows_52w": [0], "universe_size": [0]
        })

    panel = pd.concat(rows, ignore_index=False)           # Index = Datum
    panel = panel.reset_index().rename(columns={"index": "date"})
    # pro Ticker: last & prev-last (falls vorhanden) + letzte MAs/52W
    agg = (panel.sort_values(["ticker", "date"])
                .groupby("ticker")
                .agg(
                    last_close=("close", "last"),
                    prev_close=("close", lambda s: s.iloc[-2] if len(s) > 1 else pd.NA),
                    last_ma50=("ma50", "last"),
                    last_ma200=("ma200", "last"),
                    last_hh52=("hh_52w", "last"),
                    last_ll52=("ll_52w", "last"),
                ))

    # Kennzahlen robust berechnen
    pct_gt_50 = (agg["last_close"] > agg["last_ma50"]).mean() * 100
    pct_gt_200 = (agg["last_close"] > agg["last_ma200"]).mean() * 100
    advancers = (agg["last_close"] > agg["prev_close"]).mean() * 100  # NaN ignoriert
    new_highs = (agg["last_close"] >= agg["last_hh52"]).sum()
    new_lows  = (agg["last_close"] <= agg["last_ll52"]).sum()
    uni_size  = len(agg)

    return pd.DataFrame({
        "%>50w": [float(pct_gt_50)],
        "%>200w": [float(pct_gt_200)],
        "advancers_wow_%": [float(advancers)],
        "new_highs_52w": [int(new_highs)],
        "new_lows_52w": [int(new_lows)],
        "universe_size": [int(uni_size)],
    })
