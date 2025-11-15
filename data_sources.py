# data_sources.py
import os
import time
import json
import requests
import pandas as pd
import yfinance as yf
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timedelta

from config import SETTINGS

_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

def _ensure_cache_dir():
    Path(SETTINGS.cache_dir).mkdir(parents=True, exist_ok=True)

def get_sp500_tickers() -> list[str]:
    """
    Holt die S&P 500 Ticker von Wikipedia, setzt einen Browser-User-Agent
    (wichtig für GitHub Actions) und gibt Yahoo-kompatible Symbole zurück
    (z.B. 'BRK.B' -> 'BRK-B').
    """
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        # einfacher, unauffälliger UA – verhindert 403 auf dem Runner
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    }

    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()  # wirft bei 4xx/5xx eine Exception

    # HTML lokalen Parsern übergeben (nicht mehr direkt mit read_html(url) -> 403)
    tables = pd.read_html(resp.text)
    if not tables:
        raise RuntimeError("Keine Tabellen auf der S&P500-Wikipedia-Seite gefunden.")

    df = tables[0]  # die erste Tabelle enthält die Konstituenten
    if "Symbol" not in df.columns:
        # gelegentlich ändert Wikipedia Spaltennamen geringfügig
        # Fallback: erste Spalte annehmen
        symbol_col = df.columns[0]
    else:
        symbol_col = "Symbol"

    # Symbole bereinigen und Yahoo-kompatibel machen
    tickers = (
        df[symbol_col]
        .astype(str)
        .str.strip()
        .str.replace(r"\.", "-", regex=True)  # BRK.B -> BRK-B
        .tolist()
    )

    # optional: Dubletten und leere Werte raus
    tickers = [t for t in dict.fromkeys(tickers) if t]
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

    #Log-Ausgabe zur Datenqualität
    ok = sum(1 for t, df in weeks.items() if isinstance(df, pd.DataFrame) and not df.empty)
    print(f"[DEBUG] Weekly non-empty datasets: {ok}/{len(weekly)}")
    
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
        #"CPC": "^CPC",   # Put/Call Ratio (oft nur daily verfügbar)
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
