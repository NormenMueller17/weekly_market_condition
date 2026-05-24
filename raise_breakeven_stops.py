"""
raise_breakeven_stops.py — Einmaliger Live-Run der Profit-Taking-Checks.

Hebt Breakeven-Stops und Trailing-Stops in Alpaca an, ohne den vollen
Wochenrun (inkl. cancel_open_orders + Signal-Generator) auszuführen.

Verwendung:
    python raise_breakeven_stops.py           # Live-Modus: Alpaca wird wirklich geändert
    python raise_breakeven_stops.py --dry-run # Nur Vorschau, keine echten Orders
"""

import sys
import argparse
import alpaca_client
import exit_manager
import trade_journal

def main():
    parser = argparse.ArgumentParser(description="Breakeven/Trailing-Stops in Alpaca anheben")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur Vorschau — keine echten Alpaca-Orders")
    args = parser.parse_args()
    dry_run = args.dry_run

    mode = "DRY-RUN (Vorschau)" if dry_run else "LIVE (Alpaca wird geändert)"
    print(f"\n{'='*60}")
    print(f"  Breakeven/Trailing-Stop-Check — {mode}")
    print(f"{'='*60}\n")

    # 1. Portfolio + gefüllte Orders von Alpaca holen
    portfolio = alpaca_client.get_portfolio()
    if portfolio is None:
        print("❌ Kein Alpaca-Portfolio — Abbruch.")
        sys.exit(1)

    positions = [p["symbol"] for p in portfolio["positions"]]
    print(f"[ALPACA] {len(positions)} offene Positionen: {positions}\n")

    filled_buys  = alpaca_client.get_filled_orders("buy")
    filled_sells = alpaca_client.get_filled_orders("sell")

    # 2. Journal synchronisieren (aktualisiert current_price aus Alpaca)
    journal_data = trade_journal.sync(portfolio, filled_buys, filled_sells)

    # 3. Profit-Taking-Checks ausführen
    pt_results = exit_manager.run_profit_taking_checks(journal_data, dry_run=dry_run)

    # 4. Ergebnisse ins Journal schreiben
    journal_data = trade_journal.apply_profit_taking(journal_data, pt_results)

    # 5. Zusammenfassung ausgeben
    print(f"\n{'─'*60}")
    print("  Ergebnis pro Position:")
    print(f"{'─'*60}")
    any_action = False
    for r in pt_results:
        sym     = r["symbol"]
        actions = r.get("actions_taken", [])
        gain    = ((r.get("current_price") or 0) / (r.get("entry_price") or 1) - 1) * 100 \
                  if r.get("entry_price") else None

        gain_str = f"{gain:+.1f}%" if gain is not None else "?"

        flags = []
        if r.get("raise_breakeven"):
            status = "✅ Alpaca ✓" if "breakeven" in actions \
                     else ("🔍 Dry-Run" if "breakeven_dry" in actions \
                     else "⚠️  Alpaca fehlgeschlagen")
            flags.append(f"Breakeven @ {r['breakeven_stop']:.2f}  [{status}]")
        if r.get("is_fast_mover"):
            flags.append(f"🚀 Fast Mover (halten bis {r.get('hold_until')})")
        if r.get("partial_sell_1"):
            status = "✅" if "partial_1" in actions else ("🔍" if "partial_1_dry" in actions else "⚠️")
            flags.append(f"{status} Teilverkauf 1/3 ({r['partial_sell_1_qty']} Stück)")
        if r.get("partial_sell_2"):
            status = "✅" if "partial_2" in actions else ("🔍" if "partial_2_dry" in actions else "⚠️")
            flags.append(f"{status} Teilverkauf 2/3 ({r['partial_sell_2_qty']} Stück)")
        if r.get("trailing_stop"):
            status = "✅ Alpaca ✓" if "trailing" in actions \
                     else ("🔍 Dry-Run" if "trailing_dry" in actions \
                     else "⚠️  Alpaca fehlgeschlagen")
            flags.append(f"Trailing-Stop @ {r['trailing_stop_level']:.2f}  [{status}]")

        if flags:
            any_action = True
            print(f"  {sym:6s}  ({gain_str})")
            for f in flags:
                print(f"         → {f}")
        else:
            print(f"  {sym:6s}  ({gain_str})  – keine Aktion")

    print(f"{'─'*60}")
    if not any_action:
        print("  Keine Gewinnmitnahme-Aktionen ausgelöst.")

    # 6. HTML-Report neu bauen
    trade_journal.build_and_save_html(journal_data)
    print("\n✅ trades.html aktualisiert.")
    print()


if __name__ == "__main__":
    main()
