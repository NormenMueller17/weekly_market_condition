"""
Blueprint-based trade signal generator.

Buy criteria (Financial Wisdom Blueprint by FinancialWisdomTV):
  1. All 8 Minervini criteria met  (score == 8)
  2. Pattern detected              (VCP Entry=True  OR  Launchpad=True)
  3. ATR/Price < 8 %               (NATR threshold, Blueprint requirement)
  4. ROE  >= min_roe               (double-digit quality)
  5. Operating Margin >= min_op_margin
  6. Revenue growth  >= min_rev_growth
  7. Industry Ranking <= max_industry_rank  (only leading sectors)
  8. Market filter: S&P 500 10W EMA > 20W EMA

Per candidate the generator computes:
  - Entry price      (current weekly close)
  - Stop-loss level  (lower end of the middle third of the consolidation box)
  - Position size    (fractional Kelly criterion)
  - Risk per trade   (€/$ at risk + % of total equity)
  - Composite rank   (RS momentum + pattern quality + tightness + industry)
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd


# ── Default buy-rule thresholds ───────────────────────────────────────────────
# All values can be overridden at call-time via the `rules` dict.

DEFAULT_RULES: dict = {
    "min_score":          8,     # all 8 Minervini criteria
    "require_pattern":    True,  # VCP Entry OR Launchpad
    "max_atr_pct":        8.0,   # NATR < 8  (Blueprint)
    "min_roe":            10.0,  # double-digit ROE
    "min_op_margin":      5.0,   # meaningful operating margin
    "min_rev_growth":     0.0,   # any positive revenue growth
    "max_industry_rank":  50,    # only top-50 ranked industries
                                 # (lower rank number = stronger industry)
}

# ── Ranking weights (must sum to 1.0) ────────────────────────────────────────
RANK_WEIGHTS = {
    "rs_score":      0.35,   # relative strength vs. universe (most important)
    "rs_delta":      0.20,   # RS acceleration over last 4 weeks
    "pattern":       0.20,   # setup quality: VCP+Launchpad > Launchpad > VCP
    "tightness":     0.15,   # tighter stop = better risk/reward
    "industry":      0.10,   # industry tailwind (composite score)
}


# ── Market filter ─────────────────────────────────────────────────────────────

def is_market_bullish(spy_df: pd.DataFrame | None) -> bool:
    """Return True when S&P 500 10W EMA is above 20W EMA (Blueprint filter).

    Fails open (returns True) when data is unavailable so signals are never
    silently suppressed by a data glitch.
    """
    if spy_df is None or (hasattr(spy_df, "empty") and spy_df.empty):
        return True
    try:
        close = spy_df["Close"].squeeze().dropna()
    except (KeyError, AttributeError):
        return True
    if len(close) < 20:
        return True
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    return bool(ema10.iloc[-1] > ema20.iloc[-1])


# ── Position sizing (Fractional Kelly) ───────────────────────────────────────

def _fractional_kelly(win_rate: float, win_loss_ratio: float, fraction: float) -> float:
    """Return fractional Kelly position size as a decimal (e.g. 0.16 = 16 %).

    Formula:  f = (p*b - q) / b  ,  then multiply by fraction (e.g. 1/3).

    With Blueprint defaults (win_rate=0.59, win_loss_ratio=4.04, fraction=0.33)
    the result is ≈ 0.161  →  16 % of equity per position.
    """
    p = win_rate
    q = 1.0 - p
    b = win_loss_ratio
    kelly_full = (p * b - q) / b
    return round(max(0.0, kelly_full * fraction), 4)


# ── Stop-loss helpers ─────────────────────────────────────────────────────────

def _stop_launchpad(pivot: float, range_pct: float) -> Optional[float]:
    """Lower end of the middle third of a Launchpad consolidation box.

    The box spans:  base_low  →  pivot  (= breakout resistance)

        base_low  =  pivot / (1 + range_pct / 100)
        box_range =  pivot - base_low
        stop      =  base_low + box_range / 3   ← lower of middle third

    Blueprint: "The middle portion is where we place the stop
                (usually the lower of that portion)."
    """
    try:
        base_low  = pivot / (1.0 + range_pct / 100.0)
        box_range = pivot - base_low
        return base_low + box_range / 3.0
    except (TypeError, ZeroDivisionError, ValueError):
        return None


def _stop_vcp(breakout_level: float, atr_pct: float) -> Optional[float]:
    """2× ATR below the VCP breakout level — proxy for middle-third stop.

    Hard floor: never more than 15 % below breakout level (Blueprint cap).
    """
    try:
        stop  = breakout_level * (1.0 - 2.0 * atr_pct / 100.0)
        floor = breakout_level * 0.85
        return max(stop, floor)
    except (TypeError, ZeroDivisionError, ValueError):
        return None


def _stop_default(entry: float, pct: float = 0.10) -> float:
    """Fallback: fixed percentage below entry (Blueprint average ≈ 10 %)."""
    return entry * (1.0 - pct)


# ── Composite ranking ─────────────────────────────────────────────────────────

_PATTERN_SCORES = {
    "VCP+Launchpad": 100.0,   # dual confirmation → highest confidence
    "Launchpad":      70.0,   # tight base, volume contraction
    "VCP":            50.0,   # valid but broader base
}

def _composite_score(sig: "TradeSignal") -> float:
    """Compute a 0-100 composite score for ranking trade signals.

    Components
    ----------
    rs_score  (35 %) : O'Neil RS percentile 0–99, normalised to 0–100.
    rs_delta  (20 %) : 4-week RS change, clamped [-15, +15] → 0–100.
                       Rising RS = fresh institutional buying.
    pattern   (20 %) : Setup quality (see _PATTERN_SCORES).
    tightness (15 %) : How small the required stop is.
                       stop_pct=5 % → 75 pts;  stop_pct=20 % → 0 pts.
    industry  (10 %) : Composite industry score (already 0–100).
    """
    # 1. RS Score
    rs_norm  = ((sig.rs_score or 0.0) / 99.0) * 100.0

    # 2. ΔRS 4W  (clamp to ±15, map to 0–100)
    delta     = sig.rs_delta_4w or 0.0
    delta_norm = max(0.0, min(100.0, (delta + 15.0) / 30.0 * 100.0))

    # 3. Pattern quality
    pat_score = _PATTERN_SCORES.get(sig.pattern, 0.0)

    # 4. Tightness  (smaller stop → higher score)
    tightness  = max(0.0, (1.0 - sig.stop_loss_pct / 0.20)) * 100.0

    # 5. Industry score (0–100 composite; default 50 when unknown)
    ind_score  = sig.industry_score if sig.industry_score is not None else 50.0

    return (
        rs_norm    * RANK_WEIGHTS["rs_score"]  +
        delta_norm * RANK_WEIGHTS["rs_delta"]  +
        pat_score  * RANK_WEIGHTS["pattern"]   +
        tightness  * RANK_WEIGHTS["tightness"] +
        ind_score  * RANK_WEIGHTS["industry"]
    )


def rank_signals(
    signals:       list["TradeSignal"],
    max_positions: int = 5,
) -> list["TradeSignal"]:
    """Sort signals by composite score (best first) and assign rank / is_top_pick.

    Parameters
    ----------
    signals       : unranked list from generate_signals()
    max_positions : top-N signals are flagged as is_top_pick=True
    """
    ranked = sorted(signals, key=_composite_score, reverse=True)
    for i, sig in enumerate(ranked):
        sig.rank         = i + 1
        sig.is_top_pick  = (i < max_positions)
    return ranked


# ── Trade-signal dataclass ────────────────────────────────────────────────────

@dataclass
class TradeSignal:
    ticker:             str
    company:            str
    industry:           str
    sector:             str

    # Entry & stop
    entry_price:        float
    stop_loss:          float
    stop_loss_pct:      float           # e.g. 0.092  →  9.2 % below entry

    # Pattern
    pattern:            str             # "VCP" | "Launchpad" | "VCP+Launchpad"
    breakout_level:     Optional[float]

    # Fundamentals
    roe:                Optional[float]
    op_margin:          Optional[float]
    revenue_growth:     Optional[float]

    # Technical
    rs_score:           Optional[float]
    rs_delta_4w:        Optional[float]
    atr_pct:            Optional[float]
    dist_52w_high_pct:  Optional[float]

    # Position sizing
    position_size_pct:  float           # e.g. 0.161  →  16.1 %
    position_value:     float           # absolute amount in account currency
    risk_value:         float           # amount at risk per trade
    risk_on_equity_pct: float           # e.g. 0.015  →  1.5 % of total equity

    # Industry (used for ranking)
    industry_ranking:   Optional[int]   = None   # lower = stronger industry
    industry_score:     Optional[float] = None   # 0–100 composite

    # Ranking (filled by rank_signals())
    rank:               int             = 0
    is_top_pick:        bool            = False

    # Meta
    sa_link:            str  = ""
    signal_date:        str  = field(default_factory=lambda: date.today().isoformat())


# ── Main generator ────────────────────────────────────────────────────────────

def generate_signals(
    leaders:         pd.DataFrame,
    market_bullish:  bool  = True,
    account_equity:  float = 100_000.0,
    win_rate:        float = 0.59,
    win_loss_ratio:  float = 4.04,
    kelly_fraction:  float = 0.33,
    max_positions:   int   = 5,
    rules:           dict  | None = None,
) -> tuple[list[TradeSignal], pd.DataFrame]:
    """Apply Blueprint buy rules to the leaders DataFrame.

    Returns
    -------
    signals    : list[TradeSignal]   — ranked buy candidates with sizing
                                       (best first; is_top_pick=True for top N)
    candidates : pd.DataFrame        — filtered rows (for email / audit)
    """
    r            = {**DEFAULT_RULES, **(rules or {})}
    pos_size_pct = _fractional_kelly(win_rate, win_loss_ratio, kelly_fraction)

    # Market filter — no signals in a bearish market environment
    if not market_bullish:
        return [], pd.DataFrame()

    df = leaders.copy()

    # ── Filters ───────────────────────────────────────────────────────────────

    def _num(col: str, fill: float = -999.0) -> pd.Series:
        return pd.to_numeric(
            df.get(col, pd.Series(fill, index=df.index)), errors="coerce"
        ).fillna(fill)

    mask = pd.Series(True, index=df.index)

    # 1. All 8 Minervini criteria
    mask &= _num("score", 0) >= r["min_score"]

    # 2. Pattern: VCP Entry OR Launchpad
    if r["require_pattern"]:
        vcp_entry = df.get("VCP Entry",  pd.Series(False, index=df.index)).fillna(False).astype(bool)
        launchpad = df.get("Launchpad",  pd.Series(False, index=df.index)).fillna(False).astype(bool)
        mask &= vcp_entry | launchpad

    # 3. ATR / Price below NATR threshold
    mask &= _num("ATR / Price (%)", 999) < r["max_atr_pct"]

    # 4. Fundamental quality filters
    if r["min_roe"]        > 0:  mask &= _num("ROE (%)")                       >= r["min_roe"]
    if r["min_op_margin"]  > 0:  mask &= _num("Operating Margin (%)")          >= r["min_op_margin"]
    if r["min_rev_growth"] > 0:  mask &= _num("Revenue Wachstum TTM YoY (%)") >= r["min_rev_growth"]

    # 5. Industry Ranking filter  (lower rank number = stronger industry)
    #    NaN industry rank → excluded (unknown industry = no tailwind)
    if r["max_industry_rank"] is not None:
        ind_rank = _num("Industry Ranking", fill=9999)
        mask &= ind_rank <= r["max_industry_rank"]

    candidates = df[mask].copy()

    if candidates.empty:
        return [], candidates

    # ── Build signal objects ──────────────────────────────────────────────────

    signals: list[TradeSignal] = []

    for ticker, row in candidates.iterrows():
        entry = _safe_float(row.get("Close"))
        if entry is None or entry <= 0:
            continue

        atr_pct    = _safe_float(row.get("ATR / Price (%)")) or 5.0
        has_vcp    = bool(row.get("VCP Entry",  False))
        has_launch = bool(row.get("Launchpad",  False))

        # ── Determine pattern label, breakout level, and stop ────────────────
        stop: Optional[float] = None
        bl:   Optional[float] = None

        if has_launch:
            pivot     = _safe_float(row.get("Launchpad Pivot"))
            range_pct = _safe_float(row.get("Launchpad Range (%)"))
            if pivot and range_pct is not None:
                stop = _stop_launchpad(pivot, range_pct)
            bl      = pivot
            pattern = "VCP+Launchpad" if has_vcp else "Launchpad"

        elif has_vcp:
            bl      = _safe_float(row.get("VCP Breakout Level"))
            stop    = _stop_vcp(bl, atr_pct) if bl else None
            pattern = "VCP"

        else:
            pattern = "–"

        if stop is None:
            stop = _stop_default(entry)

        # Blueprint safety cap: stop never more than 20 % below entry
        stop_pct = (entry - stop) / entry
        if stop_pct > 0.20:
            stop     = entry * 0.80
            stop_pct = 0.20

        position_value = account_equity * pos_size_pct
        risk_value     = position_value * stop_pct
        risk_on_equity = risk_value / account_equity

        # Industry data (used for ranking)
        ind_rank  = _safe_int(row.get("Industry Ranking"))
        ind_score = _safe_float(row.get("Industry Score"))

        signals.append(TradeSignal(
            ticker             = str(ticker),
            company            = str(row.get("Company", "")),
            industry           = str(row.get("Industry", "")),
            sector             = str(row.get("Sektor", "")),
            entry_price        = round(entry, 2),
            stop_loss          = round(stop, 2),
            stop_loss_pct      = round(stop_pct, 4),
            pattern            = pattern,
            breakout_level     = round(bl, 2) if bl is not None else None,
            roe                = _safe_float(row.get("ROE (%)")),
            op_margin          = _safe_float(row.get("Operating Margin (%)")),
            revenue_growth     = _safe_float(row.get("Revenue Wachstum TTM YoY (%)")),
            rs_score           = _safe_float(row.get("RS (O'Neil)")),
            rs_delta_4w        = _safe_float(row.get("ΔRS 4W")),
            atr_pct            = round(atr_pct, 2),
            dist_52w_high_pct  = _safe_float(row.get("Dist to 52W High (%)")),
            position_size_pct  = round(pos_size_pct, 4),
            position_value     = round(position_value, 2),
            risk_value         = round(risk_value, 2),
            risk_on_equity_pct = round(risk_on_equity, 4),
            industry_ranking   = ind_rank,
            industry_score     = round(ind_score, 2) if ind_score is not None else None,
            sa_link            = str(row.get("SA", "")),
        ))

    # ── Rank and flag top picks ───────────────────────────────────────────────
    signals = rank_signals(signals, max_positions=max_positions)

    return signals, candidates


# ── Utility ───────────────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


# ── JSON persistence ──────────────────────────────────────────────────────────

def save_signals_json(signals: list[TradeSignal], path: str | Path) -> Path:
    """Serialize signals to a dated JSON file and return the path.

    The file is always written (even when signals==[]) so downstream consumers
    can reliably check the file's `count` field.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": date.today().isoformat(),
        "count":     len(signals),
        "signals":   [asdict(s) for s in signals],
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p
