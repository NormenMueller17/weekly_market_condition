import json
import pandas as pd
import yfinance as yf
import numpy as np
from pathlib import Path
from data_sources import get_universe
from detect_vcp import detect_vcp
from launchpad_detection import detect_launchpad, compute_launchpad_score

_rules_json = json.loads((Path(__file__).parent / "rules.json").read_text(encoding="utf-8"))
_VOLUME_BREAKOUT_SCORE: float = _rules_json.get("filters", {}).get("volume_breakout_score", 1.3)


def _macd_bullish_cross_weekly(df: pd.DataFrame, price_col: str = "Close",
                              fast: int = 12, slow: int = 26, signal: int = 9) -> bool:
    """
    Weekly MACD bullish cross:
      - MACD crosses above Signal (prev <=, current >)
      - only count if current MACD < 0 (early trend shift)
    Uses EMA12/EMA26 and Signal = EMA(MACD, 9).
    """
    if df is None or df.empty or price_col not in df.columns:
        return False

    s = pd.to_numeric(df[price_col], errors="coerce").dropna()
    if len(s) < (slow + signal + 2):  # conservative minimum
        return False

    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()

    # Need last two valid points
    macd = macd.dropna()
    sig = sig.dropna()
    if len(macd) < 2 or len(sig) < 2:
        return False

    # Align indices just in case
    aligned = pd.concat([macd.rename("macd"), sig.rename("sig")], axis=1).dropna()
    if len(aligned) < 2:
        return False

    prev = aligned.iloc[-2]
    cur = aligned.iloc[-1]

    cross = (prev["macd"] <= prev["sig"]) and (cur["macd"] > cur["sig"])
    early = (cur["macd"] < 0)

    return bool(cross and early)


def _compute_weighted_perf(close: pd.Series) -> float:
    """
    Berechnet eine gewichtete Performance aus 3M, 6M, 12M
    (ca. 63 / 126 / 252 Handelstage).
    Gibt np.nan zurück, falls zu wenig Historie vorhanden ist.
    
    Gewichtung: 12M=50%, 6M=30%, 3M=20%
    """
    if close is None or len(close) < 63:
        return float("nan")
    
    c = pd.to_numeric(close, errors="coerce").dropna()
    if len(c) < 63:
        return float("nan")
    
    # Performance berechnen (Return in %)
    def calc_return(periods):
        if len(c) < periods + 1:
            return None
        return (c.iloc[-1] / c.iloc[-periods - 1] - 1.0) * 100.0
    
    perf_3m = calc_return(63)    # ~3 Monate
    perf_6m = calc_return(126)   # ~6 Monate
    perf_12m = calc_return(252)  # ~12 Monate
    
    # Gewichtete Performance
    weights = []
    perfs = []
    
    if perf_12m is not None:
        weights.append(0.5)
        perfs.append(perf_12m)
    if perf_6m is not None:
        weights.append(0.3)
        perfs.append(perf_6m)
    if perf_3m is not None:
        weights.append(0.2)
        perfs.append(perf_3m)
    
    if not weights:
        return float("nan")
    
    # Normalisieren falls nicht alle Perioden verfügbar
    total_weight = sum(weights)
    weighted_perf = sum(p * w for p, w in zip(perfs, weights)) / total_weight
    
    return float(weighted_perf)


def _compute_rs_scores(weighted_perfs: dict[str, float]) -> dict[str, float]:
    """
    Compute O'Neil-style Relative Strength scores (1-99) from weighted performances.
    
    Args:
        weighted_perfs: Dict mapping ticker -> weighted performance
        
    Returns:
        Dict mapping ticker -> RS score (1-99)
    """
    valid = {t: p for t, p in weighted_perfs.items() if not pd.isna(p)}
    if not valid:
        return {}
    
    s = pd.Series(valid)
    ranked = s.rank(pct=True, method="average") * 98.0 + 1.0  # Scale to 1-99
    
    return ranked.to_dict()


