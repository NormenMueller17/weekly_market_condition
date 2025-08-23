# data_sources.py
import os
import time
import json
import pandas as pd
import yfinance as yf
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timedelta

from config import SETTINGS

_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

def _ensure_cache_dir():
    Path(SETTINGS.cache_dir).mkdir(parents=True, exist_ok=True)

def get_sp500_tickers() -> List[str]:
    _ensure_cache_dir()
    cache_file = Path(SETTINGS.cache_dir) / "sp500_tickers.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < 60*60*24*14:
        return json.loads(cache_file.read_text())
    tables = pd.read_html(_SP500_WIKI_URL)  # lxml/html5lib nötig
    tickers = tables[0]["Symbol"].tolist()
    tickers = [t.replace(".", "-") for t in tickers]
    cache_file.write_text(json.dumps(tickers))
    return tickers

def get_universe() -> List[str]:
    if SETTINGS.universe == "sp500":
        return get_sp500_tickers()
    if SETTINGS.universe == "custom" and SETTINGS.custom_tickers:
        return [t.strip() for t in SETTINGS.custom_tickers.split(",") if t.strip()]
    return get_sp500_tickers()

def _start_date_for_weeks(weeks: int) -> datetime:
    # +10 Wochen Puffer für MAs etc.
    return datetime.utcnow() - timedelta(weeks=weeks + 10)

def load_weekly_history(tickers: List[str], weeks: int = 60) -> Dict[str, pd.DataFrame]:
    """Lädt Weekly OHLCV je Ticker mittels start=... und interval='1wk'."""
    data: Dict[str, pd.DataFrame] = {}
    start = _start_date_for_weeks(weeks)
    for i in range(0, len(tickers), 50):
        batch = tickers[i:i+50]
        try:
            df = yf.download(
                batch,
                start=start.strftime("%Y-%m-%d"),
                interval="1wk",
                auto_adjust=False,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            time.sleep(1.0)
            continue

        if len(batch) == 1:
            t = batch[0]
            sub = df.copy()
            if isinstance(sub.columns, pd.MultiIndex):
                # yfinance variiert – ggf. Ebene wählen
                try:
                    sub = df.xs(t, axis=1, level=0)
                except Exception:
                    pass
            data[t] = sub.dropna(how="all")
        else:
            for t in batch:
                try:
                    sub = df[t] if (t,) in df.columns else df.xs(t, axis=1, level=0)
                    data[t] = sub.dropna(how="all")
                except Exception:
                    # fehlgeschlagene Einzeldownloads ignorieren
                    continue
        time.sleep(0.4)  # nett zu YF
    return data

def load_index_series():
    """
    Lädt Indizes/Proxys als Tagesdaten und resampelt auf Wochen.
    So vermeiden wir period='…w' Fehler und inkonsistente Weekly-APIs.
    """
    symbols = {
        "SPY": "SPY",    # S&P 500 ETF
        "QQQ": "QQQ",    # Nasdaq 100 ETF
        "IWM": "IWM",    # Russell 2000 ETF
        "VIX": "^VIX",   # Volatilität
        "TNX": "^TNX",   # 10y Yield * 10
        "UUP": "UUP",    # USD-Proxy ETF
        "CPC": "^CPC",   # Put/Call Ratio (oft nur daily verfügbar)
    }
    out = {}
    start = _start_date_for_weeks(max(SETTINGS.lookback_weeks, 260))
    for name, sym in symbols.items():
        try:
            df = yf.download(
                sym,
                start=start.strftime("%Y-%m-%d"),
                interval="1d",         # daily laden
                auto_adjust=False,
                progress=False,
            )
            # Auf Wochen resamplen: Schlusskurs der Woche
            if not df.empty:
                weekly = df.resample("W-FRI").last()  # Wochenende i.d.R. Freitag
                out[name] = weekly.dropna(how="all")
            else:
                out[name] = pd.DataFrame()
        except Exception:
            out[name] = pd.DataFrame()
        time.sleep(0.2)
    return out
