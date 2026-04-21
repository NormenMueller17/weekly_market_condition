"""
Blueprint-based trade signal generator.

Buy criteria (Financial Wisdom Blueprint by FinancialWisdomTV):
  1. Minervini score >= 6           (at least 6 of 8 criteria, per actual SEPA practice)
  2. RS Score >= 70                 (top third — institutional sponsorship)
  3. Distance to 52W High <= 25 %  (within striking distance of highs)
  4. ATR/Price < 8 %               (NATR threshold, Blueprint requirement)
  5. ROE  >= min_roe               (0 = disabled; quality captured by score)
  6. Operating Margin >= min_op_margin  (0 = disabled)
  7. Revenue growth  >= min_rev_growth
  8. Industry Ranking <= max_industry_rank  (only leading sectors)
  9. Market filter: S&P 500 10W EMA > 20W EMA
  Pattern (VCP/Launchpad) is a ranking bonus — not a hard requirement.

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
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd


def _filter_earnings_blackout(tickers: list[str], window_days: int = 7) -> list[str]:
    """Return tickers whose next earnings date is more than window_days away (or unknown).

    Stocks with earnings within window_days are excluded to avoid gap risk.
    Falls back to including the ticker if the earnings date cannot be determined.
    """
    import yfinance as yf
    cutoff = date.today() + timedelta(days=window_days)
    safe: list[str] = []
    for ticker in tickers:
        try:
            cal = yf.Ticker(ticker).calendar
            # calendar is a dict with key "Earnings Date" → list of dates, or a DataFrame
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
                if not dates:
                    safe.append(ticker)
                    continue
                next_earnings = min(
                    (d.date() if hasattr(d, "date") else d for d in dates),
                    default=None,
                )
            elif hasattr(cal, "empty") and not cal.empty:
                # older yfinance versions return a DataFrame
                row = cal.T.get("Earnings Date")
                next_earnings = row.iloc[0].date() if row is not None and len(row) else None
            else:
                safe.append(ticker)
                continue

            if next_earnings is None or next_earnings > cutoff:
                safe.append(ticker)
            else:
                print(f"[EARNINGS] {ticker}: Earnings {next_earnings} ≤ {window_days}d → ausgeschlossen")
        except Exception:
            safe.append(ticker)  # fail-open: lieber zu viele als zu wenige Kandidaten
    return safe


# ── Load rules.json (next to this file) ──────────────────────────────────────
# rules.json is the single source of truth for all thresholds and weights.
# If the file is missing, hardcoded fallbacks below are used automatically.

_RULES_PATH = Path(__file__).parent / "rules.json"

def _load_rules_json() -> dict:
    """Load and parse rules.json; return empty dict on any error."""
    try:
        return json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

_RULES_JSON = _load_rules_json()

# ── Default buy-rule thresholds (fallback if rules.json is missing) ───────────
_f = _RULES_JSON.get("filters", {})
DEFAULT_RULES: dict = {
    "min_score":              _f.get("min_score",              6),
    "require_pattern":        _f.get("require_pattern",        False),
    "max_atr_pct":            _f.get("max_atr_pct",            8.0),
    "min_rs_score":           _f.get("min_rs_score",           70.0),
    "max_dist_52w_high_pct":  _f.get("max_dist_52w_high_pct",  25.0),
    "min_roe":                _f.get("min_roe",                0.0),
    "min_op_margin":          _f.get("min_op_margin",          0.0),
    "min_rev_growth":         _f.get("min_rev_growth",         0.0),
    "min_eps_growth_last_q":  _f.get("min_eps_growth_last_q",  0.0),
    "max_industry_rank":      _f.get("max_industry_rank",      100),
    "max_stop_pct":           _f.get("max_stop_pct",           20.0),
    "earnings_blackout_days": _f.get("earnings_blackout_days", 7),
    "min_price":              _f.get("min_price",              5.0),
    "min_market_cap_mio":     _f.get("min_market_cap_mio",    300.0),
    "buy_stop_buffer_pct":    _f.get("buy_stop_buffer_pct",   0.1),
    "gap_limit_pct":          _f.get("gap_limit_pct",         5.0),
    "require_macd_above_signal": _f.get("require_macd_above_signal", True),
}

# ── Ranking weights (must sum to 1.0) ─────────────────────────────────────────
_w = _RULES_JSON.get("ranking_weights", {})
RANK_WEIGHTS = {
    "rs_score":  _w.get("rs_score",  0.35),
    "rs_delta":  _w.get("rs_delta",  0.20),
    "pattern":   _w.get("pattern",   0.20),
    "tightness": _w.get("tightness", 0.15),
    "industry":  _w.get("industry",  0.10),
}

# ── Portfolio / sizing defaults from rules.json ───────────────────────────────
_p = _RULES_JSON.get("portfolio", {})
_s = _RULES_JSON.get("sizing", {})
_DEFAULT_MAX_POSITIONS          = _p.get("max_positions",          5)
_DEFAULT_ACCOUNT_EQUITY         = _p.get("account_equity",          100_000.0)
_DEFAULT_WIN_RATE               = _s.get("win_rate",                0.59)
_DEFAULT_WIN_LOSS_RATIO         = _s.get("win_loss_ratio",          4.04)
_DEFAULT_KELLY_FRACTION         = _s.get("kelly_fraction",          0.33)
_DEFAULT_BEARISH_KELLY_FRACTION = _s.get("bearish_kelly_fraction",  0.5)


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


def _filter_sector_limit(
    signals:        list["TradeSignal"],
    max_per_sector: int,
) -> tuple[list["TradeSignal"], set[str]]:
    """Drop lower-ranked signals that exceed the per-sector cap (preserves rank order).

    Returns (kept_signals, dropped_tickers).
    """
    sector_count: dict[str, int] = {}
    kept:    list["TradeSignal"] = []
    dropped: set[str]            = set()
    for sig in signals:
        sector = sig.sector or "Unknown"
        count  = sector_count.get(sector, 0)
        if count < max_per_sector:
            kept.append(sig)
            sector_count[sector] = count + 1
        else:
            dropped.add(sig.ticker)
    return kept, dropped


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
    buy_stop:           float           # Buy-Stop-Order: max(entry, breakout_level) * (1 + buffer%)
    max_gap_price:      float           # Order verwerfen wenn Montag-Open > dieser Preis
    stop_loss:          float
    stop_loss_pct:      float           # e.g. 0.092  →  9.2 % below entry

    # Pattern
    pattern:            str             # "VCP" | "Launchpad" | "VCP+Launchpad"
    breakout_level:     Optional[float]

    # Fundamentals
    roe:                Optional[float]
    op_margin:          Optional[float]
    revenue_growth:     Optional[float]
    eps_growth_last_q:  Optional[float]

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
    market_regime:      str  = "bullish"   # "bullish" | "bearish" — regime when signal was generated
    sa_link:            str  = ""
    signal_date:        str  = field(default_factory=lambda: date.today().isoformat())


# ── Main generator ────────────────────────────────────────────────────────────

def generate_signals(
    leaders:                 pd.DataFrame,
    market_bullish:          bool        = True,
    account_equity:          float       = _DEFAULT_ACCOUNT_EQUITY,
    win_rate:                float       = _DEFAULT_WIN_RATE,
    win_loss_ratio:          float       = _DEFAULT_WIN_LOSS_RATIO,
    kelly_fraction:          float       = _DEFAULT_KELLY_FRACTION,
    bearish_kelly_fraction:  float       = _DEFAULT_BEARISH_KELLY_FRACTION,
    max_positions:           int         = _DEFAULT_MAX_POSITIONS,
    rules:                   dict | None = None,
    available_cash:          float | None = None,
    open_positions:          list[str] | None = None,
) -> tuple[list[TradeSignal], pd.DataFrame]:
    """Apply Blueprint buy rules to the leaders DataFrame.

    Returns
    -------
    signals    : list[TradeSignal]   — ranked buy candidates with sizing
                                       (best first; is_top_pick=True for top N)
    candidates : pd.DataFrame        — filtered rows (for email / audit)
    """
    r             = {**DEFAULT_RULES, **(rules or {})}
    market_regime = "bullish" if market_bullish else "bearish"

    # Regime-based Kelly: halve position size in bearish markets instead of blocking entirely.
    # This keeps us in the game for early-recovery breakouts while cutting risk exposure.
    effective_kelly = kelly_fraction if market_bullish else kelly_fraction * bearish_kelly_fraction
    pos_size_pct    = _fractional_kelly(win_rate, win_loss_ratio, effective_kelly)

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

    # 3. RS Score — top-third relative strength (institutional sponsorship)
    if r.get("min_rs_score", 0) > 0:
        mask &= _num("RS (O'Neil)", 0) >= r["min_rs_score"]

    # 4. Distance to 52W High — must still be in striking range of highs
    #    Column stores the distance as a positive percentage (e.g. 15.0 = 15 % below high)
    if r.get("max_dist_52w_high_pct") is not None:
        mask &= _num("Dist to 52W High (%)", 999) <= r["max_dist_52w_high_pct"]

    # 5. ATR / Price below NATR threshold
    mask &= _num("ATR / Price (%)", 999) < r["max_atr_pct"]

    # 6. Fundamental quality filters
    if r["min_roe"]             > 0:  mask &= _num("ROE (%)")                              >= r["min_roe"]
    if r["min_op_margin"]       > 0:  mask &= _num("Operating Margin (%)")                 >= r["min_op_margin"]
    if r["min_rev_growth"]      > 0:  mask &= _num("Revenue Wachstum TTM YoY (%)")        >= r["min_rev_growth"]
    if r["min_eps_growth_last_q"] > 0: mask &= _num("EPS Wachstum letztes Q YoY (%)") >= r["min_eps_growth_last_q"]

    # 7. Industry Ranking filter  (lower rank number = stronger industry)
    #    NaN industry rank → pass (fail-open: computation failure ≠ bad industry)
    if r.get("max_industry_rank") is not None and r["max_industry_rank"] > 0:
        ind_rank_raw = pd.to_numeric(
            df.get("Industry Ranking", pd.Series(index=df.index)), errors="coerce"
        )
        mask &= ind_rank_raw.isna() | (ind_rank_raw <= r["max_industry_rank"])

    # 8. MACD > Signal (weekly) — only buy into rising momentum, not falling
    if r.get("require_macd_above_signal", True):
        macd_ok = df.get("MACD > Signal (W)", pd.Series(False, index=df.index)).fillna(False).astype(bool)
        mask &= macd_ok

    # 9. Volume Breakout mandatory — only stocks with confirmed volume surge qualify
    vol_breakout_col = df.get("Vol-Breakout", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    mask &= vol_breakout_col

    # 9. Minimum price — no penny stocks (fail-open: NaN Close = data gap, not penny stock)
    if r.get("min_price", 0) > 0:
        price_raw = pd.to_numeric(
            df.get("Close", pd.Series(index=df.index)), errors="coerce"
        )
        mask &= price_raw.isna() | (price_raw >= r["min_price"])

    # 10. Minimum market cap — no micro caps (fail-open: NaN MCap = data gap)
    if r.get("min_market_cap_mio", 0) > 0:
        cap_raw = pd.to_numeric(
            df.get("MarketCap (Mio USD)", pd.Series(index=df.index)), errors="coerce"
        )
        mask &= cap_raw.isna() | (cap_raw >= r["min_market_cap_mio"])

    candidates = df[mask].copy()

    if candidates.empty:
        return [], candidates, []

    # 11. Earnings-Blackout: kein Kauf wenn Earnings ≤ 7 Tage entfernt
    earnings_window = r.get("earnings_blackout_days", 7)
    if earnings_window > 0:
        no_earnings = _filter_earnings_blackout(list(candidates.index), earnings_window)
        candidates = candidates[candidates.index.isin(no_earnings)]
        if candidates.empty:
            return [], candidates, []

    # ── Portfolio-aware filtering ─────────────────────────────────────────────
    held = set(open_positions or [])
    if held:
        candidates = candidates[~candidates.index.isin(held)]

    remaining_slots = max_positions - len(held)
    if remaining_slots <= 0:
        return [], candidates, []

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
            # Stop never above the trigger candle's wick low
            week_low = _safe_float(row.get("Week Low"))
            if stop is not None and week_low and week_low < stop:
                stop = week_low
            elif stop is None and week_low:
                stop = week_low
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

        # Floor: stop must be at least 1× ATR below entry (avoids sub-1% stops
        # on tight Launchpad bases where the formula gives no real room)
        atr_floor = entry * (1.0 - atr_pct / 100.0)
        if stop > atr_floor:
            stop = atr_floor

        # Blueprint safety cap: stop never more than 20 % below entry
        stop_pct = (entry - stop) / entry
        if stop_pct > 0.20:
            stop     = entry * 0.80
            stop_pct = 0.20

        # Buy-Stop: 0.1% über dem höheren von entry und breakout_level
        buf = 1.0 + r.get("buy_stop_buffer_pct", 0.1) / 100.0
        pivot = bl if bl else entry
        buy_stop = round(max(entry, pivot) * buf, 2)

        # Max. Gap: Order verwerfen wenn Montag-Open mehr als gap_limit_pct% über Pivot
        gap_limit = 1.0 + r.get("gap_limit_pct", 5.0) / 100.0
        max_gap_price = round(pivot * gap_limit, 2)

        # Risk-first sizing: position = max_risk / stop_pct, capped at max_position_pct
        max_risk_pct  = r.get("max_risk_per_trade_pct", 1.5) / 100.0
        max_pos_pct   = r.get("max_position_pct",       15.0) / 100.0
        # Bearish regime: halve risk exposure
        if not market_bullish:
            max_risk_pct *= r.get("bearish_kelly_fraction", 0.5)
        risk_based_pct = max_risk_pct / stop_pct if stop_pct > 0 else max_pos_pct
        pos_size_pct   = min(risk_based_pct, max_pos_pct)
        risk_value     = account_equity * max_risk_pct  # always exact target risk
        position_value = account_equity * pos_size_pct
        if available_cash is not None:
            cash_per_slot  = available_cash / max(1, remaining_slots)
            position_value = min(position_value, cash_per_slot)
            risk_value     = position_value * stop_pct
        risk_on_equity = risk_value / account_equity

        # Industry data (used for ranking)
        ind_rank  = _safe_int(row.get("Industry Ranking"))
        ind_score = _safe_float(row.get("Industry Score"))

        signals.append(TradeSignal(
            ticker             = str(ticker),
            company            = str(row.get("Company", "")),
            industry           = str(row.get("Industry", "") or ""),
            sector             = str(row.get("Sektor", "") or ""),
            entry_price        = round(entry, 2),
            buy_stop           = buy_stop,
            max_gap_price      = max_gap_price,
            stop_loss          = round(stop, 2),
            stop_loss_pct      = round(stop_pct, 4),
            pattern            = pattern,
            breakout_level     = round(bl, 2) if bl is not None else None,
            roe                = _safe_float(row.get("ROE (%)")),
            op_margin          = _safe_float(row.get("Operating Margin (%)")),
            revenue_growth     = _safe_float(row.get("Revenue Wachstum TTM YoY (%)")),
            eps_growth_last_q  = _safe_float(row.get("EPS Wachstum letztes Q YoY (%)")),
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
            market_regime      = market_regime,
            sa_link            = str(row.get("SA", "")),
        ))

    # ── Rank and flag top picks ───────────────────────────────────────────────
    signals = rank_signals(signals, max_positions=remaining_slots)

    # ── Sector concentration limit ────────────────────────────────────────────
    max_per_sector = _RULES_JSON.get("portfolio", {}).get("max_positions_per_sector", 3)
    sector_excluded: set[str] = set()
    if max_per_sector > 0:
        signals, sector_excluded = _filter_sector_limit(signals, max_per_sector=max_per_sector)

    return signals, candidates, sector_excluded


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
