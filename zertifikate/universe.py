"""
universe.py — Lädt das Large-Cap US-Universum für den Zertifikate-Scanner.

Quelle: iShares Russell Top 200 ETF (IWL) — enthält die 200 größten US-Aktien.
Fallback: iShares Russell 1000 (IWB), gefiltert auf Market Cap >= min_market_cap_b.
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import List

import pandas as pd
import requests
import yfinance as yf

# Lokaler JSON-Cache für Company-Info — unabhängig vom yfinance HTTP-Cache
_COMPANY_INFO_CACHE_PATH = Path(__file__).parent / "company_info_cache.json"

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


def fetch_company_info(tickers: List[str], seed_info: dict | None = None) -> dict:
    """
    Holt Name und Sektor für eine Liste von Tickern.
    Rückgabe: {ticker: {"name": str, "sector": str}}

    Strategie:
      1. seed_info (aus iShares-CSV) als Basis verwenden
      2. Lokalen JSON-Cache lesen (zertifikate/company_info_cache.json)
      3. Nur noch wirklich fehlende Ticker via yfinance .info nachladen
      4. Cache aktualisieren
    """
    # ── 1. seed_info (iShares-CSV) als Basis ─────────────────────────────────
    cached: dict = dict(seed_info) if seed_info else {}

    # ── 2. Lokalen Cache laden und mit seed_info zusammenführen ──────────────
    if _COMPANY_INFO_CACHE_PATH.exists():
        try:
            disk = json.loads(_COMPANY_INFO_CACHE_PATH.read_text(encoding="utf-8"))
            # Disk-Cache hat Vorrang gegenüber seed_info (manuell gepflegt / yfinance-Daten)
            for t, v in disk.items():
                if v.get("name") and v["name"] != t:  # nur echte Namen
                    cached[t] = v
        except Exception:
            pass

    # ── 3. Fehlende Ticker via yfinance nachladen ─────────────────────────────
    missing = [t for t in tickers if t not in cached or cached[t].get("name") == t]

    if missing:
        print(f"[INFO] Lade Company-Info für {len(missing)} Ticker via yfinance …")
        updated = False
        for t in missing:
            try:
                inf = yf.Ticker(t).info or {}
                name   = inf.get("longName") or inf.get("shortName")
                sector = inf.get("sector") or inf.get("industry")
                if name:
                    cached[t] = {"name": name, "sector": sector or "n/a"}
                    updated = True
                else:
                    cached.setdefault(t, {"name": t, "sector": "n/a"})
            except Exception as exc:
                print(f"[INFO] fetch_company_info: {t} fehlgeschlagen ({exc})")
                cached.setdefault(t, {"name": t, "sector": "n/a"})
            time.sleep(0.1)

        # ── 4. Cache nur bei echten Daten persistieren ────────────────────────
        if updated:
            try:
                _COMPANY_INFO_CACHE_PATH.write_text(
                    json.dumps(cached, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[INFO] Company-Info-Cache aktualisiert ({len(cached)} Einträge).")
            except Exception as exc:
                print(f"[WARN] Cache-Schreiben fehlgeschlagen: {exc}")

    return {t: cached.get(t, {"name": t, "sector": "n/a"}) for t in tickers}


def load_large_cap_universe(rules: dict) -> tuple[List[str], dict]:
    """
    Gibt (tickers, company_info) zurück.
    tickers     : Liste von Ticker-Symbolen (US Large Caps > min_market_cap_b)
    company_info: {ticker: {"name": str, "sector": str}} — direkt aus iShares-CSV

    Strategie:
      1. IWB (Russell 1000) laden und auf Market Cap >= min_cap filtern
      2. Falls IWB nicht erreichbar: IWV (Russell 3000) + Filter
      3. Letzter Fallback: hardcodierte S&P-100-Auswahl (ohne company_info)
    """
    min_cap = rules.get("min_market_cap_b", 150)

    tickers, csv_info = _fetch_ishares("IWB")

    if not tickers:
        print("[UNIVERSE] IWB fehlgeschlagen, versuche IWV ...")
        tickers, csv_info = _fetch_ishares("IWV")

    if tickers and min_cap > 0:
        tickers = _filter_by_market_cap(tickers, min_cap)
        csv_info = {t: v for t, v in csv_info.items() if t in tickers}

    if not tickers:
        print("[UNIVERSE] iShares nicht erreichbar -- nutze eingebettete Fallback-Liste.")
        tickers  = _fallback_large_caps()
        csv_info = {}

    print(f"[UNIVERSE] {len(tickers)} Titel geladen (min_cap={min_cap}B), "
          f"{len(csv_info)} mit Name/Sektor aus CSV.")
    return tickers, csv_info


def _fetch_ishares(etf: str) -> tuple[List[str], dict]:
    url = _ISHARES_URLS.get(etf)
    if not url:
        print(f"[UNIVERSE] Unbekannter ETF: {etf}")
        return [], {}

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            tickers, company_info = _parse_ishares_csv(resp.text)
            if tickers:
                return tickers, company_info
        except Exception as exc:
            print(f"[UNIVERSE] {etf} Attempt {attempt + 1}/3: {exc}")
            time.sleep(2 ** attempt)

    return [], {}


def _parse_ishares_csv(raw: str) -> tuple[List[str], dict]:
    """
    Parst BlackRock-CSV (Metadaten-Header übersprungen).
    Gibt (tickers, company_info) zurück.
    company_info: {ticker: {"name": str, "sector": str}}
    """
    lines = raw.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "Ticker" in line and "Name" in line:
            header_idx = i
            break

    if header_idx is None:
        print("[UNIVERSE] CSV-Header 'Ticker' nicht gefunden.")
        return [], {}

    csv_block = "\n".join(lines[header_idx:])
    try:
        df = pd.read_csv(io.StringIO(csv_block))
    except Exception as exc:
        print(f"[UNIVERSE] CSV-Parse-Fehler: {exc}")
        return [], {}

    # Spaltennamen normalisieren
    df.rename(columns={c: c.strip() for c in df.columns}, inplace=True)

    ticker_col = next((c for c in df.columns if c.strip().lower() == "ticker"), None)
    name_col   = next((c for c in df.columns if c.strip().lower() == "name"),   None)
    sector_col = next((c for c in df.columns if c.strip().lower() == "sector"), None)

    if ticker_col is None:
        print(f"[UNIVERSE] Keine Ticker-Spalte gefunden. Spalten: {list(df.columns)}")
        return [], {}

    df[ticker_col] = df[ticker_col].astype(str).str.strip()
    mask = (
        df[ticker_col].notna()
        & (df[ticker_col] != "-")
        & (df[ticker_col] != "nan")
        & ~df[ticker_col].str.contains("/", na=False)
        & ~df[ticker_col].isin(_BLACKLIST)
    )
    df = df[mask]

    tickers = df[ticker_col].tolist()

    # Name + Sektor direkt aus CSV befüllen
    company_info: dict = {}
    for _, row in df.iterrows():
        t = row[ticker_col]
        name   = str(row[name_col]).strip()   if name_col   and pd.notna(row[name_col])   else ""
        sector = str(row[sector_col]).strip() if sector_col and pd.notna(row[sector_col]) else "n/a"
        if name and name != t and name.lower() not in ("nan", "-", ""):
            company_info[t] = {"name": name, "sector": sector or "n/a"}

    return tickers, company_info


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