def compute_minervini_template(df: pd.DataFrame) -> dict:
    """
    Prüft ein Ticker-DataFrame gegen die Minervini-Kriterien (wöchentlich).
    Gibt ein Dictionary mit allen Metriken zurück.
    """
    # Wochenaggregation
    dfw = df.resample("W-FRI").agg({
        "Close": "last",
        "High": "max",
        "Low": "min",
        "Volume": "sum"
    }).dropna()

    vcp_result = detect_vcp(dfw, window=60)
    vcp_flag = vcp_result.get("VCP", False)
    vcp_waves = vcp_result.get("Waves", 0)
    vcp_entry = vcp_result.get("Entry_Signal", False)
    vcp_breakout = vcp_result.get("Breakout_Level", None)
  
    launchpad_result = detect_launchpad(dfw)
    launchpad_score = compute_launchpad_score(launchpad_result)

    close_w = dfw["Close"].dropna()
    close = dfw["Close"]
    high = dfw["High"]
    low = dfw["Low"]
    volume = dfw["Volume"]
  
    # --- Close aktuell / Vorwoche / Veränderung in % ---
    if len(close_w) >= 2:
        close_weekly_now = float(close_w.iloc[-1])
        close_weekly_prev = float(close_w.iloc[-2])
        if close_weekly_prev != 0:
            close_weekly_change_pct = (close_weekly_now / close_weekly_prev - 1.0) * 100.0
        else:
            close_weekly_change_pct = float("nan")
    elif len(close_w) == 1:
        close_weekly_now = float(close_w.iloc[-1])
        close_weekly_prev = float("nan")
        close_weekly_change_pct = float("nan")
    else:
        close_weekly_now = float("nan")
        close_weekly_prev = float("nan")
        close_weekly_change_pct = float("nan")

    # Gleitende Durchschnitte
    sma10 = close.rolling(10).mean()
    sma30 = close.rolling(30).mean()
    sma40 = close.rolling(40).mean()

    sma10_rising = len(sma10.dropna()) > 1 and sma10.iloc[-1] > sma10.iloc[-2]
    sma30_rising = len(sma30.dropna()) > 1 and sma30.iloc[-1] > sma30.iloc[-2]
    sma40_rising = len(sma40.dropna()) > 1 and sma40.iloc[-1] > sma40.iloc[-2]
    ma_order = len(sma40.dropna()) > 0 and sma10.iloc[-1] > sma30.iloc[-1] and sma30.iloc[-1] > sma40.iloc[-1]

    # --- 52W-Regel + 52W High + Distanz ---
    if len(close) >= 52:
        high_52w_series = high.rolling(52).max()
        low_52w_series = low.rolling(52).min()

        high_52w = float(high_52w_series.iloc[-1])
        low_52w = float(low_52w_series.iloc[-1])

        tt_range_ok = (close.iloc[-1] >= 1.30 * low_52w) and (close.iloc[-1] >= 0.75 * high_52w)
    else:
        high_52w = float("nan")
        low_52w = float("nan")
        tt_range_ok = False

    last_close = float(close.iloc[-1])
    if not pd.isna(high_52w) and high_52w > 0:
        dist_to_52w_high_pct = (high_52w - last_close) / high_52w * 100.0
    else:
        dist_to_52w_high_pct = float("nan")

    # RS-Trend
    sma13 = close.rolling(13).mean()
    rs_trend = len(sma13.dropna()) > 0 and close.iloc[-1] > sma13.iloc[-1]

    # --- Volume (holiday-aware): compare avg DAILY volume of last trading week vs MA20 of DAILY volume ---
    daily_vol = pd.to_numeric(df["Volume"], errors="coerce").dropna() if "Volume" in df.columns else pd.Series(dtype=float)
    if len(daily_vol) >= 20:
        vol20_val = float(daily_vol.rolling(20).mean().iloc[-1])
        # Last (completed) trading week based on last available trading day (W-FRI).
        week_id = daily_vol.index.to_period("W-FRI")
        last_week = week_id[-1]
        vol_week = daily_vol[week_id == last_week].dropna()
        n_days = int(len(vol_week))
        if n_days > 0 and (not pd.isna(vol20_val)) and vol20_val != 0:
            avg_daily_week_vol = float(vol_week.sum() / n_days)
            vol_score = avg_daily_week_vol / vol20_val
            vol_breakout = avg_daily_week_vol > vol20_val * _VOLUME_BREAKOUT_SCORE
        else:
            vol_score = float("nan")
            vol_breakout = False
    else:
        vol_breakout = False
        vol20_val = float("nan")
        vol_score = float("nan")

    # --- ATR / Price (14) on DAILY data ---
    atr_pct = float("nan")
    try:
        dfd = df[["High", "Low", "Close"]].copy()
        dfd["High"] = pd.to_numeric(dfd["High"], errors="coerce")
        dfd["Low"] = pd.to_numeric(dfd["Low"], errors="coerce")
        dfd["Close"] = pd.to_numeric(dfd["Close"], errors="coerce")
        dfd = dfd.dropna()
        if len(dfd) >= 15:
            prev_close = dfd["Close"].shift(1)
            tr = pd.concat([
                (dfd["High"] - dfd["Low"]).abs(),
                (dfd["High"] - prev_close).abs(),
                (dfd["Low"] - prev_close).abs(),
            ], axis=1).max(axis=1)
            atr14 = tr.rolling(14).mean().iloc[-1]
            last_close_d = float(dfd["Close"].iloc[-1])
            if last_close_d not in (0, None) and not pd.isna(atr14):
                atr_pct = float(atr14) / last_close_d * 100.0
    except Exception:
        atr_pct = float("nan")

    # Wochenvergleich Close
    if len(close) >= 2:
        weekly_momentum = close.iloc[-1] > close.iloc[-2]
    else:
        weekly_momentum = False

    criteria = {
        "SMA10W steigend": sma10_rising,
        "SMA30W steigend": sma30_rising,
        "SMA40W steigend": sma40_rising,
        "MA-Ordnung 10>30>40": ma_order,
        "52W Range OK": tt_range_ok,
        "RS-Trend ↑": rs_trend,
        "Vol-Breakout": vol_breakout,
        "Close > Vorwoche": weekly_momentum,
    }

    score = sum(int(v) for v in criteria.values())

    return {
        "score": score,
        **criteria,
        # zusätzliche Kennzahlen, die wir später im Report nutzen:
        "52W High": high_52w,
        "Dist to 52W High (%)": dist_to_52w_high_pct,
        "vol20": vol20_val,
        "vol_score": vol_score,
        "close_weekly_now": close_weekly_now,
        "close_weekly_prev": close_weekly_prev,
        "close_weekly_change_pct": close_weekly_change_pct,
        "VCP": vcp_flag,
        "VCP Waves": vcp_waves,
        "VCP Entry": vcp_entry,
        "VCP Breakout Level": vcp_breakout,
        "ATR / Price (%)": atr_pct,
        "Launchpad": launchpad_result.get("Launchpad", False),
        "Launchpad Score": launchpad_score,
        "Launchpad Weeks": launchpad_result.get("Base_Weeks", 0),
        "Launchpad Range (%)": launchpad_result.get("Range_Pct", float("nan")),
        "Launchpad Pivot": launchpad_result.get("Pivot_Level", None),
    }


