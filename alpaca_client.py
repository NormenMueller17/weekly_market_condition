"""Thin Alpaca Paper-Trading client.

Fetches account summary and open positions for dynamic position sizing and
portfolio reporting. Also places OTO stop-limit orders for buy signals.

Order flow per signal (top picks only):
  Parent : Stop-Limit Buy  @ buy_stop / max_gap_price  (GTC)
  Child  : Stop-Market Sell @ stop_loss                 (auto-activated on fill)

Requires env vars ALPACA_API_KEY and ALPACA_API_SECRET.
Falls back gracefully (returns None / []) when keys are missing or
the API is unreachable, so the screener can run without Alpaca.
"""

import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from signal_generator import TradeSignal

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


def _open_order_symbols(client) -> set[str]:
    """Return set of ticker symbols that already have an open order."""
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return {o.symbol for o in orders}
    except Exception:
        return set()


def place_signal_orders(signals: list, dry_run: bool = False) -> list[dict]:
    """Place OTO stop-limit orders for top-pick signals.

    For each top pick:
      - Parent:  Stop-Limit Buy  @ buy_stop (trigger) / max_gap_price (limit)  GTC
      - Child:   Stop-Market Sell @ stop_loss  (auto-placed by Alpaca on fill)

    Skips tickers that already have an open order or an existing position.
    Returns a list of result dicts (one per signal) with keys:
      ticker, qty, status, order_id (if placed)
    """
    client = _get_trading_client()
    if client is None:
        return [{"ticker": s.ticker, "qty": 0, "status": "no_client"} for s in signals if s.is_top_pick]

    existing_orders    = _open_order_symbols(client)
    existing_positions = set(open_position_tickers())
    skip_tickers       = existing_orders | existing_positions

    results: list[dict] = []

    for sig in [s for s in signals if s.is_top_pick]:
        qty = int(sig.position_value // sig.buy_stop)
        base = {"ticker": sig.ticker, "qty": qty,
                "buy_stop": sig.buy_stop, "max_gap": sig.max_gap_price,
                "stop_loss": sig.stop_loss}

        if qty < 1:
            results.append({**base, "status": "skip_qty_too_small"})
            continue

        if sig.ticker in skip_tickers:
            results.append({**base, "status": "skip_already_exists"})
            continue

        if dry_run:
            results.append({**base, "status": "dry_run"})
            continue

        try:
            from alpaca.trading.requests import StopLimitOrderRequest, StopLossRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

            order = client.submit_order(
                StopLimitOrderRequest(
                    symbol         = sig.ticker,
                    qty            = qty,
                    side           = OrderSide.BUY,
                    time_in_force  = TimeInForce.GTC,
                    stop_price     = round(sig.buy_stop, 2),
                    limit_price    = round(sig.max_gap_price, 2),
                    order_class    = OrderClass.OTO,
                    stop_loss      = StopLossRequest(stop_price=round(sig.stop_loss, 2)),
                )
            )
            results.append({**base, "status": "placed", "order_id": str(order.id)})

        except Exception as e:
            results.append({**base, "status": f"error: {e}"})

    return results


def get_filled_orders(side: str = "sell", days_back: int = 365) -> list[dict]:
    """Return filled buy or sell orders from Alpaca order history.

    Args:
      side      : "buy" or "sell"
      days_back : how far back to search (default 1 year)

    Returns list of dicts with keys:
      symbol, qty, filled_avg_price, filled_at (ISO str), order_id, order_type
    """
    import datetime
    client = _get_trading_client()
    if client is None:
        return []
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums   import QueryOrderStatus, OrderSide

        after     = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)
        side_enum = OrderSide.BUY if side == "buy" else OrderSide.SELL

        orders = client.get_orders(GetOrdersRequest(
            status = QueryOrderStatus.CLOSED,
            side   = side_enum,
            after  = after,
            limit  = 500,
        ))

        result = []
        for o in orders:
            if str(getattr(o, "status", "")) != "filled":
                continue
            result.append({
                "symbol":           o.symbol,
                "qty":              float(getattr(o, "filled_qty",      0) or 0),
                "filled_avg_price": float(getattr(o, "filled_avg_price", 0) or 0),
                "filled_at":        str(getattr(o,  "filled_at",        "") or ""),
                "order_id":         str(o.id),
                "order_type":       str(getattr(o,  "type",             "") or ""),
            })
        return result
    except Exception as e:
        print(f"[ALPACA] get_filled_orders({side}): {e}")
        return []


def get_filled_sells_since(symbols: list[str], since_date_str: str) -> dict[str, dict]:
    """Return filled sell orders for *symbols* placed on/after *since_date_str* (YYYY-MM-DD).

    Returns dict mapping symbol -> {qty, filled_avg_price, filled_at, order_id}.
    Only the most recent fill per symbol is kept.
    """
    import datetime
    client = _get_trading_client()
    if client is None:
        return {}
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums   import QueryOrderStatus, OrderSide

        since  = datetime.datetime.fromisoformat(since_date_str).replace(
            tzinfo=datetime.timezone.utc
        )
        orders = client.get_orders(GetOrdersRequest(
            status = QueryOrderStatus.CLOSED,
            side   = OrderSide.SELL,
            after  = since,
            limit  = 500,
        ))

        result: dict[str, dict] = {}
        sym_set = set(symbols)
        for o in orders:
            if str(getattr(o, "status", "")) != "filled":
                continue
            sym = o.symbol
            if sym not in sym_set:
                continue
            result[sym] = {
                "qty":              float(getattr(o, "filled_qty",      0) or 0),
                "filled_avg_price": float(getattr(o, "filled_avg_price", 0) or 0),
                "filled_at":        str(getattr(o,  "filled_at",        "") or ""),
                "order_id":         str(o.id),
            }
        return result
    except Exception as e:
        print(f"[ALPACA] get_filled_sells_since: {e}")
        return {}


def get_portfolio_history(period: str = "1A") -> Optional[dict]:
    """Return daily equity history from Alpaca (up to 1 year).

    Returns dict with:
      timestamps – list of Unix timestamp ints (one per day)
      equity     – list of equity floats (parallel to timestamps)
      base_value – starting equity of the period
    or None on failure.
    """
    client = _get_trading_client()
    if client is None:
        return None
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        history = client.get_portfolio_history(
            GetPortfolioHistoryRequest(period=period, timeframe="1D")
        )
        timestamps = [int(t) for t in (history.timestamp or [])]
        equity     = [float(e) if e is not None else None for e in (history.equity or [])]
        base       = float(history.base_value) if history.base_value else None
        # Drop leading None/zero entries (account not yet funded)
        while equity and (equity[0] is None or equity[0] == 0):
            equity.pop(0)
            timestamps.pop(0)
        return {"timestamps": timestamps, "equity": equity, "base_value": base}
    except Exception as e:
        print(f"[ALPACA] get_portfolio_history: {e}")
        return None


def cancel_open_orders() -> int:
    """Cancel all open GTC orders. Call at end of week for cleanup.

    Returns number of cancelled orders.
    """
    client = _get_trading_client()
    if client is None:
        return 0
    try:
        cancelled = client.cancel_orders()
        return len(cancelled) if cancelled else 0
    except Exception:
        return 0
