"""Industry strength scoring.

This module computes an industry-level composite score for the current
"Minervini leaders" list.

Metrics (formal definitions, aligned with the discussion):

1) Industry_RS_Score (0..100)
   - per industry: median of stock RS (O'Neil-like 1..99)
   - then ranked across industries and scaled to 0..100

2) Strong_Stock_Score (0..100)
   - share of stocks with RS > 80 within the industry (only valid RS)

3) Industry_Volume_Score (unbounded, typically ~[-100, +100])
   - Activity  = median(Volume_Ratio_i)
     Volume_Ratio_i = Volume_last / MA20(Volume)
   - Direction = mean(Direction_i)
     Direction_i = 1 if Close_last > MA20(Close) else 0
   - VolumeScore = (Activity * Direction - 1) * 100   (Variant 2)

4) Industry_Score (composite)
   - default weights: RS 50%, Breadth 30%, Volume 20%

Important: A stock is excluded from a metric if required inputs are missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass(frozen=True)
class IndustryScoreWeights:
    rs: float = 0.50
    strong: float = 0.30
    volume: float = 0.20


def _download_recent_daily(ticker: str, period: str = "3mo") -> pd.DataFrame:
    """Download recent daily OHLCV for a single ticker."""
    df = yf.download(
        ticker,
        period=period,
        interval="1d",
        auto_adjust=False,
        actions=False,
        repair=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    # MultiIndex can happen; try to flatten
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df = df.xs(ticker, axis=1, level=1)
        except Exception:
            # fallback: take first level if it resembles OHLCV
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def _compute_volume_activity_direction(daily: pd.DataFrame) -> Tuple[Optional[float], Optional[int]]:
    """Return (volume_ratio, direction_flag) with holiday-aware week normalization.

    The goal is to make the volume signal comparable across weeks with fewer
    trading days (e.g., Christmas week).

    Definitions:
    - volume_ratio = AvgDailyVolume_last_week / MA20(Volume)
        where AvgDailyVolume_last_week = Sum(Volume in last trading week) / (# trading days in that week)
        and MA20(Volume) is the 20-day rolling mean of daily volume (daily average).
    - direction_flag = 1 if Close_last > MA20(Close) else 0

    Returns (None, None) if insufficient data.
    """
    if daily is None or daily.empty:
        return None, None
    if "Close" not in daily.columns or "Volume" not in daily.columns:
        return None, None

    close = pd.to_numeric(daily["Close"], errors="coerce").dropna()
    vol = pd.to_numeric(daily["Volume"], errors="coerce").dropna()

    # Ensure alignment and enough points for MA20
    common_idx = close.index.intersection(vol.index)
    close = close.loc[common_idx]
    vol = vol.loc[common_idx]
    if len(close) < 21 or len(vol) < 21:
        return None, None

    close_ma20 = close.rolling(20).mean().iloc[-1]
    vol_ma20 = vol.rolling(20).mean().iloc[-1]
    close_last = close.iloc[-1]

    if pd.isna(close_ma20) or pd.isna(vol_ma20) or vol_ma20 == 0:
        return None, None

    # Last (completed) trading week based on the last available trading day.
    # Using 'W-FRI' aligns with typical weekly bars (week ends on Friday).
    week_id = vol.index.to_period("W-FRI")
    last_week = week_id[-1]
    vol_week = vol[week_id == last_week].dropna()
    n_days = int(len(vol_week))
    if n_days <= 0:
        return None, None

    avg_daily_week_vol = float(vol_week.sum() / n_days)
    volume_ratio = float(avg_daily_week_vol / vol_ma20)

    direction = int(close_last > close_ma20)
    return volume_ratio, direction


def compute_industry_scores(
    leaders: pd.DataFrame,
    *,
    industry_col: str = "Industry",
    sector_col: str = "Sektor",
    rs_col: str = "RS (O'Neil)",
    strong_rs_threshold: float = 80.0,
    weights: IndustryScoreWeights = IndustryScoreWeights(),
    daily_period: str = "3mo",
    max_workers: int = 12,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Compute industry metrics and attach per-stock industry scores.

    Returns:
      - leaders_with_scores: leaders DataFrame with new columns
      - industry_table: one row per industry with all metrics
    """
    if leaders is None or leaders.empty:
        return leaders, pd.DataFrame()

    df = leaders.copy()
    if industry_col not in df.columns or rs_col not in df.columns:
        # Can't compute without these.
        return df, pd.DataFrame()

    # --- 0b) Sector (optional) ---
    # If available, compute the most common sector per industry.
    sector_mode = None
    if sector_col in df.columns:
        sec_valid = df[[industry_col, sector_col]].dropna()
        sec_valid = sec_valid[sec_valid[industry_col].astype(str).str.lower().ne("n/a")]
        sec_valid = sec_valid[sec_valid[sector_col].astype(str).str.lower().ne("n/a")]
        if not sec_valid.empty:
            sector_mode = (
                sec_valid.groupby(industry_col)[sector_col]
                .agg(lambda s: s.value_counts().index[0])
                .rename("Sektor")
            )

    # --- 1) Per-ticker daily features (volume_ratio, direction) ---
    tickers = list(df.index)
    vol_ratio_map: Dict[str, Optional[float]] = {t: None for t in tickers}
    direction_map: Dict[str, Optional[int]] = {t: None for t in tickers}

    # Parallel download for leaders only (small list)
    max_workers = max(4, min(max_workers, len(tickers)))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_download_recent_daily, t, daily_period): t for t in tickers}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                daily = fut.result()
                vr, dflag = _compute_volume_activity_direction(daily)
                vol_ratio_map[t] = vr
                direction_map[t] = dflag
            except Exception:
                vol_ratio_map[t] = None
                direction_map[t] = None

    df["_vol_ratio_20d"] = pd.Series(vol_ratio_map)
    df["_direction_ma20"] = pd.Series(direction_map)

    # --- 2) Industry RS raw & score (ranked 0..100) ---
    rs_numeric = pd.to_numeric(df[rs_col], errors="coerce")
    df["_rs_num"] = rs_numeric

    rs_valid = df[[industry_col, "_rs_num"]].dropna()
    rs_valid = rs_valid[rs_valid[industry_col].astype(str).str.lower().ne("n/a")]

    rs_raw = rs_valid.groupby(industry_col)["_rs_num"].median().rename("Industry_RS_raw")
    if rs_raw.empty:
        # still return attached empty columns for consistency
        for c in ["Industry RS Score", "Industry Strong Stock Score", "Industry Volume Score", "Industry Score"]:
            df[c] = np.nan
        df.drop(columns=["_vol_ratio_20d", "_direction_ma20", "_rs_num"], inplace=True, errors="ignore")
        return df, pd.DataFrame()

    # Rank: lowest raw -> 0, highest -> 100
    rs_rank = rs_raw.rank(method="min")
    n_ind = len(rs_raw)
    if n_ind == 1:
        rs_score = pd.Series({rs_raw.index[0]: 100.0})
    else:
        rs_score = (rs_rank - 1) / (n_ind - 1) * 100.0
    rs_score = rs_score.rename("Industry RS Score")

    # --- 3) Strong stock score (share RS > threshold) ---
    strong_flag = rs_valid["_rs_num"] > float(strong_rs_threshold)
    strong_counts = strong_flag.groupby(rs_valid[industry_col]).sum().rename("Strong_Count")
    valid_counts = rs_valid.groupby(industry_col)["_rs_num"].count().rename("Valid_RS_Count")
    strong_ratio = (strong_counts / valid_counts).replace([np.inf, -np.inf], np.nan)
    strong_score = (strong_ratio * 100.0).rename("Industry Strong Stock Score")

    # --- 4) Volume score (Variant 2) ---
    vol_valid = df[[industry_col, "_vol_ratio_20d", "_direction_ma20"]].dropna()
    vol_valid = vol_valid[vol_valid[industry_col].astype(str).str.lower().ne("n/a")]

    activity = vol_valid.groupby(industry_col)["_vol_ratio_20d"].median().rename("Activity")
    direction = vol_valid.groupby(industry_col)["_direction_ma20"].mean().rename("Direction")
    vol_score = ((activity * direction - 1.0) * 100.0).rename("Industry Volume Score")

    # --- 5) Composite score ---
    # Align all metrics on industries
    industry_tbl = pd.concat([rs_raw, rs_score, strong_score, vol_score, activity, direction, valid_counts], axis=1)
    if sector_mode is not None:
        industry_tbl = industry_tbl.join(sector_mode, how='left')


    industry_tbl["Industry Score"] = (
        weights.rs * industry_tbl["Industry RS Score"]
        + weights.strong * industry_tbl["Industry Strong Stock Score"]
        + weights.volume * industry_tbl["Industry Volume Score"]
    )

    # --- 5b) Industry ranking (1 = highest Industry Score) ---
    # "min" gives tied industries the same best rank.
    industry_tbl["Industry Ranking"] = (
        industry_tbl["Industry Score"].rank(ascending=False, method="min").astype(int)
    )

    # --- 6) Map industry scores back to tickers ---
    for col in ["Industry Ranking", "Industry RS Score", "Industry Strong Stock Score", "Industry Volume Score", "Industry Score"]:
        df[col] = df[industry_col].map(industry_tbl[col])

    # Cleanup internal helper cols
    df.drop(columns=["_vol_ratio_20d", "_direction_ma20", "_rs_num"], inplace=True, errors="ignore")
    out_tbl = industry_tbl.reset_index().rename(columns={industry_col: "Industry"})
    # Prefer column order: Industry, Sektor, ...
    if "Sektor" in out_tbl.columns:
        cols = ["Industry", "Sektor"] + [c for c in out_tbl.columns if c not in ("Industry", "Sektor")]
        out_tbl = out_tbl[cols]
    return df, out_tbl
