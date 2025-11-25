# data_sources.py
import os
import time
import json
import requests
import pandas as pd
import yfinance as yf
import re
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timedelta

from config import SETTINGS

TICKER_META: dict[str, dict] = {}
_CSV_FILE = "SP_micro_3.csv"
#_CSV_FILE = "202511_most_capitalized_500M.csv"

def _ensure_cache_dir():
    Path(SETTINGS.cache_dir).mkdir(parents=True, exist_ok=True)

def get_company_info_map_from_csv(path: str = _CSV_FILE):
    """
    Liest eine CSV mit mindestens 'Symbol' und optional 'Company', 'Industry'.
    Robust ggü. , / ; als Separator und Anführungszeichen in Namen.
    Gibt ein Dict: {symbol: {"company": str|None, "industry": str|None}} zurück.
    """
    if path is None:
        raise FileNotFoundError("CSV-Datei nicht gefunden")

    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV-Datei nicht gefunden: {path}")

    # WICHTIG: sep=None + engine='python' -> pandas versucht den Separator zu erkennen
    df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")

    # Spaltennamen vereinheitlichen (manchmal klein/anders geschrieben)
    cols = {c.lower(): c for c in df.columns}
    # Mindestens 'symbol' muss vorhanden sein
    if "symbol" not in cols:
        raise ValueError(f"Spalte 'Symbol' nicht gefunden in {path}. Gefunden: {list(df.columns)}")

    sym_col = cols["symbol"]
    comp_col = cols.get("company")
    ind_col  = cols.get("industry")

    # Strings säubern
    df[sym_col] = (
        df[sym_col].astype(str).str.strip().str.upper()
        .str.replace(r"\s+", "", regex=True)
        .str.replace(".", "-", regex=False)  # BRK.B -> BRK-B
    )

    # Doppelte Symbole entfernen, letzte Angabe gewinnt
    df = df.dropna(subset=[sym_col]).drop_duplicates(subset=[sym_col], keep="last")

    info_map = {}
    for _, row in df.iterrows():
        symbol = row[sym_col]
        company = str(row[comp_col]).strip() if comp_col in row and pd.notna(row[comp_col]) else None
        industry = str(row[ind_col]).strip() if ind_col in row and pd.notna(row[ind_col]) else None
        info_map[symbol] = {"company": company, "industry": industry}

    # Optional: kleines Logging
    print(f"[INFO MAP] {len(info_map)} Einträge aus {path} geladen.")
    return info_map

def _read_universe_csv_smart(path: str) -> pd.DataFrame:
    """
    Liest 'Symbol' (+ optional 'Company', 'Industry') robust ein.
    Erkennt gängige Separatoren (',',';','\\t') und normalisiert Header.
    """
    if not os.path.exists(path):
        # auch relativen Pfad relativ zu dieser Datei probieren
        here = os.path.dirname(__file__)
        alt = os.path.join(here, path)
        if os.path.exists(alt):
            path = alt
        else:
            raise FileNotFoundError(f"CSV-Datei nicht gefunden: {path}")

    # 1) Sniff: erst sep=None (python-engine), dann fallbacks
    df = None
    try:
        df = pd.read_csv(path, sep=None, engine="python", dtype=str, on_bad_lines="skip")
    except Exception:
        pass
    if df is None or df.empty:
        for sep in [",", ";", "\t", "|"]:
            try:
                df = pd.read_csv(path, sep=sep, dtype=str, on_bad_lines="skip")
                if not df.empty:
                    break
            except Exception:
                continue
    if df is None or df.empty:
        raise ValueError(f"CSV konnte nicht gelesen werden oder ist leer: {path}")

    # 2) Header normalisieren
    norm_map = {c: c.strip() for c in df.columns}
    df = df.rename(columns=norm_map)
    lower = {c.lower(): c for c in df.columns}

    # Symbol-Spalte finden (fallback: erste Spalte)
    sym_col = lower.get("symbol") or lower.get("ticker") or list(df.columns)[0]
    comp_col = lower.get("company")
    ind_col  = lower.get("industry")

    use_cols = [sym_col] + [c for c in [comp_col, ind_col] if c]
    df = df[use_cols].copy()

    # 3) Spaltennamen vereinheitlichen
    rename_map = {sym_col: "Symbol"}
    if comp_col: rename_map[comp_col] = "Company"
    if ind_col:  rename_map[ind_col]  = "Industry"
    df = df.rename(columns=rename_map)

    # 4) Ticker normalisieren (Trim, Upper, Whitespace raus, BRK.B -> BRK-B)
    df["Symbol"] = (
        df["Symbol"].astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\s+", "", regex=True)
        .str.replace(".", "-", regex=False)
    )
    df = df.dropna(subset=["Symbol"])
    df = df[df["Symbol"] != ""]
    df = df.drop_duplicates(subset=["Symbol"], keep="first")

    return df

