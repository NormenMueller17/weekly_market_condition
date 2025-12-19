import pandas as pd
import yfinance as yf
import numpy as np
from data_sources import get_universe
from detect_vcp import detect_vcp

_VOLUME_BREAKOUT_SCORE = 1.3

def _compute_weighted_perf(close: pd.Series) -> float:
    """
    Berechnet eine gewichtete Performance aus 3M, 6M, 12M
    (ca. 63 / 126 / 252 Handelstage).
    Gibt np.nan zurück, falls zu wenig Historie vorhanden ist.
    """
    close = pd.to_numeric(close, errors="coerce").dropna()
    if len(close) < 252 + 1:
        return np.nan

    c_now = close.iloc[-1]

    def _ret(days: int) -> float:
        if len(close) <= days:
            return np.nan
        c_past = close.iloc[-days - 1]
        if c_past <= 0:
            return np.nan
        return c_now / c_past - 1.0

    r3  = _ret(63)   # ~ 3 Monate
    r6  = _ret(126)  # ~ 6 Monate
    r12 = _ret(252)  # ~ 12 Monate

    # Falls zu viele NaN → keine sinnvolle Kennzahl
    vals = [r for r in (r3, r6, r12) if not np.isnan(r)]
    if len(vals) == 0:
        return np.nan

    # Gewichte wie oft in der Literatur zu O'Neil-Nachbauten verwendet
    w3, w6, w12 = 0.1, 0.2, 0.7
    # fehlende Zeiträume werden einfach mit 0 gewichtet, wenn NaN
    r3  = 0.0 if np.isnan(r3)  else r3
    r6  = 0.0 if np.isnan(r6)  else r6
    r12 = 0.0 if np.isnan(r12) else r12

    weighted = w3 * r3 + w6 * r6 + w12 * r12
    return float(weighted)

def _compute_rs_scores(weighted_perfs: dict[str, float]) -> dict[str, float]:
    """
    Wandelt ein dict {ticker: weighted_perf} in O'Neil-ähnliche RS-Scores (1–99) um.
    RS = Prozentrang der gewichteten Performance im Universum.
    """
    # Nur gültige Werte verwenden
    items = [(t, p) for t, p in weighted_perfs.items() if p is not None and not np.isnan(p)]
    if not items:
        return {t: np.nan for t in weighted_perfs.keys()}

    # Nach Performance sortieren (schlechteste zuerst)
    items_sorted = sorted(items, key=lambda x: x[1])
    n = len(items_sorted)

    rs_map: dict[str, float] = {}
    for rank_idx, (ticker, perf) in enumerate(items_sorted):
        # Prozentrang 0..100
        if n == 1:
            pct = 100.0
        else:
            pct = rank_idx / (n - 1) * 100.0
        # O'Neil skaliert auf 1–99; wir nehmen gerundet
        rs_val = round(pct)
        # clamp auf 1..99
        rs_val = max(1, min(99, rs_val))
        rs_map[ticker] = float(rs_val)

    # Ticker ohne gültige Performance bekommen NaN
    for t in weighted_perfs.keys():
        if t not in rs_map:
            rs_map[t] = np.nan

    return rs_map

def compute_minervini_template(df: pd.DataFrame) -> dict:
    """Berechnet die 7 Minervini-Kriterien für ein Kurs-DataFrame mit OHLCV-Daten."""

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

    close_w = dfw["Close"].dropna()
    close = dfw["Close"]
    high = dfw["High"]
    low = dfw["Low"]
    volume = dfw["Volume"]

    # --- Neuer Block: Close aktuell / Vorwoche / Veränderung in % ---
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

    # Volumen-Breakout
    if len(volume.dropna()) > 20:
        vol20 = volume.rolling(20).mean()
        vol_breakout = volume.iloc[-1] > vol20.iloc[-1] * _VOLUME_BREAKOUT_SCORE
        vol20_val = float(vol20.iloc[-1])
        vol_score = volume.iloc[-1] / vol20_val if vol20_val and vol20_val != 0 else float("nan")
    else:
        vol_breakout = False
        vol20_val = float("nan")
        vol_score = float("nan")

    # NEU: Wochenvergleich Close
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
    }


def screen_universe_minervini(universe=None, min_score: int = 0) -> pd.DataFrame:
    """
    Screeningt ein Universum nach Minervini.
    - universe: optionale Iterable von Ticker-Symbolen. Wenn None, wird get_universe() benutzt.
    - min_score: Mindestanzahl erfüllter Kriterien (zusätzlich zum Pflicht-Kriterium 'Vol-Breakout').
    """
    tickers = list(universe) if universe is not None else list(get_universe())

    results = {}
    weighted_perfs: dict[str, float] = {}   # <-- NEU: für RS-Berechnung (aktuell)
    daily_data: dict[str, pd.DataFrame] = {}  # speichert die Daily-DFs fürs 4W-Backtest

    for t in tickers:
        try:
            df = yf.download(
                t,
                period="2y",
                interval="1d",
                auto_adjust=False,
                actions=False,
                repair=True,
                progress=False,
                threads=False,
            )

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

            # ---- NEU: RS-Performance vorbereiten ----
            close_daily = pd.to_numeric(df["Close"], errors="coerce").dropna()
            weighted_perf = _compute_weighted_perf(close_daily)
            weighted_perfs[t] = weighted_perf

            # store df for later 4-week snapshot calculation
            # ensure datetimeindex
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            daily_data[t] = df.copy()

            # ---- Minervini-Kriterien ----
            res = compute_minervini_template(df)
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

    # ---- NEU: RS-Werte berechnen (aktuell) ----
    rs_map = _compute_rs_scores(weighted_perfs)
    df_results["RS (O'Neil)"] = df_results.index.map(lambda t: rs_map.get(t, np.nan))

    # ---- NEU: weighted_perfs_4w berechnen (cut-off 4 Wochen vorher) ----
    weighted_perfs_4w: dict[str, float] = {}
    for t, df in daily_data.items():
        try:
            # letzter verfügbarer Tag
            last_dt = df.index.max()
            cutoff = last_dt - pd.Timedelta(weeks=4)

            # slice up to cutoff (inclusive)
            df_cut = df.loc[:cutoff]
            if df_cut is None or df_cut.empty:
                weighted_perfs_4w[t] = np.nan
                continue

            close_cut = pd.to_numeric(df_cut["Close"], errors="coerce").dropna()
            wp4 = _compute_weighted_perf(close_cut)
            weighted_perfs_4w[t] = wp4
        except Exception:
            weighted_perfs_4w[t] = np.nan

    # ---- RS-Scores für 4W ----
    rs_map_4w = _compute_rs_scores(weighted_perfs_4w)

    # ---- ΔRS_4w berechnen und in df_results mappen ----
    def safe_get(mapping, key):
        v = mapping.get(key, np.nan)
        return np.nan if v is None else v

    df_results["RS_now"] = df_results.index.map(lambda t: safe_get(rs_map, t))
    df_results["RS_4w"] = df_results.index.map(lambda t: safe_get(rs_map_4w, t))
    # delta: now - 4w
    df_results["RS_delta_4w"] = df_results["RS_now"] - df_results["RS_4w"]

    # ---- Minervini Leader filtern ----
    leaders = df_results[
        ((df_results["score"] - df_results["Vol-Breakout"].astype(int)) >= min_score)
    ].sort_values("score", ascending=False)

    return leaders
