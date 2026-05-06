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


def _find_open_stop_sell(client, symbol: str):
    """Return the open stop-sell order for *symbol*, or None."""
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus, OrderSide
        orders = client.get_orders(GetOrdersRequest(
            status  = QueryOrderStatus.OPEN,
            side    = OrderSide.SELL,
            symbols = [symbol],
        ))
        for o in orders:
            if str(getattr(o, "type", "")).lower() in ("stop", "stop_limit", "stop_market"):
                return o
        return orders[0] if orders else None
    except Exception as e:
        print(f"[COVERAGE] _find_open_stop_sell({symbol}): {e}")
        return None


def check_sell_order_coverage(portfolio: Optional[dict], dry_run: bool = False) -> list[dict]:
    """Prüfe für jede offene Position ob eine aktive Stop-Sell-Order existiert.

    Fehlt eine Order, wird sie automatisch aus dem Trade-Journal angelegt.
    Stop-Preis: current_stop → initial_stop → 8%-Fallback unter Einstandspreis.

    Returns list of result dicts per position:
      symbol, status, stop_price, [qty, order_id]
    """
    import json
    from pathlib import Path

    if portfolio is None or not portfolio.get("positions"):
        return []

    client = _get_trading_client()
    if client is None:
        return [{"symbol": p["symbol"], "status": "no_client"} for p in portfolio["positions"]]

    # Stop-Levels aus Trade-Journal laden
    trades_file = Path("docs/data/trades.json")
    journal_stops: dict[str, float] = {}
    if trades_file.exists():
        try:
            data = json.loads(trades_file.read_text(encoding="utf-8"))
            for trade in data.get("open", []):
                sym  = trade["symbol"]
                stop = trade.get("current_stop") or trade.get("initial_stop")
                if stop:
                    journal_stops[sym] = float(stop)
        except Exception:
            pass

    results: list[dict] = []
    for pos in portfolio["positions"]:
        symbol      = pos["symbol"]
        qty         = float(pos["qty"])
        entry_price = float(pos["avg_entry_price"])

        stop_order = _find_open_stop_sell(client, symbol)

        if stop_order is not None:
            existing_stop = float(getattr(stop_order, "stop_price", 0) or 0)
            results.append({"symbol": symbol, "status": "covered", "stop_price": existing_stop})
            continue

        # Keine Sell-Order — Stop-Preis bestimmen
        stop_price = journal_stops.get(symbol)
        if stop_price is None:
            stop_price = round(entry_price * 0.92, 2)  # 8%-Fallback
            print(f"[COVERAGE] ⚠️  {symbol}: kein Journal-Stop → Fallback 8% unter Entry ({stop_price:.2f})")

        result: dict = {"symbol": symbol, "qty": qty, "stop_price": stop_price}

        if dry_run:
            result["status"] = "missing_dry_run"
            print(f"[COVERAGE] ⚠️  {symbol}: KEINE Sell-Order!  DRY-RUN — würde Stop @ {stop_price:.2f} anlegen")
            results.append(result)
            continue

        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            order = client.submit_order(StopOrderRequest(
                symbol        = symbol,
                qty           = int(qty),
                side          = OrderSide.SELL,
                time_in_force = TimeInForce.GTC,
                stop_price    = round(stop_price, 2),
            ))
            result["status"]   = "placed"
            result["order_id"] = str(order.id)
            print(f"[COVERAGE] ✅ {symbol}: Stop-Sell angelegt @ {stop_price:.2f}  (Qty {int(qty)})")
        except Exception as e:
            result["status"] = f"error: {e}"
            print(f"[COVERAGE] ❌ {symbol}: Stop-Sell konnte nicht angelegt werden: {e}")

        results.append(result)

    return results