def get_universe_from_csv(path: str = _CSV_FILE) -> list[str]:
    """
    Liefert die Ticker-Liste aus der CSV und füllt optional TICKER_META
    mit 'Company'/'Industry' (falls vorhanden).
    """
    df = _read_universe_csv_smart(path)

    # optionales Metadaten-Mapping befüllen
    TICKER_META.clear()
    has_company = "Company" in df.columns
    has_industry = "Industry" in df.columns
    if has_company or has_industry:
        for _, row in df.iterrows():
            TICKER_META[row["Symbol"]] = {
                "company": row.get("Company"),
                "industry": row.get("Industry"),
            }

    tickers = df["Symbol"].tolist()
    print(f"[UNIVERSE] {len(tickers)} Symbole aus {os.path.basename(path)} geladen.")
    return tickers

def get_universe() -> list[str]:
    """Einheitlicher Einstiegspunkt – aktuell CSV-Modus."""
    return get_universe_from_csv(_CSV_FILE)


def _start_date_for_weeks(weeks: int) -> datetime:
    # +10 Wochen Puffer für MAs etc.
    return datetime.utcnow() - timedelta(weeks=weeks + 10)


def _slice_ticker_from_downloaded(df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """
    Schneidet aus einem yfinance-Download-DataFrame die Spalten für 'ticker' heraus – 
    robust gegen verschiedene MultiIndex-Layouts (Ticker in Level 0 oder 1)
    und gegen Single-Ticker-Frames ohne MultiIndex.
    """
    if df is None or df.empty:
        return None

    # Fall A: Single-Ticker-Frame (keine MultiIndex-Spalten)
    if not isinstance(df.columns, pd.MultiIndex):
        cols = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in df.columns]
        return df[cols] if cols else None

    # Fall B: MultiIndex – versuche zuerst Ticker in Level 0, sonst in Level 1
    try:
        if ticker in df.columns.get_level_values(0):
            sub = df.xs(ticker, axis=1, level=0, drop_level=False)
        elif ticker in df.columns.get_level_values(1):
            sub = df.xs(ticker, axis=1, level=1, drop_level=False)
        else:
            return None
    except Exception:
        return None

    # Nach xs kann noch eine Ticker-Ebene übrig sein – versuche die OHLCV-Ebene zu isolieren
    if isinstance(sub.columns, pd.MultiIndex) and \
       any(x in sub.columns.get_level_values(-1) for x in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]):
        sub = sub.droplevel(0, axis=1) if sub.columns.nlevels > 1 else sub

    cols = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in sub.columns]
    return sub[cols] if cols else None


def load_weekly_history(universe: List[str], weeks: int = 104) -> Dict[str, pd.DataFrame]:
    """
    Lädt Weekly-Serien (Close) für alle Ticker im Universe.
    Robust gegen yfinance-MultiIndex-Varianten, lädt in Chunks, säubert NaNs.
    Gibt dict[ticker] -> DataFrame({'Close': Series}) zurück.
    """
    out: Dict[str, pd.DataFrame] = {}

    tickers = list(dict.fromkeys([t for t in universe if isinstance(t, str) and t.strip()]))
    if not tickers:
        print("[WARN] load_weekly_history: empty tickers list")
        return out

    CHUNK = 40
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i+CHUNK]
        tries = 2
        raw = None

        for attempt in range(tries):
            try:
                raw = yf.download(
                    tickers=chunk,
                    period="3y",           # 2–3 Jahre, damit 104 Wochen sicher drin sind
                    interval="1wk",
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=True,
                )
                break
            except Exception as e:
                print(f"[WARN] yfinance chunk {i//CHUNK+1} attempt {attempt+1} failed: {e}")
                time.sleep(0.8)

        if raw is None or raw.empty:
            continue

        for t in chunk:
            sub = _slice_ticker_from_downloaded(raw, t)
            if sub is None or "Close" not in sub.columns:
                continue

            s = pd.to_numeric(sub["Close"], errors="coerce").dropna()
            if s.empty:
                continue

            # Tail auf benötigte Anzahl Wochen
            s = s.tail(weeks)
            if s.empty:
                continue

            out[t] = pd.DataFrame({"Close": s})

        # leichte Pause, um Rate Limits zu meiden
        time.sleep(0.2)

    print(f"[DEBUG] load_weekly_history: built {len(out)} series (of {len(tickers)})")
    return out

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
