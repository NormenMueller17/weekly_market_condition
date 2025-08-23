import pandas as pd
from typing import Dict

from indicators import rsi, macd

# Breadth metrics on a per-universe weekly dict[ticker]->DF with Close, High, Low, Volume

def compute_breadth(weekly_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for t, df in weekly_data.items():
        if df.empty or 'Close' not in df:
            continue
        s = df['Close']
        rows.append(pd.DataFrame({
            'ticker': t,
            'close': s,
            'ma50': s.rolling(50).mean(),
            'ma200': s.rolling(200).mean(),
            'wk_change': s.pct_change(),
            'hh_52w': s.rolling(52).max(),
            'll_52w': s.rolling(52).min(),
        }))
    if not rows:
        return pd.DataFrame()
    panel = pd.concat(rows)
    latest = panel.groupby('ticker').tail(1)
    prev = panel.groupby('ticker').nth(-2)

    breadth = pd.DataFrame({
        '%>50w': (latest['close'] > latest['ma50']).mean() * 100,
        '%>200w': (latest['close'] > latest['ma200']).mean() * 100,
        'advancers_wow_%': (latest['close'] > prev['close']).mean() * 100,
        'new_highs_52w': (latest['close'] >= latest['hh_52w']).sum(),
        'new_lows_52w': (latest['close'] <= latest['ll_52w']).sum(),
        'universe_size': len(latest),
    }, index=[0])
    return breadth
