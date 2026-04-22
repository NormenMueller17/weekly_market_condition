"""
TraderLion Launchpad Pattern Detection

Das Launchpad ist eine tight consolidation (3-5 Wochen) über wichtigen MAs
mit Volume Contraction, gefolgt von einem Volume-Breakout.

Unterschied zu VCP:
- Kürzere Base (3-5 Wochen vs. 8-12 Wochen)
- Strengere Volatilitätskriterien (<10% Range)
- Fokus auf unmittelbare Breakout-Candidates
"""

from __future__ import annotations
import pandas as pd
import numpy as np
import math


def detect_launchpad(
    df: pd.DataFrame,
    base_weeks_min: int = 3,
    base_weeks_max: int = 5,
    max_range_pct: float = 0.08,
    min_volume_contraction: float = 0.70,
    require_above_ma50: bool = True,
    require_prior_trend: bool = True,
    breakout_volume_factor: float = 1.40,
    pivot_proximity_pct: float = 0.03,
) -> dict:
    """
    Detect TraderLion Launchpad pattern.

    Parameters
    ----------
    df : pd.DataFrame
        Weekly OHLCV data (from resample)
    base_weeks_min : int
        Minimum consolidation weeks (default: 3)
    base_weeks_max : int
        Maximum consolidation weeks (default: 5)
    max_range_pct : float
        Max allowed range during base (default: 8%)
    min_volume_contraction : float
        vol_second_half / vol_first_half must be below this (0.70 = 30% contraction)
    require_above_ma50 : bool
        Require price above 50-week MA
    require_prior_trend : bool
        Require MA20 > MA50 and MA50 rising (Stage-2 confirmation)
    breakout_volume_factor : float
        max(last 3 bars) >= factor * base_avg triggers Launchpad_Entry (default: 1.40)
    pivot_proximity_pct : float
        Close must be within this % of pivot for Near_Pivot (default: 3%)

    Returns
    -------
    dict
        {
            "Launchpad": bool,
            "Launchpad_Entry": bool,
            "Base_Weeks": int,
            "Range_Pct": float,
            "Volume_Contraction": float,
            "Volume_Dry": bool,
            "Pivot_Level": float | None,
            "Near_Pivot": bool,
            "Above_MA50": bool,
            "Prior_Trend": bool,
        }
    """
    
    result = {
        "Launchpad": False,
        "Launchpad_Entry": False,
        "Base_Weeks": 0,
        "Range_Pct": float("nan"),
        "Volume_Contraction": float("nan"),
        "Volume_Dry": False,
        "Pivot_Level": None,
        "Near_Pivot": False,
        "Above_MA50": False,
        "Prior_Trend": False,
    }
    
    # Basic checks
    if df is None or df.empty:
        return result
    
    required = {"Close", "High", "Low", "Volume"}
    if not required.issubset(df.columns):
        return result
    
    df = df.dropna().copy()
    if len(df) < 50:  # Need at least 50 weeks for MA
        return result
    
    # Extract series
    close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    high = pd.to_numeric(df["High"], errors="coerce").dropna()
    low = pd.to_numeric(df["Low"], errors="coerce").dropna()
    volume = pd.to_numeric(df["Volume"], errors="coerce").dropna()
    
    if len(close) < 50:
        return result
    
    # -----------------------------------------------------------------------
    # 1) MA Alignment + Prior-Trend Check
    # -----------------------------------------------------------------------
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()

    last_close = float(close.iloc[-1])
    last_ma10  = float(ma10.iloc[-1])
    last_ma20  = float(ma20.iloc[-1])
    last_ma50  = float(ma50.iloc[-1])

    if not (math.isfinite(last_close) and math.isfinite(last_ma20) and math.isfinite(last_ma50)):
        return result

    above_ma10 = last_close > last_ma10
    above_ma50 = last_close > last_ma50
    result["Above_MA50"] = above_ma50

    if require_above_ma50 and not above_ma50:
        return result

    if not above_ma10:
        return result

    # Prior trend: MA20 > MA50, MA50 rising over last 4 weeks
    ma50_4w_ago = float(ma50.iloc[-5]) if len(ma50) >= 5 else float("nan")
    prior_trend = (
        last_ma20 > last_ma50
        and math.isfinite(ma50_4w_ago)
        and last_ma50 > ma50_4w_ago
    )
    result["Prior_Trend"] = prior_trend

    if require_prior_trend and not prior_trend:
        return result
    
    # -----------------------------------------------------------------------
    # 2) Find Tight Base (last N weeks)
    # -----------------------------------------------------------------------
    # Try different base lengths
    best_base = None
    
    for base_len in range(base_weeks_min, base_weeks_max + 1):
        if len(close) < base_len:
            continue
        
        # Get last N weeks
        base_close = close.tail(base_len)
        base_high = high.tail(base_len)
        base_low = low.tail(base_len)
        base_volume = volume.tail(base_len)
        
        if len(base_close) < base_weeks_min:
            continue
        
        # Check range
        h = float(base_high.max())
        l = float(base_low.min())
        range_pct = (h - l) / l

        if range_pct > max_range_pct:
            continue

        # Convergence: second-half range must be tighter than first-half
        half = max(1, base_len // 2)
        fh_range = (float(base_high.iloc[:half].max()) - float(base_low.iloc[:half].min())) / float(base_low.iloc[:half].min())
        sh_range = (float(base_high.iloc[half:].max()) - float(base_low.iloc[half:].min())) / float(base_low.iloc[half:].min())
        if sh_range >= fh_range:
            continue

        # Check volume contraction
        vol_first_half = base_volume.iloc[:len(base_volume)//2].mean()
        vol_second_half = base_volume.iloc[len(base_volume)//2:].mean()
        
        if vol_first_half == 0:
            continue
        
        vol_contraction = vol_second_half / vol_first_half
        
        if vol_contraction > min_volume_contraction:
            continue
        
        # This base qualifies!
        if best_base is None or base_len > best_base["weeks"]:
            best_base = {
                "weeks": base_len,
                "range_pct": range_pct,
                "vol_contraction": vol_contraction,
                "pivot": h,
            }
    
    if best_base is None:
        return result

    # -----------------------------------------------------------------------
    # 3) Post-Base Signals
    # -----------------------------------------------------------------------
    pivot = best_base["pivot"]
    base_vol_avg = float(volume.tail(best_base["weeks"]).mean())

    # Volume dryness: last bar < 70% of base avg
    vol_dry = (float(volume.iloc[-1]) < 0.70 * base_vol_avg) if base_vol_avg > 0 else False

    # Pivot proximity: close within pivot_proximity_pct below/above pivot
    near_pivot = (
        last_close >= pivot * (1.0 - pivot_proximity_pct)
        and last_close <= pivot * (1.0 + pivot_proximity_pct)
    )

    # Breakout-volume entry: max of last 3 bars >= breakout_volume_factor × base avg
    recent_vol_max = float(volume.tail(3).max())
    launchpad_entry = (
        near_pivot
        and base_vol_avg > 0
        and recent_vol_max >= breakout_volume_factor * base_vol_avg
    )

    # -----------------------------------------------------------------------
    # 4) Build Result
    # -----------------------------------------------------------------------
    result = {
        "Launchpad": True,
        "Launchpad_Entry": launchpad_entry,
        "Base_Weeks": best_base["weeks"],
        "Range_Pct": best_base["range_pct"] * 100.0,
        "Volume_Contraction": best_base["vol_contraction"],
        "Volume_Dry": vol_dry,
        "Pivot_Level": pivot,
        "Near_Pivot": near_pivot,
        "Above_MA50": above_ma50,
        "Prior_Trend": prior_trend,
    }

    return result


def compute_launchpad_score(launchpad_result: dict) -> float:
    """
    Compute quality score (0-100) for a Launchpad pattern.

    Scoring:
    - Prior trend (MA20>MA50, MA50 rising): max 20 pts
    - Tighter range:                        max 20 pts
    - Volume contraction:                   max 20 pts
    - Near pivot (within 3%):               max 15 pts
    - Volume dry (last bar < 70% avg):      max 15 pts
    - Base duration (3-5 weeks):            max 10 pts
    """
    if not launchpad_result.get("Launchpad", False):
        return 0.0

    score = 0.0

    # Prior trend
    if launchpad_result.get("Prior_Trend", False):
        score += 20

    # Range scoring (tighter = better)
    range_pct = launchpad_result.get("Range_Pct", 100)
    if range_pct < 4:
        score += 20
    elif range_pct < 6:
        score += 15
    elif range_pct < 8:
        score += 10
    else:
        score += 5

    # Volume contraction (more = better)
    vol_contraction = launchpad_result.get("Volume_Contraction", 1.0)
    if vol_contraction < 0.40:
        score += 20
    elif vol_contraction < 0.55:
        score += 15
    elif vol_contraction < 0.70:
        score += 10
    else:
        score += 5

    # Near pivot
    if launchpad_result.get("Near_Pivot", False):
        score += 15

    # Volume dry
    if launchpad_result.get("Volume_Dry", False):
        score += 15

    # Base duration (4-5 weeks sweet spot)
    base_weeks = launchpad_result.get("Base_Weeks", 0)
    if 4 <= base_weeks <= 5:
        score += 10
    elif base_weeks == 3:
        score += 7
    else:
        score += 3

    return min(score, 100.0)


# Example usage
if __name__ == "__main__":
    import yfinance as yf
    
    # Test with a ticker
    ticker = "NVDA"
    df = yf.download(ticker, period="1y", interval="1wk", progress=False)
    
    if not df.empty:
        result = detect_launchpad(df)
        score = compute_launchpad_score(result)
        
        print(f"\n{ticker} Launchpad Analysis:")
        print(f"{'='*50}")
        print(f"Launchpad Detected: {result['Launchpad']}")
        if result['Launchpad']:
            print(f"Base Duration: {result['Base_Weeks']} weeks")
            print(f"Range: {result['Range_Pct']:.2f}%")
            print(f"Volume Contraction: {result['Volume_Contraction']:.2f}")
            print(f"Pivot Level: ${result['Pivot_Level']:.2f}")
            print(f"Above MA50: {result['Above_MA50']}")
            print(f"\nQuality Score: {score:.0f}/100")
