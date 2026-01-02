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
    max_range_pct: float = 0.08,      # 12% max Range während Base
    min_volume_contraction: float = 0.50,
    require_above_ma50: bool = True,
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
        Maximum consolidation weeks (default: 7)
    max_range_pct : float
        Max allowed range during base (default: 12%)
    min_volume_contraction : float
        Volume must contract by at least this much (0.70 = 30% contraction)
    require_above_ma50 : bool
        Require price above 50-week MA
        
    Returns
    -------
    dict
        {
            "Launchpad": bool,
            "Base_Weeks": int,
            "Range_Pct": float,
            "Volume_Contraction": float,
            "Pivot_Level": float | None,
            "Above_MA50": bool
        }
    """
    
    result = {
        "Launchpad": False,
        "Base_Weeks": 0,
        "Range_Pct": float("nan"),
        "Volume_Contraction": float("nan"),
        "Pivot_Level": None,
        "Above_MA50": False,
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
    # 1) MA Alignment Check
    # -----------------------------------------------------------------------
    ma10 = close.rolling(10).mean()
    ma50 = close.rolling(50).mean()
    
    last_close = float(close.iloc[-1])
    last_ma10 = float(ma10.iloc[-1])
    last_ma50 = float(ma50.iloc[-1])
    
    if not math.isfinite(last_close) or not math.isfinite(last_ma50):
        return result
    
    above_ma10 = last_close > last_ma10
    above_ma50 = last_close > last_ma50
    result["Above_MA50"] = above_ma50
    
    if require_above_ma50 and not above_ma50:
        return result
    
    if not above_ma10:
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
    # 3) Build Result
    # -----------------------------------------------------------------------
    result = {
        "Launchpad": True,
        "Base_Weeks": best_base["weeks"],
        "Range_Pct": best_base["range_pct"] * 100.0,  # as percentage
        "Volume_Contraction": best_base["vol_contraction"],
        "Pivot_Level": best_base["pivot"],
        "Above_MA50": above_ma50,
    }
    
    return result


def compute_launchpad_score(launchpad_result: dict) -> float:
    """
    Compute quality score (0-100) for a Launchpad pattern.
    
    Higher score = Better setup
    
    Scoring:
    - Tighter range = Better (max 30 points)
    - More volume contraction = Better (max 30 points)
    - Longer base (up to 5 weeks) = Better (max 20 points)
    - Above MA50 = +20 points
    """
    if not launchpad_result.get("Launchpad", False):
        return 0.0
    
    score = 0.0
    
    # Range scoring (tighter = better)
    range_pct = launchpad_result.get("Range_Pct", 100)
    if range_pct < 5:
        score += 30
    elif range_pct < 8:
        score += 25
    elif range_pct < 10:
        score += 20
    elif range_pct < 12:
        score += 15
    else:
        score += 10
    
    # Volume contraction (more = better)
    vol_contraction = launchpad_result.get("Volume_Contraction", 1.0)
    if vol_contraction < 0.50:
        score += 30
    elif vol_contraction < 0.60:
        score += 25
    elif vol_contraction < 0.70:
        score += 20
    else:
        score += 10
    
    # Base duration (3-5 weeks sweet spot)
    base_weeks = launchpad_result.get("Base_Weeks", 0)
    if 4 <= base_weeks <= 5:
        score += 20
    elif base_weeks == 3 or base_weeks == 6:
        score += 15
    else:
        score += 10
    
    # Above MA50
    if launchpad_result.get("Above_MA50", False):
        score += 20
    
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
