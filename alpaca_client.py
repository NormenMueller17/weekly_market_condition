"""Thin Alpaca Paper-Trading client.

Fetches two things needed for dynamic position sizing:
  - available_cash()     → float  (buying power)
  - open_position_tickers() → list[str]  (tickers currently held)

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


def available_cash() -> Optional[float]:
    """Return paper-account buying power, or None on failure."""
    client = _get_trading_client()
    if client is None:
        return None
    try:
        account = client.get_account()
        return float(account.buying_power)
    except Exception:
        return None


def open_position_tickers() -> list[str]:
    """Return list of ticker symbols currently held, or [] on failure."""
    client = _get_trading_client()
    if client is None:
        return []
    try:
        positions = client.get_all_positions()
        return [p.symbol for p in positions]
    except Exception:
        return []
