"""
Exit Manager — MACD Bearish Cross + Profit-Taking (Minervini / O'Neill).

MACD exit rule (weekly):
  1. MACD line crosses below Signal line on a completed weekly candle
  2. Raised stop = Low (wick) of the trigger week candle
  3. Alpaca stop order updated (only raised, never lowered)

Profit-Taking rules (weekly, per open position):
  Regel 1 — Breakeven: Nach +10 % → Stop auf Einstiegspreis heben
  Regel 2 — O'Neill Fast Mover: +20 % in ≤ 3 Wochen → 8 Wochen halten, kein Teilverkauf
  Regel 3 — Minervini Power of Three:
      Bei +20 %  (normaler Move): 1/3 der Position verkaufen
      Bei +40 %: weiteres 1/3 verkaufen
      Rest: ATR-basierter Trailing Stop (2× ATR unter höchstem Wochenschluss)
"""

import os
import json
import datetime
from pathlib import Path
import yfinance as yf
import pandas as pd
from typing import Optional


def _load_pt_rules() -> dict:
    """Load profit_taking section from rules.json."""
    try:
        rules = json.loads((Path(__file__).parent / "rules.json").read_text(encoding="utf-8"))
        return rules.get("profit_taking", {})
    except Exception:
        return {}


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

def _place_market_sell(client, symbol: str, qty: int) -> bool:
    """Place a DAY market-sell order for *qty* shares of *symbol*."""
    try:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        client.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        ))
        return True
    except Exception as e:
        print(f"[PROFIT] _place_market_sell({symbol}, {qty}): {e}")
        return False


def _replace_stop_qty(client, order_id: str, new_qty: int) -> bool:
    """Reduce (or restore) the share quantity of an existing stop order."""
    try:
        from alpaca.trading.requests import ReplaceOrderRequest
        client.replace_order_by_id(
            order_id   = order_id,
            order_data = ReplaceOrderRequest(qty=int(new_qty)),
        )
        return True
    except Exception as e:
        print(f"[EXIT] _replace_stop_qty({order_id}, {new_qty}): {e}")
        return False


def _place_partial_sell(client, symbol: str, qty: int) -> bool:
    """Place a partial market-sell, freeing shares from the open stop order first.

    A GTC stop-loss order holds the ENTIRE position as collateral
    ("held_for_orders"). Alpaca rejects a market sell for any quantity while
    that hold covers all shares — it has 0 "available". So before selling, the
    stop order's qty is reduced by the amount we're about to sell; the stop
    price is left untouched. If the market sell then fails, the stop qty is
    restored so the remaining position stays protected.
    """
    stop_order = _find_stop_order(client, symbol)
    if stop_order is None:
        # No competing hold found — try the sell directly.
        return _place_market_sell(client, symbol, qty)

    held_qty = int(float(getattr(stop_order, "qty", 0) or 0))
    remaining = held_qty - qty
    if remaining < 0:
        print(f"[PROFIT] _place_partial_sell({symbol}): Verkaufsmenge {qty} "
              f"> Stop-Order-Menge {held_qty} — Abbruch")
        return False

    if remaining == 0:
        # Selling the whole held quantity — cancel the stop, it would be a
        # zero-qty order otherwise.
        try:
            client.cancel_order_by_id(str(stop_order.id))
        except Exception as e:
            print(f"[EXIT] cancel_order_by_id({stop_order.id}): {e}")
            return False
    elif not _replace_stop_qty(client, str(stop_order.id), remaining):
        return False

    ok = _place_market_sell(client, symbol, qty)
    if not ok and remaining != held_qty:
        # Roll back the qty reduction so the rest of the position stays protected.
        if remaining == 0:
            print(f"[PROFIT] {symbol}: Market-Sell fehlgeschlagen nach Stop-Order-"
                  f"Stornierung — Stop muss manuell neu gesetzt werden!")
        else:
            _replace_stop_qty(client, str(stop_order.id), held_qty)
    return ok


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
    """Return the open stop-sell order for *symbol*, or None.

    Two-pass strategy:
    1. Targeted query with symbols filter (fast path).
    2. Fallback: scan ALL open sell orders and filter by symbol client-side.
       This catches bracket/OTO child-legs that Alpaca's symbols filter sometimes
       omits when the parent order already filled.
    """
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums   import QueryOrderStatus, OrderSide

        def _best(orders):
            """Prefer explicit stop types; fall back to first sell order."""
            for o in orders:
                if str(getattr(o, "type", "")).lower() in ("stop", "stop_limit", "stop_market"):
                    return o
            return orders[0] if orders else None

        # Pass 1 — targeted
        orders = client.get_orders(GetOrdersRequest(
            status  = QueryOrderStatus.OPEN,
            side    = OrderSide.SELL,
            symbols = [symbol],
        ))
        hit = _best(orders)
        if hit is not None:
            return hit

        # Pass 2 — full scan (catches OTO/bracket child-legs missed by symbol filter)
        print(f"[EXIT] _find_stop_order({symbol}): symbol-filter empty, scanning all open sell orders")
        all_orders = client.get_orders(GetOrdersRequest(
            status = QueryOrderStatus.OPEN,
            side   = OrderSide.SELL,
        ))
        sym_orders = [o for o in all_orders
                      if str(getattr(o, "symbol", "")).upper() == symbol.upper()]
        hit = _best(sym_orders)
        if hit is None:
            print(f"[EXIT] _find_stop_order({symbol}): kein Stop-Order gefunden (auch nach Full-Scan)")
        return hit

    except Exception as e:
        print(f"[EXIT] _find_stop_order({symbol}): {e}")
        return None


