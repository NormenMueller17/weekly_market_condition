"""Thin Alpaca Paper-Trading client.

Fetches account summary and open positions for dynamic position sizing and portfolio reporting.

Requires env vars ALPACA_API_KEY and ALPACA_API_SECRET.
Falls back gracefully (returns None / []) when keys are missing or
the API is unreachable, so the screener can run without Alpaca.
"""

import os
from typing import Optional

_PAPER_BASE_URL = "https://paper-api.alpaca.markets"


def _get_trading_client():
    key    = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        return None
    try:
        from alpaca.trading.client import TradingClient
        return TradingClient(key, secret, paper=True)
    except Exception:
        return None


def get_portfolio() -> Optional[dict]:
    """Return account summary and full position details, or None on failure.

    Returns a dict with:
      cash          – non-marginable buying power (actual cash, no leverage)
      equity        – total account equity
      unrealized_pl – sum of unrealized P&L across all positions
      positions     – list of dicts with keys:
                        symbol, qty, avg_entry_price, current_price,
                        unrealized_pl, unrealized_plpc (%), market_value
    """
    client = _get_trading_client()
    if client is None:
        return None
    try:
        account   = client.get_account()
        positions = client.get_all_positions()

        pos_list = []
        for p in positions:
            try:
                plpc = float(p.unrealized_plpc) * 100  # SDK returns decimal, convert to %
            except Exception:
                plpc = 0.0
            pos_list.append({
                "symbol":          p.symbol,
                "qty":             float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price":   float(p.current_price),
                "unrealized_pl":   float(p.unrealized_pl),
                "unrealized_plpc": plpc,
                "market_value":    float(p.market_value),
            })

        return {
            "cash":          float(account.non_marginable_buying_power),
            "equity":        float(account.equity),
            "unrealized_pl": sum(p["unrealized_pl"] for p in pos_list),
            "positions":     pos_list,
        }
    except Exception:
        return None


def available_cash() -> Optional[float]:
    """Return non-marginable buying power (actual cash, no leverage), or None on failure."""
    portfolio = get_portfolio()
    return portfolio["cash"] if portfolio is not None else None


def open_position_tickers() -> list[str]:
    """Return list of ticker symbols currently held, or [] on failure."""
    portfolio = get_portfolio()
    if portfolio is None:
        return []
    return [p["symbol"] for p in portfolio["positions"]]
