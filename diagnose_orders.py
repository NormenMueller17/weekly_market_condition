"""Diagnose: warum bekommen Positionen exit_reason=position_closed_unknown?

Fragt ALLE Orders (jeder Status, jede Seite) der uebergebenen Symbole direkt bei
Alpaca ab und zeigt Status / Seite / Typ / filled_qty / filled_avg_price /
submitted_at / filled_at / order_class. Nur lesend — keine Orders, keine Trades.

Nutzung:
  python diagnose_orders.py ON STX VSH GLW NUE TTMI SANM
  python diagnose_orders.py            # nimmt automatisch alle unknown-Symbole aus trades.json

Voraussetzung: ALPACA_API_KEY und ALPACA_API_SECRET als Umgebungsvariablen
(in GitHub Actions via Secrets gesetzt).
"""
import os
import sys
import json
import datetime

# .env laden (falls lokal vorhanden)
_env = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env):
    with open(_env, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

import alpaca_client


def _unknown_syms_from_journal() -> list[str]:
    path = os.path.join(os.path.dirname(__file__), "docs", "data", "trades.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    return sorted({t["symbol"] for t in d.get("closed", [])
                   if t.get("exit_reason") == "position_closed_unknown"})


def main():
    syms = [s.upper() for s in sys.argv[1:]] or _unknown_syms_from_journal()
    if not syms:
        print("Keine Symbole angegeben und keine unknown-Trades im Journal gefunden.")
        return
    print(f"Analysiere Symbole: {', '.join(syms)}\n")

    client = alpaca_client._get_trading_client()
    if client is None:
        print("Kein Alpaca-Client (ALPACA_API_KEY / ALPACA_API_SECRET fehlen?)")
        return

    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    after = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=180)

    # ALLE Orders (jeder Status) durchpaginieren — bewusst KEIN side/symbol-Filter
    # server-seitig, um OTO-Child-Legs sicher zu erfassen.
    PAGE = 500
    all_orders, seen = [], set()
    until = None
    while True:
        batch = client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.ALL, after=after, until=until, limit=PAGE,
        ))
        if not batch:
            break
        new = 0
        for o in batch:
            if str(o.id) in seen:
                continue
            seen.add(str(o.id))
            all_orders.append(o)
            new += 1
        if len(batch) < PAGE or new == 0:
            break
        until = getattr(batch[-1], "submitted_at", None)
        if until is None:
            break

    print(f"Gesamt {len(all_orders)} Orders in den letzten 180 Tagen abgerufen.\n")

    want = {s.upper() for s in syms}
    for sym in syms:
        rows = [o for o in all_orders if o.symbol == sym]
        rows.sort(key=lambda o: str(getattr(o, "submitted_at", "")))
        print(f"===== {sym}  ({len(rows)} Orders) =====")
        if not rows:
            print("  (keine Orders im 180-Tage-Fenster gefunden!)\n")
            continue
        for o in rows:
            print(
                f"  side={str(o.side):18s} type={str(o.type):18s} "
                f"status={str(o.status):22s} class={str(getattr(o,'order_class',None)):12s} "
                f"qty={getattr(o,'qty',None)} filled_qty={getattr(o,'filled_qty',None)} "
                f"fill_price={getattr(o,'filled_avg_price',None)} "
                f"sub={str(getattr(o,'submitted_at',''))[:19]} "
                f"filled={str(getattr(o,'filled_at',''))[:19]} "
                f"id={str(o.id)[:8]}"
            )
        # Kurzdiagnose: Wuerde sync() einen Sell finden?
        sells = [o for o in rows if str(o.side).lower().endswith("sell")
                 and str(o.status) == "OrderStatus.FILLED"
                 and float(getattr(o, "filled_avg_price", 0) or 0) > 0]
        if sells:
            print(f"  -> {len(sells)} passende Filled-Sell(s) MIT Preis vorhanden — "
                  f"sync() HAETTE matchen muessen (moeglicher Filter-/Pagination-Bug).")
        else:
            filled_sells_noprice = [o for o in rows if str(o.side).lower().endswith("sell")
                                    and str(o.status) == "OrderStatus.FILLED"]
            if filled_sells_noprice:
                print("  -> Filled-Sell vorhanden, aber filled_avg_price=0 — vom Filter (>0) ausgeschlossen!")
            else:
                print("  -> KEIN gefuellter Sell-Order — Position evtl. anders geschlossen (Storno/Expiry?).")
        print()


if __name__ == "__main__":
    main()
