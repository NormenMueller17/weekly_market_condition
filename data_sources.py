import os
import time
import json
import pandas as pd
import yfinance as yf
from pathlib import Path
from typing import List

from .config import SETTINGS

_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

def _ensure_cache_dir():
    Path(SETTINGS.cache_dir).mkdir(parents=True, exist_ok=True)

# --- Universe helpers ---

def get_sp500_tickers() -> List[str]:
    """Fetch S&P 500 tickers from Wikipedia and cache locally."""
    _ensure_cache_dir()
    cache_file = Path(SETTINGS.cache_dir) / "sp500_tickers.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < 60*60*24*14:  # 14 Tage Cache
        return json.loads(cache_file.read_text())
    tables = pd.read_html(_SP500_WIKI_URL)
    tickers = tables[0]['Symbol'].tolist()
    tickers = [t.replace(".", "-") for t in tickers]  # BRK.B -> BRK-B
    cache_file.write_text(json.dumps(tickers))
    return tickers


def get_universe() -> List[str]:
    if SETTINGS.universe == "sp500":
        return get_sp500_tickers()
    if SETTINGS.universe == "custom" and SETTINGS.custom_tickers:
        return [t.strip() for t in SETTINGS.custom_tickers.split(",") if t.strip()]
    # default fallback
    return get_sp500_tickers()

# --- Price loaders ---

def load_weekly_history(tickers: List[str], weeks: int = 60) -> dict:
    """Download weekly OHLCV for a list of tickers. Returns dict[ticker] -> DataFrame."""
    data = {}
    period = max(104, weeks + 10)  # at least 2 Jahre für MAs
    for batch_start in range(0, len(tickers), 50):
        batch = tickers[batch_start:batch_start+50]
        df = yf.download(batch, period=f"{period}w", interval="1wk", auto_adjust=False, group_by='ticker', threads=True, progress=False)
        # Normalize multi-index formats
        if len(batch) == 1:
            t = batch[0]
            data[t] = df.copy()
        else:
            for t in batch:
                if (t,) in df.columns:  # yfinance variant A
                    sub = df[t].copy()
                else:
                    # yfinance variant B: top-level columns "Open/High/..." and tickers sub-level
                    try:
                        sub = df.xs(t, axis=1, level=0)
                    except Exception:
                        continue
                data[t] = sub.dropna(how='all')
        time.sleep(0.5)  # be nice
    return data


def load_index_series():
    """Load weekly series for key indices and proxies."""
    symbols = {
        'SPY': 'SPY',   # S&P 500 ETF
        'QQQ': 'QQQ',   # Nasdaq 100 ETF
        'IWM': 'IWM',   # Russell 2000 ETF
        'VIX': '^VIX',  # Volatility
        'TNX': '^TNX',  # 10Y Yield * 10
        'UUP': 'UUP',   # USD Proxy
        'CPC': '^CPC',  # CBOE Total Put/Call Ratio
    }
    out = {}
    for name, sym in symbols.items():
        out[name] = yf.download(sym, period="260w", interval="1wk", auto_adjust=False, progress=False)
    return out