def _replace_stop(client, order_id: str, new_stop: float) -> bool:
    """Replace an existing order's stop price. Returns True on success."""
    try:
        from alpaca.trading.requests import ReplaceOrderRequest
        client.replace_order_by_id(
            order_id   = order_id,
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


# ── Profit-Taking ─────────────────────────────────────────────────────────────

def check_profit_taking(trade: dict) -> dict:
    """Evaluate all profit-taking rules for a single open position.

    Returns a result dict with flags and target levels.  No orders are placed
    here — execution happens in run_profit_taking_checks().

    Keys returned:
      symbol, is_fast_mover, hold_until,
      raise_breakeven, breakeven_stop,
      partial_sell_1, partial_sell_1_qty,
      partial_sell_2, partial_sell_2_qty,
      trailing_stop, trailing_stop_level
    """
    symbol = trade.get("symbol", "")
    result: dict = {
        "symbol":             symbol,
        "is_fast_mover":      False,
        "hold_until":         None,
        "raise_breakeven":    False,
        "breakeven_stop":     None,
        "partial_sell_1":     False,
        "partial_sell_1_qty": 0,
        "partial_sell_1_price": None,
        "partial_sell_2":     False,
        "partial_sell_2_qty": 0,
        "partial_sell_2_price": None,
        "trailing_stop":      False,
        "trailing_stop_level": None,
    }

    entry_price   = trade.get("entry_price") or 0.0
    current_price = trade.get("current_price") or 0.0
    if entry_price <= 0 or current_price <= 0:
        return result

    gain_pct    = (current_price / entry_price - 1) * 100
    entry_date  = trade.get("entry_date", "")
    original_qty = int(trade.get("pt_original_qty") or trade.get("qty") or 0)
    current_qty  = int(trade.get("qty") or 0)

    # State flags already persisted in the journal
    is_fast_mover  = trade.get("pt_is_fast_mover",  False)
    hold_until     = trade.get("pt_hold_until")
    # pt_breakeven_alpaca_confirmed: Alpaca-Stop wurde wirklich angehoben.
    # pt_breakeven_done allein reicht nicht — es wird sofort gesetzt (für den
    # Report), auch wenn Alpaca noch nicht erfolgreich war.
    breakeven_done = trade.get("pt_breakeven_alpaca_confirmed", False)
    partial_1_done = trade.get("pt_partial_1_done", False)
    partial_2_done = trade.get("pt_partial_2_done", False)

    # Rule parameters
    pt = _load_pt_rules()
    breakeven_trigger  = pt.get("breakeven_trigger_pct", 10.0)
    fast_move_weeks    = int(pt.get("fast_move_weeks",    3))
    fast_move_hold     = int(pt.get("fast_move_hold_weeks", 8))
    partial_1_trigger  = pt.get("partial_1_trigger_pct", 20.0)
    partial_1_frac     = pt.get("partial_1_qty_frac",    0.333)
    partial_2_trigger  = pt.get("partial_2_trigger_pct", 40.0)
    partial_2_frac     = pt.get("partial_2_qty_frac",    0.333)
    trailing_atr_mult  = pt.get("trailing_atr_mult",     2.0)

    # ── Fetch weekly data ─────────────────────────────────────────────────────
    try:
        hist = yf.download(symbol, period="2y", interval="1wk",
                           progress=False, auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 10:
            return result
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
    except Exception as e:
        print(f"[PROFIT] {symbol} data: {e}")
        return result

    close = pd.to_numeric(hist["Close"], errors="coerce")
    high  = pd.to_numeric(hist["High"],  errors="coerce")
    low   = pd.to_numeric(hist["Low"],   errors="coerce")

    try:
        hist_since = hist[hist.index >= pd.Timestamp(entry_date)] if entry_date else hist
    except Exception:
        hist_since = hist

    # ── Regel 1: Breakeven-Stop nach +breakeven_trigger % ────────────────────
    if not breakeven_done and gain_pct >= breakeven_trigger:
        result["raise_breakeven"] = True
        result["breakeven_stop"]  = round(entry_price, 2)

    # ── Regel 2: O'Neill Fast-Mover-Erkennung ────────────────────────────────
    if not is_fast_mover and not partial_1_done:
        target = entry_price * (1 + partial_1_trigger / 100)
        first_n = hist_since.head(fast_move_weeks)
        if not first_n.empty:
            reached_early = (pd.to_numeric(first_n["High"], errors="coerce") >= target).any()
            if reached_early:
                result["is_fast_mover"] = True
                try:
                    hold_dt = (datetime.date.fromisoformat(entry_date)
                               + datetime.timedelta(weeks=fast_move_hold))
                    result["hold_until"] = hold_dt.isoformat()
                except Exception:
                    pass

    # ── Regel 3a + 3b: Minervini Teilverkäufe ────────────────────────────────
    today_str = datetime.date.today().isoformat()
    effective_fast = is_fast_mover or result["is_fast_mover"]
    fast_hold_over = effective_fast and hold_until and today_str >= hold_until
    allow_partial  = (not effective_fast) or fast_hold_over

    if allow_partial and not partial_1_done and gain_pct >= partial_1_trigger:
        qty1 = max(1, round(original_qty * partial_1_frac))
        if qty1 < current_qty:
            result["partial_sell_1"]       = True
            result["partial_sell_1_qty"]   = qty1
            result["partial_sell_1_price"] = current_price

    if allow_partial and partial_1_done and not partial_2_done and gain_pct >= partial_2_trigger:
        qty2 = max(1, round(original_qty * partial_2_frac))
        if qty2 < current_qty:
            result["partial_sell_2"]       = True
            result["partial_sell_2_qty"]   = qty2
            result["partial_sell_2_price"] = current_price

    # ── Regel 3c: ATR-Trailing-Stop für den Runner ───────────────────────────
    if partial_1_done:
        try:
            tr = pd.concat([
                (high - low).abs(),
                (high - close.shift(1)).abs(),
                (low  - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr10 = tr.rolling(10).mean().iloc[-1]

            highest_close = pd.to_numeric(hist_since["Close"], errors="coerce").max()
            if pd.notna(highest_close) and pd.notna(atr10) and atr10 > 0:
                trailing_level = round(highest_close - trailing_atr_mult * atr10, 2)
                current_stop   = trade.get("current_stop") or 0.0
                if trailing_level > current_stop and trailing_level < current_price:
                    result["trailing_stop"]       = True
                    result["trailing_stop_level"] = trailing_level
        except Exception as e:
            print(f"[PROFIT] ATR-Trailing {symbol}: {e}")

    return result


def run_profit_taking_checks(
    journal_data: dict,
    dry_run: bool = False,
    defer_sells: bool = False,
) -> list[dict]:
    """Run all profit-taking rules for every open position in the journal.

    For each position this may:
      - Raise the Alpaca stop to breakeven (after +10 %)
      - Place a partial market-sell (1/3 at +20 %, 1/3 at +40 %)
      - Apply an ATR-based trailing stop (after first partial sell)

    Args:
      defer_sells : when True, partial MARKET sells are not placed here — they
        are only flagged as 'partial_1_deferred' / 'partial_2_deferred' so the
        caller can persist them and execute on the next trading day. Stop-order
        modifications (breakeven, trailing) are still applied because Alpaca
        accepts GTC stop changes while the market is closed; only plain market
        orders are rejected outside trading hours. This is the weekend bug: the
        weekly report runs Saturday (market closed), so market sells fired here
        were always rejected and never persisted.

    Returns list of result dicts with an extra key 'actions_taken'.
    """
    results: list[dict] = []
    open_trades = journal_data.get("open", [])
    if not open_trades:
        return results

    client = None if dry_run else _trading_client()

    for trade in open_trades:
        check = check_profit_taking(trade)
        symbol = check["symbol"]
        actions: list[str] = []

        # 1. Breakeven-Stop
        if check["raise_breakeven"]:
            if dry_run:
                actions.append("breakeven_dry")
            elif client:
                order = _find_stop_order(client, symbol)
                if order:
                    existing = float(getattr(order, "stop_price", 0) or 0)
                    new_stop = check["breakeven_stop"]
                    if new_stop > existing and _replace_stop(client, str(order.id), new_stop):
                        actions.append("breakeven")

        # 2. Teilverkauf 1 — Market-Sell; bei defer_sells nur vormerken
        if check["partial_sell_1"]:
            qty = check["partial_sell_1_qty"]
            if dry_run:
                actions.append("partial_1_dry")
            elif defer_sells:
                actions.append("partial_1_deferred")
            elif client and _place_partial_sell(client, symbol, qty):
                actions.append("partial_1")

        # 3. Teilverkauf 2 — Market-Sell; bei defer_sells nur vormerken
        if check["partial_sell_2"]:
            qty = check["partial_sell_2_qty"]
            if dry_run:
                actions.append("partial_2_dry")
            elif defer_sells:
                actions.append("partial_2_deferred")
            elif client and _place_partial_sell(client, symbol, qty):
                actions.append("partial_2")

        # 4. ATR-Trailing-Stop
        if check["trailing_stop"]:
            if dry_run:
                actions.append("trailing_dry")
            elif client:
                order = _find_stop_order(client, symbol)
                if order:
                    existing = float(getattr(order, "stop_price", 0) or 0)
                    new_stop = check["trailing_stop_level"]
                    if new_stop > existing and _replace_stop(client, str(order.id), new_stop):
                        actions.append("trailing")

        check["actions_taken"] = actions
        results.append(check)

        if actions:
            print(f"[PROFIT] {symbol}: {', '.join(actions)}")

    return results


def execute_partial_sells(planned: list[dict], dry_run: bool = False) -> list[dict]:
    """Place the deferred profit-taking market sells on a trading day.

    Args:
      planned : list of dicts with keys symbol, qty, leg ('partial_1'/'partial_2').

    Returns the same list with an added 'placed' bool per entry.
    """
    client = None if dry_run else _trading_client()
    out: list[dict] = []
    for p in planned:
        sym = p["symbol"]
        qty = int(p["qty"])
        leg = p.get("leg", "partial_1")
        if qty < 1:
            out.append({**p, "placed": False})
            continue
        if dry_run:
            print(f"[PROFIT-MONDAY] 🔍 DRY-RUN {sym} {leg}: {qty} Stück")
            out.append({**p, "placed": True})
            continue
        ok = bool(client) and _place_partial_sell(client, sym, qty)
        print(f"[PROFIT-MONDAY] {'✅' if ok else '❌'} {sym} {leg}: {qty} Stück"
              + ("" if ok else " — Order fehlgeschlagen"))
        out.append({**p, "placed": ok})
    return out
