"""
Stand-Alone Journal-Sync: Tagebuch sofort mit aktuellem Alpaca-Portfolio abgleichen.

Nutzung:
  python sync_journal.py          # normaler Sync
  python sync_journal.py --force  # schließt auch Positionen ohne gefundenen Sell-Order

Voraussetzung: ALPACA_API_KEY und ALPACA_API_SECRET in .env oder als Umgebungsvariablen.
"""
import sys
import os

# .env laden (falls vorhanden)
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

import alpaca_client
import trade_journal

def main():
    force = "--force" in sys.argv

    portfolio    = alpaca_client.get_portfolio()
    filled_buys  = alpaca_client.get_filled_orders("buy")
    filled_sells = alpaca_client.get_filled_orders("sell")

    if portfolio is None:
        print("❌ Kein Alpaca-Portfolio – Abbruch.")
        return

    print(f"[ALPACA] {len(portfolio['positions'])} Positionen, {len(filled_sells)} Sell-Orders")

    if force:
        _force_close_missing(portfolio, filled_sells)

    data = trade_journal.sync(portfolio, filled_buys, filled_sells)
    trade_journal.build_and_save_html(data)

    open_count   = len(data.get("open", []))
    closed_count = len(data.get("closed", []))
    print(f"\n✅ Sync abgeschlossen — {open_count} offen / {closed_count} geschlossen")
    print(f"   → docs/data/trades.json + docs/trades.html aktualisiert")


def _force_close_missing(portfolio: dict, filled_sells: list[dict]) -> None:
    """Schließt Journal-Positionen, die nicht mehr in Alpaca sind und keinen Sell-Order haben.

    Legt exit_reason='position_closed_unknown' an und nutzt current_price als Exit-Preis.
    Nur ausgeführt mit --force.
    """
    import datetime

    data         = trade_journal.load()
    open_symbols = {p["symbol"] for p in portfolio["positions"]}
    sell_syms    = {o["symbol"] for o in filled_sells if o["filled_avg_price"] > 0}
    today        = datetime.date.today().isoformat()

    still_open = []
    for trade in data["open"]:
        sym = trade["symbol"]
        if sym in open_symbols:
            still_open.append(trade)
            continue
        if sym in sell_syms:
            still_open.append(trade)   # wird von sync() normal behandelt
            continue
        # Kein Alpaca-Eintrag, kein Sell-Order → Force-Close
        exit_price = trade.get("current_price") or trade.get("entry_price") or 0
        entry      = trade.get("entry_price") or 0
        qty        = trade.get("qty") or 0
        pl         = (exit_price - entry) * qty
        pl_pct     = ((exit_price / entry) - 1) * 100 if entry else 0
        closed_trade = {
            **trade,
            "exit_date":     today,
            "exit_price":    round(exit_price, 4),
            "exit_reason":   "position_closed_unknown",
            "exit_order_id": None,
            "realized_pl":   round(pl, 2),
            "realized_plpc": round(pl_pct, 2),
        }
        for k in ("current_price", "unrealized_pl", "unrealized_plpc",
                  "stop_raised_date", "current_stop"):
            closed_trade.pop(k, None)
        data["closed"].append(closed_trade)
        print(f"[FORCE-CLOSE] ⚠️  {sym} → geschlossen (exit_reason=position_closed_unknown, "
              f"exit_price={exit_price:.2f}, P&L {pl:+.0f})")

    data["open"] = still_open
    data["closed"].sort(key=lambda t: t.get("exit_date", ""), reverse=True)
    trade_journal.save(data)


if __name__ == "__main__":
    main()
