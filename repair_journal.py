"""Repariert historische position_closed_unknown-Eintraege aus den echten Alpaca-Fills.

Hintergrund: Durch die alpaca-py-Enum-Stringify-Regression gab get_filled_orders()
konstant [] zurueck, sodass geschlossene Positionen mit Fallback-Kurs statt echtem
Fill journalisiert wurden (exit_reason=position_closed_unknown). sync() repariert nur
OFFENE Positionen — bereits geschlossene Eintraege bleiben falsch. Dieses Skript
rekonstruiert exit_date/exit_price/exit_reason/realized_pl je Symbol aus ALLEN
gefuellten Sell-Fills (inkl. Teilverkaeufe).

Nutzung:
  python repair_journal.py            # Vorschau (dry-run), schreibt nichts
  python repair_journal.py --apply    # schreibt trades.json + baut trades.html neu

Symbole mit Mengen-Mismatch (verkauft != Ursprungsmenge, z.B. STX) werden NICHT
automatisch geschrieben, sondern zur manuellen Pruefung markiert.

Voraussetzung: ALPACA_API_KEY / ALPACA_API_SECRET (in Actions als Secrets).
"""
import os
import sys
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
import trade_journal

QTY_TOL = 0.5  # Toleranz fuer Mengenabgleich (verkauft vs. Ursprungsmenge)


def _reason_from_type(order_type: str) -> str:
    t = (order_type or "").lower()
    if "stop" in t:
        return "stop_hit"
    if "market" in t:
        return "manual_market"
    return "manual"


def _reconstruct(trade: dict, sells: list[dict]) -> dict:
    """Berechne echte Exit-Kennzahlen aus den Sell-Fills eines Symbols.

    Beruecksichtigt nur Fills am/nach entry_date, um Fills einer frueheren
    Position im selben Symbol nicht mitzuzaehlen.
    """
    entry     = trade.get("entry_price") or 0
    orig_qty  = trade.get("pt_original_qty") or trade.get("qty") or 0
    entry_day = (trade.get("entry_date") or "0000-00-00")[:10]

    relevant = [s for s in sells
                if s["filled_avg_price"] > 0 and s["filled_at"][:10] >= entry_day]
    relevant.sort(key=lambda s: s["filled_at"])

    sold_qty = sum(s["qty"] for s in relevant)
    realized = sum(s["qty"] * (s["filled_avg_price"] - entry) for s in relevant)
    weighted_exit = (sum(s["qty"] * s["filled_avg_price"] for s in relevant) / sold_qty
                     if sold_qty else 0)
    plpc = (realized / (entry * orig_qty) * 100) if entry and orig_qty else 0

    last = relevant[-1] if relevant else None
    return {
        "sells":         relevant,
        "sold_qty":      sold_qty,
        "orig_qty":      orig_qty,
        "exit_price":    round(weighted_exit, 4),
        "exit_date":     last["filled_at"][:10] if last else trade.get("exit_date"),
        "exit_reason":   _reason_from_type(last["order_type"]) if last else "position_closed_unknown",
        "exit_order_id": last["order_id"] if last else None,
        "realized_pl":   round(realized, 2),
        "realized_plpc": round(plpc, 2),
        "qty_ok":        abs(sold_qty - orig_qty) <= QTY_TOL,
    }


def main():
    apply = "--apply" in sys.argv

    data         = trade_journal.load()
    filled_sells = alpaca_client.get_filled_orders("sell", days_back=365)
    by_symbol: dict[str, list] = {}
    for s in filled_sells:
        by_symbol.setdefault(s["symbol"], []).append(s)

    targets = [t for t in data.get("closed", [])
               if t.get("exit_reason") == "position_closed_unknown"]
    if not targets:
        print("Keine position_closed_unknown-Eintraege gefunden — nichts zu tun.")
        return

    print(f"{len(targets)} zu reparierende Eintraege, "
          f"{len(filled_sells)} gefuellte Sells insgesamt abgerufen.\n")

    changed = 0
    flagged = []
    for trade in targets:
        sym = trade["symbol"]
        rec = _reconstruct(trade, by_symbol.get(sym, []))

        print(f"===== {sym} =====")
        print(f"  Fills: " + (", ".join(
            f"{s['qty']:g}@{s['filled_avg_price']:g}({s['order_type'].split('.')[-1]},"
            f"{s['filled_at'][:10]})" for s in rec["sells"]) or "(keine!)"))
        print(f"  ALT : reason={trade.get('exit_reason')} date={trade.get('exit_date')} "
              f"exit={trade.get('exit_price')} P&L={trade.get('realized_pl')} "
              f"({trade.get('realized_plpc')}%)")
        print(f"  NEU : reason={rec['exit_reason']} date={rec['exit_date']} "
              f"exit={rec['exit_price']} P&L={rec['realized_pl']} ({rec['realized_plpc']}%) "
              f"[verkauft {rec['sold_qty']:g} / orig {rec['orig_qty']:g}]")

        if not rec["sells"]:
            print("  -> SKIP: keine Fills gefunden.\n")
            flagged.append(sym)
            continue
        if not rec["qty_ok"]:
            print(f"  -> SKIP (Mengen-Mismatch: {rec['sold_qty']:g} verkauft auf "
                  f"{rec['orig_qty']:g} — manuell pruefen).\n")
            flagged.append(sym)
            continue

        if apply:
            trade["exit_date"]     = rec["exit_date"]
            trade["exit_price"]    = rec["exit_price"]
            trade["exit_reason"]   = rec["exit_reason"]
            trade["exit_order_id"] = rec["exit_order_id"]
            trade["realized_pl"]   = rec["realized_pl"]
            trade["realized_plpc"] = rec["realized_plpc"]
            changed += 1
            print("  -> GESCHRIEBEN.\n")
        else:
            changed += 1
            print("  -> wuerde geschrieben (dry-run).\n")

    if flagged:
        print(f"Manuell zu pruefen (nicht geschrieben): {', '.join(flagged)}")

    if apply and changed:
        data["closed"].sort(key=lambda t: t.get("exit_date", ""), reverse=True)
        trade_journal.save(data)
        trade_journal.build_and_save_html(data)
        print(f"\n{changed} Eintraege repariert — trades.json + trades.html aktualisiert.")
    else:
        print(f"\nDry-run: {changed} Eintraege wuerden repariert. Mit --apply schreiben.")


if __name__ == "__main__":
    main()
