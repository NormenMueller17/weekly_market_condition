"""
universe.py — Lädt das Large-Cap US-Universum für den Zertifikate-Scanner.

Quelle: iShares Russell Top 200 ETF (IWL) — enthält die 200 größten US-Aktien.
Fallback: iShares Russell 1000 (IWB), gefiltert auf Market Cap >= min_market_cap_b.
"""
from __future__ import annotations

import io
import time
from typing import List

import pandas as pd
import requests
import yfinance as yf

# BlackRock-CSV-URLs (kein API-Key nötig)
_ISHARES_URLS = {
    "IWB": (
        "https://www.ishares.com/us/products/239707/ishares-russell-1000-etf"
        "/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
    ),
    "IWV": (
        "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf"
        "/1467271812596.ajax?fileType=csv&fileName=IWV_holdings&dataType=fund"
    ),
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Ticker, die regelmäßig Probleme machen (Delisting, fehlerhafte Daten)
_BLACKLIST = {"BRK/B", "BRK.B", "BF/B", "BF.B"}


def load_large_cap_universe(rules: dict) -> List[str]:
    """
    Gibt eine Liste von Ticker-Symbolen zurück (US Large Caps > min_market_cap_b).

    Strategie:
      1. IWB (Russell 1000) laden und auf Market Cap >= min_cap filtern
      2. Falls IWB nicht erreichbar: IWV (Russell 3000) + Filter
      3. Letzter Fallback: hardcodierte S&P-100-Auswahl
    """
    min_cap = rules.get("min_market_cap_b", 150)

    tickers = _fetch_ishares("IWB")

    if not tickers:
        print("[UNIVERSE] IWB fehlgeschlagen, versuche IWV ...")
        tickers = _fetch_ishares("IWV")

    if tickers and min_cap > 0:
        tickers = _filter_by_market_cap(tickers, min_cap)

    if not tickers:
        print("[UNIVERSE] iShares nicht erreichbar -- nutze eingebettete Fallback-Liste.")
        tickers = _fallback_large_caps()

    print(f"[UNIVERSE] {len(tickers)} Titel geladen (min_cap={min_cap}B).")
    return tickers


def _fetch_ishares(etf: str) -> List[str]:
    url = _ISHARES_URLS.get(etf)
    if not url:
        print(f"[UNIVERSE] Unbekannter ETF: {etf}")
        return []

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            tickers = _parse_ishares_csv(resp.text)
            if tickers:
                return tickers
        except Exception as exc:
            print(f"[UNIVERSE] {etf} Attempt {attempt + 1}/3: {exc}")
            time.sleep(2 ** attempt)

    return []


def _parse_ishares_csv(raw: str) -> List[str]:
    """
    BlackRock-CSVs haben einen Metadaten-Header (erste ~9 Zeilen).
    Die eigentliche Tabelle beginnt mit der Zeile, die "Ticker" enthält.
    """
    lines = raw.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "Ticker" in line and "Name" in line:
            header_idx = i
            break

    if header_idx is None:
        print("[UNIVERSE] CSV-Header 'Ticker' nicht gefunden.")
        return []

    csv_block = "\n".join(lines[header_idx:])
    try:
        df = pd.read_csv(io.StringIO(csv_block))
    except Exception as exc:
        print(f"[UNIVERSE] CSV-Parse-Fehler: {exc}")
        return []

    # Spaltenname normalisieren (manchmal "Ticker", manchmal "TICKER")
    col_map = {c: c.strip() for c in df.columns}
    df.rename(columns=col_map, inplace=True)

    ticker_col = next((c for c in df.columns if c.strip().lower() == "ticker"), None)
    if ticker_col is None:
        print(f"[UNIVERSE] Keine Ticker-Spalte gefunden. Spalten: {list(df.columns)}")
        return []

    tickers = (
        df[ticker_col]
        .dropna()
        .astype(str)
        .str.strip()
        .replace("-", pd.NA)        # Zeilen ohne Ticker (Cash, etc.)
        .dropna()
        .tolist()
    )
    # Bereinigen: Nur valide US-Ticker (keine Sonderzeichen außer .)
    tickers = [t for t in tickers if t and t not in _BLACKLIST and "/" not in t]
    return tickers


def _filter_by_market_cap(tickers: List[str], min_cap_b: float) -> List[str]:
    """
    Filtert eine Ticker-Liste auf Market Cap >= min_cap_b Milliarden USD.
    Nutzt yfinance Tickers().tickers für parallele Abfragen (deutlich schneller).
    """
    print(f"[UNIVERSE] Filtere {len(tickers)} Titel auf MarketCap >= {min_cap_b}B ...")
    threshold = min_cap_b * 1e9
    filtered = []

    chunk_size = 50
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        try:
            batch = yf.Tickers(" ".join(chunk))
            for t in chunk:
                try:
                    info = batch.tickers[t].fast_info
                    mc = getattr(info, "market_cap", None) or 0
                    if mc >= threshold:
                        filtered.append(t)
                except Exception:
                    pass
        except Exception as exc:
            print(f"[UNIVERSE] Chunk-Fehler: {exc}")
        time.sleep(0.3)

    print(f"[UNIVERSE] {len(filtered)} Titel nach MarketCap-Filter.")
    return filtered if filtered else tickers  # Fallback: ungefiltert


def _fallback_large_caps() -> List[str]:
    """Hardcoded Subset der größten US-Titel als Notfall-Fallback."""
    return [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA",
        "BRK.B", "AVGO", "JPM", "LLY", "V", "UNH", "XOM", "MA", "COST",
        "HD", "PG", "JNJ", "ABBV", "BAC", "MRK", "CVX", "NFLX", "KO",
        "PEP", "ORCL", "TMO", "CRM", "ACN", "MCD", "ABT", "LIN", "TXN",
        "PM", "CSCO", "WMT", "DHR", "NEE", "ADBE", "NKE", "QCOM", "MS",
        "RTX", "GS", "HON", "AMGN", "LOW", "CAT", "SPGI", "ISRG", "BLK",
        "IBM", "PLD", "AXP", "MDT", "SBUX", "GE", "AMAT", "MMM", "ADP",
        "BKNG", "GILD", "CI", "VRTX", "PANW", "SO", "DUK", "MU", "ELV",
        "BSX", "TJX", "C", "SYK", "ZTS", "CB", "LRCX", "PGR", "KLAC",
        "REGN", "MCO", "NOW", "EQIX", "ICE", "CMG", "APH", "ITW", "HCA",
    ]