def refresh_expiring_sell_orders(dry_run: bool = False, warn_days: int = 80) -> list[dict]:
    """Erneuere GTC-Sell-Orders, die älter als *warn_days* Tage sind.

    Alpaca löscht GTC-Orders stillschweigend nach 90 Tagen. Diese Funktion
    findet ablaufende Stop-Sell-Orders, storniert sie und legt neue mit
    demselben Stop-Preis und derselben Menge an.

    Returns list of result dicts per order:
      symbol, age_days, stop_price, status, [order_id, new_order_id]
    """
    import datetime

    client = _get_trading_client()
    if client is None:
        return [{"status": "no_client"}]

    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus, OrderSide
        open_sells = client.get_orders(GetOrdersRequest(
            status = QueryOrderStatus.OPEN,
            side   = OrderSide.SELL,
        ))
    except Exception as e:
        print(f"[REFRESH] Fehler beim Laden der Sell-Orders: {e}")
        return [{"status": f"error: {e}"}]

    now     = datetime.datetime.now(datetime.timezone.utc)
    results: list[dict] = []

    for order in (open_sells or []):
        symbol     = order.symbol
        created_at = getattr(order, "created_at", None)
        stop_price = float(getattr(order, "stop_price", 0) or 0)
        qty        = float(getattr(order, "qty", 0) or 0)

        if created_at is None:
            results.append({"symbol": symbol, "status": "no_created_at"})
            continue

        if isinstance(created_at, str):
            try:
                created_at = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                results.append({"symbol": symbol, "status": "invalid_date"})
                continue

        age_days = (now - created_at).days

        if age_days < warn_days:
            results.append({"symbol": symbol, "status": "ok", "age_days": age_days, "stop_price": stop_price})
            continue

        result: dict = {
            "symbol":     symbol,
            "age_days":   age_days,
            "stop_price": stop_price,
            "qty":        qty,
            "order_id":   str(order.id),
        }

        if dry_run:
            result["status"] = "expiring_dry_run"
            print(f"[REFRESH] ⚠️  {symbol}: Sell-Order {age_days} Tage alt — DRY-RUN würde erneuern @ {stop_price:.2f}")
            results.append(result)
            continue

        # 1. Alte Order stornieren
        try:
            client.cancel_order_by_id(str(order.id))
        except Exception as e:
            result["status"] = f"cancel_error: {e}"
            print(f"[REFRESH] ❌ {symbol}: Stornierung fehlgeschlagen: {e}")
            results.append(result)
            continue

        # 2. Neue Order mit gleicher Konfiguration anlegen
        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            new_order = client.submit_order(StopOrderRequest(
                symbol        = symbol,
                qty           = int(qty),
                side          = OrderSide.SELL,
                time_in_force = TimeInForce.GTC,
                stop_price    = round(stop_price, 2),
            ))
            result["status"]       = "renewed"
            result["new_order_id"] = str(new_order.id)
            print(f"[REFRESH] ✅ {symbol}: Stop-Sell erneuert @ {stop_price:.2f}  (war {age_days} Tage alt)")
        except Exception as e:
            result["status"] = f"place_error: {e}"
            print(f"[REFRESH] ❌ {symbol}: Neue Order konnte nicht angelegt werden: {e}")

        results.append(result)

    return results


def cancel_open_orders() -> int:
    """Cancel unfired BUY-stop orders from the previous week.

    Only cancels open BUY-side orders. SELL-side orders (protective stops for
    existing positions) are intentionally left untouched.

    Returns number of cancelled orders.
    """
    client = _get_trading_client()
    if client is None:
        return 0
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus, OrderSide

        open_buys = client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            side=OrderSide.BUY,
        ))
        cancelled = 0
        for order in (open_buys or []):
            try:
                client.cancel_order_by_id(str(order.id))
                cancelled += 1
            except Exception as e:
                print(f"[ALPACA] cancel_open_orders: Fehler bei {order.symbol}: {e}")
        return cancelled
    except Exception as e:
        print(f"[ALPACA] cancel_open_orders: {e}")
        return 0
