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
from rate_limit import RateLimiter

TICKER_BLACKLIST = {
    "BFHIV",  # Delisted - no price data
    "C/PN",   # Invalid ticker format (slash not allowed)
}
TICKER_META: dict[str, dict] = {}
_CSV_FILE = "202511_most_capitalized_500M_3.csv"
#_CSV_FILE = "2025_11_Most_Capitalized_DE.csv"
#_CSV_FILE = "202511_most_capitalized_500M_3_mini.csv"
#_CSV_FILE = "202511_test_3.csv"

GLOBAL_LIMITER = RateLimiter(max_calls=6, period_seconds=1.0)

def download_ohlcv_batched(
    tickers: List[str],
    *,
    period: str,
    interval: str,
    chunk_size: int = 40,
    auto_adjust: bool = False,
    threads: bool = False,
    tries: int = 2,
    retry_sleep_s: float = 0.8,
    inter_chunk_sleep_s: float = 0.2,
    ) -> Dict[str, pd.DataFrame]:

    out: Dict[str, pd.DataFrame] = {}

    # Defensive normalization: keep stable order, remove empties
    clean = list(dict.fromkeys([t.strip() for t in tickers if isinstance(t, str) and t.strip()]))
    if not clean:
        return out

    CHUNK = max(1, int(chunk_size))
    total_chunks = (len(clean) + CHUNK - 1) // CHUNK

    for i in range(0, len(clean), CHUNK):
        chunk_idx = (i // CHUNK) + 1
        chunk = clean[i:i + CHUNK]

        raw: Optional[pd.DataFrame] = None

        for attempt in range(tries):
            try:
                raw = yf.download(
                    tickers=chunk,
                    period=period,
                    interval=interval,
                    group_by="ticker",
                    auto_adjust=auto_adjust,
                    progress=False,
                    threads=threads,
                )
                break
            except Exception as e:
                # Match your previous logging style closely
                print(f"[WARN] yfinance chunk {chunk_idx}/{total_chunks} attempt {attempt+1} failed: {e}")
                time.sleep(retry_sleep_s)

        if raw is None or raw.empty:
            # could not download anything for this chunk
            continue

        # ---- Parse the returned DataFrame into per-ticker subframes ----
        # yfinance returns either:
        #   A) MultiIndex columns: (Ticker, Field) for multi ticker calls
        #   B) Single-index columns: Field only, if only one ticker
        # We handle both.

        if isinstance(raw.columns, pd.MultiIndex):
            # Multi-ticker case
            level0 = raw.columns.get_level_values(0)

            for t in chunk:
                # Some tickers might not be present in the result if Yahoo had no data
                if t not in level0:
                    continue

                sub = raw[t].copy()
                # Drop rows where all OHLCV are NaN
                sub = sub.dropna(how="all")
                if sub.empty:
                    continue

                out[t] = sub

        else:
            # Single ticker case: raw is already OHLCV for that ticker
            t = chunk[0]
            sub = raw.copy().dropna(how="all")
            if not sub.empty:
                out[t] = sub

        # Small pause between chunks to be nicer to Yahoo
        time.sleep(inter_chunk_sleep_s)

    return out

def _ensure_cache_dir():
    Path(SETTINGS.cache_dir).mkdir(parents=True, exist_ok=True)

def get_company_info_map_from_csv(path: str = _CSV_FILE) -> dict[str, dict[str, str]]:
    """
    Liest path (CSV) robust ein (Separator-Autodetect) und liefert ein Mapping:
      { 'AAPL': {'Company': 'Apple Inc.', 'Industry': '...'}, ... }

    Erkennt automatisch, ob die Namensspalte 'Company' oder 'Description' heißt.
    Ticker werden identisch zu get_universe_from_csv normalisiert.
    """
    # -> robustes Einlesen (unterstützt ',', ';', '\t', '|', on_bad_lines='skip')
    df = _read_universe_csv_smart(path)
    if df is None or df.empty:
        return {}

    # Spalten wie im Smart-Loader vereinheitlichen: Symbol, Company?, Industry?
    # Falls keine Company/Industry vorliegen, mit 'n/a' auffüllen.
    comp = (
        df["Company"].astype(str).fillna("n/a").str.strip()
        if "Company" in df.columns else
        pd.Series(["n/a"] * len(df), index=df.index)
    )
    ind = (
        df["Industry"].astype(str).fillna("n/a").str.strip()
        if "Industry" in df.columns else
        pd.Series(["n/a"] * len(df), index=df.index)
    )

    info_map = {
        sym: {"Company": c if c else "n/a", "Industry": i if i else "n/a"}
        for sym, c, i in zip(df["Symbol"], comp, ind)
    }

    print(f"[INFO MAP] {len(info_map)} Einträge aus {os.path.basename(path)} geladen.")
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
    #df["Symbol"] = (
    #    df["Symbol"].astype(str)
    #    .str.strip()
    #    .str.upper()
    #    .str.replace(r"\s+", "", regex=True)
    #    .str.replace(".", "-", regex=False)
    #)
    
    df["Symbol"] = df["Symbol"].apply(_normalize_symbol)
    df = df.dropna(subset=["Symbol"])
    df = df[df["Symbol"] != ""]
    df = df.drop_duplicates(subset=["Symbol"], keep="first")

    return df

def _normalize_symbol(sym: str) -> str:
    """
    Normalisiert Ticker:
    - Whitespace entfernen
    - Großbuchstaben
    - BRK.B -> BRK-B
    - SAP.DE, SIE.DE usw. bleiben mit '.DE' erhalten
    """
    if not isinstance(sym, str):
        sym = str(sym)

    s = sym.strip().upper()
    s = re.sub(r"\s+", "", s)

    # Deutsche Listings mit .DE am Ende NICHT anfassen
    if s.endswith(".DE"):
        return s

    # Bei anderen Tickers (z.B. BRK.B) den Punkt in Dash wandeln
    # (falls du das weiterhin brauchst)
    s = s.replace(".", "-")
    return s


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

    tickers = df["Symbol"].apply(_normalize_symbol)
    
    # Filter out invalid tickers and blacklisted symbols
    tickers = [t for t in dict.fromkeys(tickers) if t and t != "NAN"]
    
    # Apply blacklist
    tickers_before = len(tickers)
    tickers = [t for t in tickers if t not in TICKER_BLACKLIST]
    filtered_count = tickers_before - len(tickers)
    
    if filtered_count > 0:
        print(f"[UNIVERSE] Filtered out {filtered_count} blacklisted ticker(s): {TICKER_BLACKLIST}")
    
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
    """Load weekly OHLCV history for the universe using batched downloads.

    Phase-1 screening should run on cheap bulk weekly downloads and must not
    call expensive per-ticker endpoints (like `Ticker.info`).

    Returns
    -------
    Dict[ticker, DataFrame]
        Weekly OHLCV data with columns at least: Close, High, Low, Volume.
    """
    out: Dict[str, pd.DataFrame] = {}

    tickers = list(dict.fromkeys([t for t in universe if isinstance(t, str) and t.strip()]))
    if not tickers:
        print("[WARN] load_weekly_history: empty tickers list")
        return out

    # Use batched OHLCV downloads.
    # Keep period long enough so `weeks` is always available (weeks=104 -> ~2y).
    # Using 3y gives buffer for missing weeks / holidays / sparse assets.
    batched = download_ohlcv_batched(
        tickers=tickers,
        period="3y",
        interval="1wk",
        chunk_size=40,        # keep your previous CHUNK=40 behavior
        auto_adjust=False,
        threads=False,
    )

    if not batched:
        print(f"[DEBUG] load_weekly_history: built 0 series (of {len(tickers)})")
        return out

    for t, sub in batched.items():
        if sub is None or sub.empty:
            continue

        # Ensure required columns exist (yfinance sometimes omits Volume for some assets)
        cols = set(sub.columns)
        if "Close" not in cols:
            continue
        if "High" not in cols:
            sub["High"] = sub["Close"]
        if "Low" not in cols:
            sub["Low"] = sub["Close"]
        if "Volume" not in cols:
            sub["Volume"] = 0

        # Coerce numeric + drop empty
        for c in ("Close", "High", "Low", "Volume"):
            sub[c] = pd.to_numeric(sub[c], errors="coerce")
        sub = sub.dropna(subset=["Close"]).copy()
        if sub.empty:
            continue

        # Tail to required number of weeks
        sub = sub.tail(weeks)
        if sub.empty:
            continue

        # Keep same output column selection behavior as before
        out[t] = sub[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in sub.columns]].copy()

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
