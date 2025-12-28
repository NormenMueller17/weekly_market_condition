import numpy as np
import pandas as pd

"""Quality-Compounder scoring.

compute_quality_score() returns an integer score 0..100.
The scorer is robust to missing data (row-wise weight re-normalization).

Required columns (best effort):
- Industry
- ROIC (%)
- Cash Conversion (ratio)
- FCF / Net Income (ratio)
- Operating Margin (%)
- Op_Margin_Stability_5y (stddev)
- REV_Neg_YoY_Count_5y (int)
- EPS_Neg_YoY_Count_5y (int)
- ATR / Price (%)
- Max Drawdown 5Y (%)
- Max Drawdown 10Y (%)
"""


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _pct_rank(s: pd.Series) -> pd.Series:
    """Percentile rank 0..100; NaN stays NaN."""
    x = _to_num(s)
    return x.rank(pct=True) * 100.0


def _pct_rank_by_group(df: pd.DataFrame, value_col: str, group_col: str) -> pd.Series:
    x = _to_num(df[value_col])
    if group_col not in df.columns:
        return _pct_rank(x)
    return df.groupby(group_col)[value_col].transform(lambda s: _pct_rank(s))


def _invert(score_0_100: pd.Series) -> pd.Series:
    return 100.0 - score_0_100


def _discrete_neg_years_score(neg_years: pd.Series) -> pd.Series:
    """0->100, 1->75, 2->50, >=3->0."""
    n = _to_num(neg_years)
    out = pd.Series(np.nan, index=n.index, dtype=float)
    out[n == 0] = 100.0
    out[n == 1] = 75.0
    out[n == 2] = 50.0
    out[n >= 3] = 0.0
    return out


def compute_quality_score(
    df: pd.DataFrame,
    *,
    industry_col: str = "Industry",
    rounding: str = "round",
) -> pd.Series:
    """Compute Quality Score for each row of df.

    Returns:
        pd.Series of dtype Int64 (nullable integer), range 0..100.
    """
    required = [
        industry_col,
        "ROIC (%)",
        "Cash Conversion",
        "FCF / Net Income",
        "Operating Margin (%)",
        "Op_Margin_Stability_5y",
        "REV_Neg_YoY_Count_5y",
        "EPS_Neg_YoY_Count_5y",
        "ATR / Price (%)",
        "Max Drawdown 5Y (%)",
        "Max Drawdown 10Y (%)",
    ]

    # Work on a copy with missing columns added as NaN
    d = df.copy()
    for c in required:
        if c not in d.columns:
            d[c] = np.nan

    g = industry_col

    # --- Percentile-based components (0..100) ---
    roic_p = _pct_rank_by_group(d, "ROIC (%)", g)
    # NOPAT/InvestedCapital proxy (same information as ROIC, but kept for weight parity)
    nopat_ic_p = roic_p

    cash_conv_p = _pct_rank_by_group(d, "Cash Conversion", g)
    fcf_ni_p = _pct_rank_by_group(d, "FCF / Net Income", g)

    op_margin_p = _pct_rank_by_group(d, "Operating Margin (%)", g)
    op_stability_p = _invert(_pct_rank_by_group(d, "Op_Margin_Stability_5y", g))

    atr_p = _invert(_pct_rank(d["ATR / Price (%)"]))

    mdd5_p = _invert(_pct_rank(d["Max Drawdown 5Y (%)"]))
    mdd10_p = _invert(_pct_rank(d["Max Drawdown 10Y (%)"]))

    # --- Discrete components ---
    rev_cons = _discrete_neg_years_score(d["REV_Neg_YoY_Count_5y"])
    eps_cons = _discrete_neg_years_score(d["EPS_Neg_YoY_Count_5y"])

    # Assemble component table
    comps = pd.DataFrame(
        {
            "roic": roic_p,
            "nopat_ic": nopat_ic_p,
            "cash_conv": cash_conv_p,
            "fcf_ni": fcf_ni_p,
            "op_margin": op_margin_p,
            "op_stability": op_stability_p,
            "atr": atr_p,
            "rev_cons": rev_cons,
            "eps_cons": eps_cons,
            "mdd5": mdd5_p,
            "mdd10": mdd10_p,
        },
        index=d.index,
    )

    weights = {
        "roic": 20.0,
        "nopat_ic": 10.0,
        "cash_conv": 10.0,
        "fcf_ni": 10.0,
        "op_margin": 10.0,
        "op_stability": 10.0,
        "atr": 5.0,
        "rev_cons": 7.0,
        "eps_cons": 8.0,
        "mdd5": 5.0,
        "mdd10": 5.0,
    }

    w = pd.Series(weights)

    # Row-wise weight renormalization (ignore NaN components)
    valid_mask = comps.notna()
    denom = (valid_mask * w).sum(axis=1)

    # Weighted average (components are already scaled 0..100)
    weighted = (comps.mul(w, axis=1)).sum(axis=1)
    score = weighted / denom

    # If denom is 0 (all missing) -> NaN
    score = score.where(denom > 0)

    # Clamp
    score = score.clip(lower=0.0, upper=100.0)

    if rounding == "floor":
        score_int = np.floor(score)
    elif rounding == "ceil":
        score_int = np.ceil(score)
    else:
        score_int = np.round(score)

    return pd.Series(score_int, index=df.index).astype("Int64")
