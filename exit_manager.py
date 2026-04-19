"""
MACD Bearish Cross Exit Manager.

Blueprint exit rule (weekly):
  1. MACD line crosses below Signal line on a completed weekly candle
  2. Raised stop = Low (wick) of the trigger week candle
  3. Alpaca stop order for that position is updated to the raised stop
     (only raised, never lowered — Alpaca executes the sell automatically
     if price drops below the new stop in any subsequent week)
"""

import os
import yfinance as yf
import pandas as pd
from typing import Optional


# ── MACD helpers ─────────────────────────────────────────────────────────────

def _macd_series(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast  = close.ewm(span=fast,   adjust=False).mean()
    ema_slow  = close.ewm(span=slow,   adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line  = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, sig_line


def check_macd_bearish_cross(ticker: str) -> dict:
    """Return MACD state for the most recently completed weekly candle.

    Returns:
      cross      – True if MACD crossed below Signal last week
      week_low   – Low of the trigger candle (= raised stop level), or None
      macd       – current MACD value (float)
      signal_val – current Signal value (float)
    """
    result = {"cross": False, "week_low": None, "macd": None, "signal_val": None}
    try:
        hist = yf.download(
            ticker, period="2y", interval="1wk",
            progress=False, auto_adjust=True,
        )
        if hist is None or hist.empty or len(hist) < 35:
            return result

        # Flatten MultiIndex columns that yfinance sometimes returns
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)

        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        low   = pd.to_numeric(hist["Low"],   errors="coerce")

        macd_line, sig_line = _macd_series(close)
        aligned = pd.concat(
            [macd_line.rename("macd"), sig_line.rename("sig")], axis=1
        ).dropna()

        if len(aligned) < 2:
            return result

        prev = aligned.iloc[-2]
        cur  = aligned.iloc[-1]

        bearish_cross = (prev["macd"] >= prev["sig"]) and (cur["macd"] < cur["sig"])
        result["cross"]      = bearish_cross
        result["macd"]       = round(float(cur["macd"]), 4)
        result["signal_val"] = round(float(cur["sig"]),  4)

        if bearish_cross:
            trigger_low = low.reindex(aligned.index).iloc[-1]
            result["week_low"] = round(float(trigger_low), 2) if pd.notna(trigger_low) else None

    except Exception as e:
        print(f"[EXIT] check_macd_bearish_cross({ticker}): {e}")
    return result


# ── Alpaca helpers ────────────────────────────────────────────────────────────

def _trading_client():
    key    = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        return None
    try:
        from alpaca.trading.client import TradingClient
        return TradingClient(key, secret, paper=True)
    except Exception:
        return None


def _find_stop_order(client, symbol: str):
    """Return the open stop-sell order for *symbol*, or None."""
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums   import QueryOrderStatus, OrderSide
        orders = client.get_orders(GetOrdersRequest(
            status  = QueryOrderStatus.OPEN,
            side    = OrderSide.SELL,
            symbols = [symbol],
        ))
        # Prefer explicit stop types; fall back to any sell order (covers bracket children)
        for o in orders:
            if str(getattr(o, "type", "")).lower() in ("stop", "stop_limit", "stop_market"):
                return o
        return orders[0] if orders else None
    except Exception as e:
        print(f"[EXIT] _find_stop_order({symbol}): {e}")
        return None


def _replace_stop(client, order_id: str, new_stop: float) -> bool:
    """Replace an existing order's stop price. Returns True on success."""
    try:
        from alpaca.trading.requests import ReplaceOrderRequest
        client.replace_order(
            order_id_or_client_order_id = order_id,
            order_data = ReplaceOrderRequest(stop_price=round(new_stop, 2)),
        )
        return True
    except Exception as e:
        print(f"[EXIT] _replace_stop({order_id}, {new_stop}): {e}")
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

def run_exit_checks(portfolio: Optional[dict], dry_run: bool = False) -> list[dict]:
    """Check every open Alpaca position for a MACD bearish cross.

    For each position with a bearish cross:
      - Computes raised stop = Low of the trigger weekly candle
      - Updates the existing Alpaca stop order (only raises, never lowers)

    Args:
      portfolio : output of alpaca_client.get_portfolio(), or None
      dry_run   : if True, compute checks but do not touch Alpaca orders

    Returns list of result dicts (one per position):
      symbol, entry, cross, macd, signal, week_low, new_stop, action, status
    """
    results: list[dict] = []

    if portfolio is None or not portfolio.get("positions"):
        return results

    client = None if dry_run else _trading_client()

    for pos in portfolio["positions"]:
        symbol = pos["symbol"]
        entry  = pos["avg_entry_price"]

        check = check_macd_bearish_cross(symbol)
        record: dict = {
            "symbol":    symbol,
            "entry":     entry,
            "cross":     check["cross"],
            "macd":      check["macd"],
            "signal":    check["signal_val"],
            "week_low":  check["week_low"],
            "new_stop":  None,
            "action":    "none",
            "status":    "no_cross",
        }

        if not check["cross"]:
            results.append(record)
            continue

        raised_stop = check["week_low"]
        if raised_stop is None:
            record["status"] = "cross_but_no_low"
            results.append(record)
            continue

        record["new_stop"] = raised_stop
        record["action"]   = "raise_stop"

        if dry_run:
            record["status"] = "dry_run"
            results.append(record)
            continue

        if client is None:
            record["status"] = "no_client"
            results.append(record)
            continue

        stop_order = _find_stop_order(client, symbol)
        if stop_order is None:
            record["status"] = "no_stop_order_found"
            results.append(record)
            continue

        existing_stop = float(getattr(stop_order, "stop_price", 0) or 0)

        if raised_stop <= existing_stop:
            record["action"] = "none"
            record["status"] = "stop_already_higher"
            results.append(record)
            continue

        success        = _replace_stop(client, str(stop_order.id), raised_stop)
        record["status"] = "updated" if success else "update_failed"
        results.append(record)

    return results