def screen_universe_minervini(universe=None, min_score: int = 0) -> pd.DataFrame:
    """
    Screeningt ein Universum nach Minervini.
    - universe: optionale Iterable von Ticker-Symbolen. Wenn None, wird get_universe() benutzt.
    - min_score: Mindestanzahl erfüllter Kriterien (zusätzlich zum Pflicht-Kriterium 'Vol-Breakout').
    """
    tickers = list(universe) if universe is not None else list(get_universe())

    results = {}
    weighted_perfs: dict[str, float] = {}
    daily_data: dict[str, pd.DataFrame] = {}

    for t in tickers:
        try:
            df = yf.download(
                t,
                period="2y",
                interval="1d",
                auto_adjust=False,
                actions=False,
                repair=False,
                progress=False,
                threads=False,
            )                        
            if df is not None and not df.empty:
                df = df.copy()
              
            if df.empty:
                print(f"{t}: keine Daten")
                continue

            # MultiIndex (selten, aber möglich)
            if isinstance(df.columns, pd.MultiIndex):
                try:
                    df = df.xs(t, axis=1, level=1)
                except Exception:
                    print(f"{t}: MultiIndex ohne {t} – übersprungen")
                    continue

            # Fehlende Spalten robuster auffüllen
            required = {"Close", "High", "Low", "Volume"}
            have = set(df.columns)
            if not required.issubset(have):
                if "Adj Close" in df.columns and "Close" not in have:
                    df["Close"] = df["Adj Close"]
                if "High" not in have:
                    df["High"] = df["Close"]
                if "Low" not in have:
                    df["Low"] = df["Close"]
                if "Volume" not in have:
                    df["Volume"] = 0

            # RS-Performance vorbereiten
            close_daily = pd.to_numeric(df["Close"], errors="coerce").dropna()
            weighted_perf = _compute_weighted_perf(close_daily)
            weighted_perfs[t] = weighted_perf

            # Store df for later 4-week snapshot calculation
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            daily_data[t] = df.copy()

            # Minervini-Kriterien
            res = compute_minervini_template(df)
            wdf = (
                df[["Close"]]
                .copy()
                .resample("W-FRI")
                .last()
                .dropna()
            )
            res["MACD Bullish Cross (W)"] = _macd_bullish_cross_weekly(wdf)

            results[t] = res

        except Exception as e:
            print(f"Fehler bei {t}: {e}")
            continue

    if not results:
        return pd.DataFrame()

    df_results = pd.DataFrame(results).T

    # Wenn score fehlt → sofort abbrechen
    if "score" not in df_results.columns:
        return pd.DataFrame()

    # RS-Werte berechnen (aktuell)
    rs_map = _compute_rs_scores(weighted_perfs)
    df_results["RS (O'Neil)"] = df_results.index.map(lambda t: rs_map.get(t, np.nan))

    # Weighted_perfs_4w berechnen (cut-off 4 Wochen vorher)
    weighted_perfs_4w: dict[str, float] = {}
    for t, df in daily_data.items():
        try:
            last_dt = df.index.max()
            cutoff = last_dt - pd.Timedelta(weeks=4)

            df_cut = df.loc[:cutoff]
            if df_cut is None or df_cut.empty:
                weighted_perfs_4w[t] = np.nan
                continue

            close_cut = pd.to_numeric(df_cut["Close"], errors="coerce").dropna()
            wp4 = _compute_weighted_perf(close_cut)
            weighted_perfs_4w[t] = wp4
        except Exception:
            weighted_perfs_4w[t] = np.nan

    # RS-Scores für 4W
    rs_map_4w = _compute_rs_scores(weighted_perfs_4w)

    # ΔRS_4w berechnen und in df_results mappen
    def safe_get(mapping, key):
        v = mapping.get(key, np.nan)
        return np.nan if v is None else v

    df_results["RS_now"] = df_results.index.map(lambda t: safe_get(rs_map, t))
    df_results["RS_4w"] = df_results.index.map(lambda t: safe_get(rs_map_4w, t))
    df_results["RS_delta_4w"] = df_results["RS_now"] - df_results["RS_4w"]

    # Minervini Leader filtern
    leaders = df_results[
        ((df_results["score"] - df_results["Vol-Breakout"].astype(int)) >= min_score)
    ].sort_values("score", ascending=False)


    return leaders


