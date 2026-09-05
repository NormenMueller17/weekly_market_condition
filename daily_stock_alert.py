"""daily_stock_alert.py — Taeglicher Schutzlauf fuer das Aktiendepot (Mo-Fr).

  python daily_stock_alert.py             # Live
  python daily_stock_alert.py --dry-run   # nur pruefen, nichts aendern

Warum es das gibt
-----------------
Bis 2026-07-29 liefen ALLE dynamischen Exit-Regeln ausschliesslich im
Samstagslauf. Der initiale Stop lag zwar als GTC-Order beim Broker und griff
jederzeit — aber Breakeven, Trailing/Giveback und der Coverage-Check wurden nur
einmal pro Woche ausgewertet. Bis zu sechs Handelstage Luecke.

Was das kostet, steht im Tagebuch: sechs Fast Mover fielen von einem
Hoechststand im Median +48,0 % auf -0,0 % zurueck. Der Giveback-Stop, der genau
das verhindern soll, wurde erst am Samstag nachgezogen — da war der Gewinn weg.
Eine Position ohne Stop-Order (AVNS) blieb ebenso bis Samstag unbemerkt.

Was hier laeuft
---------------
  1. Coverage-Check  — Positionen ohne Stop-Order finden
  2. Ablaufende Stops verlaengern
  3. Journal synchronisieren
  4. Profit-Taking   — Breakeven, Trailing/Giveback, Teilverkaeufe

Teilverkaeufe laufen hier SOFORT (`defer_sells=False`). Der Umweg ueber
`--monday-execute` existiert nur, weil samstags der Markt zu ist; werktags ist
er offen.

Was hier bewusst NICHT laeuft
-----------------------------
Der MACD-Bearish-Cross (`exit_manager.run_exit_checks`). Die Regel ist auf
ABGESCHLOSSENEN Wochenbalken definiert — `check_macd_bearish_cross` nimmt
`iloc[-1]`, was samstags die fertige Woche ist, werktags aber eine angebrochene.
Mitten in der Woche ausgewertet waere das nicht dieselbe Regel oefter, sondern
eine andere, nie gemessene: das Kreuz flackert, und `week_low` waere das Tief
von ein bis vier Tagen statt einer ganzen Woche — also ein deutlich zu enger
Stop. Der Cross bleibt beim Samstagslauf.

Aus demselben Grund laeuft das Profit-Taking mit `partial_week=True`: die ATR10
klammert die angebrochene Woche aus, Hoch und Schlusskurs bleiben drin.

Stille ist ein Ergebnis: ohne Aktion keine Mail. Jeder Fehlerpfad endet mit
Exit-Code != 0 und Fehlermail, damit ein Absturz nicht wie ein ruhiger Tag
aussieht.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import alpaca_client
import exit_manager
import trade_journal
from emailer import send_email


class DailyAlertError(RuntimeError):
    """Abbruchgrund, der als Fehlermail rausgeht."""


def _fmt_money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "–"


# ── Mail ──────────────────────────────────────────────────────────────────────

_ACTION_LABEL = {
    "breakeven":          "Breakeven-Stop gesetzt",
    "breakeven_dry":      "Breakeven-Stop (Vorschau)",
    "trailing":           "Trailing-Stop nachgezogen",
    "trailing_dry":       "Trailing-Stop (Vorschau)",
    "partial_1":          "Teilverkauf 1 (+20 %)",
    "partial_1_dry":      "Teilverkauf 1 (Vorschau)",
    "partial_2":          "Teilverkauf 2 (+40 %)",
    "partial_2_dry":      "Teilverkauf 2 (Vorschau)",
}


def build_mail(pt_results: list[dict], uncovered: list[dict],
               refreshed: list[dict], dry_run: bool,
               dashboard_error: str | None = None) -> str:
    banner = ("<p style='background:#fff3cd;border:1px solid #ffeeba;padding:.6em;'>"
              "⚠️ TEST-MODUS — nichts wurde geändert</p>") if dry_run else ""
    if dashboard_error:
        banner += ("<p style='background:#f8d7da;border:1px solid #f5c6cb;padding:.6em;'>"
                   f"⚠️ Dashboard-Aktualisierung fehlgeschlagen: {dashboard_error}</p>")

    cover_html = ""
    if uncovered:
        rows = "".join(
            f"<tr><td class='left'><b>{u.get('symbol', '?')}</b></td>"
            f"<td class='left'>{u.get('status', '')}</td></tr>"
            for u in uncovered
        )
        cover_html = (
            "<h3 style='color:#721c24;'>⚠️ Positionen ohne Stop-Order</h3>"
            f"<table>{rows}</table>"
        )

    act_rows = ""
    for r in pt_results:
        acts = r.get("actions_taken", [])
        if not acts:
            continue
        labels = ", ".join(_ACTION_LABEL.get(a, a) for a in acts)
        level = r.get("trailing_stop_level") or r.get("breakeven_stop")
        act_rows += (
            f"<tr><td class='left'><b>{r['symbol']}</b></td>"
            f"<td class='left'>{labels}</td>"
            f"<td>{_fmt_money(level)}</td></tr>"
        )
    act_html = (
        "<h3>Ausgeführte Anpassungen</h3>"
        f"<table><tr><th>Symbol</th><th>Aktion</th><th>Level</th></tr>{act_rows}</table>"
        if act_rows else ""
    )

    refresh_html = ""
    if refreshed:
        rows = "".join(
            f"<tr><td class='left'><b>{r.get('symbol', '?')}</b></td>"
            f"<td class='left'>{r.get('status', '')}</td></tr>"
            for r in refreshed
        )
        refresh_html = f"<h3>Verlängerte Stop-Orders</h3><table>{rows}</table>"

    return f"""
<div style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;padding:1em;">
  <h2 style="color:#003d99;">Depot-Alert — {date.today().isoformat()}</h2>
  {banner}
  {cover_html}
  {act_html}
  {refresh_html}
  <p style="color:#555;font-size:.9em;">
    MACD-Bearish-Cross wird nur im Samstagslauf geprüft — die Regel ist auf
    abgeschlossene Wochenbalken definiert.
  </p>
  <style>
    table {{ border-collapse:collapse; margin-bottom:1.5em; }}
    th,td {{ border:1px solid #d0d5e8; padding:.45em .9em; text-align:right; }}
    th {{ background:#eef2fa; color:#003d99; }}
    .left {{ text-align:left; }}
  </style>
</div>
"""


def send_error_mail(msg: str) -> None:
    body = (f"<div style='font-family:Arial,sans-serif;'>"
            f"<h2 style='color:#721c24;'>Depot-Alert fehlgeschlagen</h2>"
            f"<pre style='background:#f8f8f8;padding:1em;white-space:pre-wrap;'>{msg}</pre>"
            f"<p>Stops und Gewinnmitnahme wurden heute möglicherweise nicht geprüft.</p>"
            f"</div>")
    try:
        send_email(body, subject_suffix="Depot-Alert FEHLGESCHLAGEN")
    except Exception as e:
        print(f"[DAILY] Fehlermail konnte nicht verschickt werden: {e}")


# ── Ablauf ────────────────────────────────────────────────────────────────────

def run(dry_run: bool) -> int:
    portfolio = alpaca_client.get_portfolio()
    if portfolio is None:
        raise DailyAlertError(
            "Alpaca-Portfolio nicht abrufbar. Ohne Bestand kann weder der "
            "Coverage-Check noch die Gewinnmitnahme laufen."
        )
    positions = portfolio.get("positions", [])
    print(f"[DAILY] {len(positions)} offene Position(en): "
          f"{[p['symbol'] for p in positions]}")
    if not positions:
        print("[DAILY] Kein Bestand — nichts zu pruefen.")
        return 0

    # 1. Positionen ohne Stop-Order
    coverage  = alpaca_client.check_sell_order_coverage(portfolio, dry_run=dry_run)
    # "delisted" ist kein Mangel, sondern ein bekannter Dauerzustand — sonst
    # kaeme die Mail bis in alle Ewigkeit taeglich.
    uncovered = [c for c in coverage
                 if c.get("status") not in ("covered", "ok", "delisted")]
    for c in coverage:
        print(f"[COVERAGE] {c.get('symbol')}: {c.get('status')}")

    # 2. Ablaufende Stop-Orders verlaengern
    #
    # `refresh_expiring_sell_orders` liefert eine Zeile je GEPRUEFTER Order,
    # nicht je verlaengerter — Orders, die noch lange genug laufen, kommen mit
    # status "ok" zurueck. Wer die ganze Liste als Aktion wertet, verschickt
    # jeden Tag eine Mail, solange ueberhaupt eine Position offen ist. Genau
    # das ist hier passiert.
    geprueft  = alpaca_client.refresh_expiring_sell_orders(dry_run=dry_run)
    refreshed = [r for r in geprueft if r.get("status") != "ok"]
    for r in geprueft:
        print(f"[REFRESH] {r.get('symbol')}: {r.get('status')}")
    if geprueft and not refreshed:
        print(f"[REFRESH] {len(geprueft)} Sell-Order(s) aktiv, keine ablaufenden")

    # 3. Journal synchronisieren
    filled_buys  = alpaca_client.get_filled_orders("buy")
    filled_sells = alpaca_client.get_filled_orders("sell")
    journal_data = trade_journal.sync(portfolio, filled_buys, filled_sells)

    # 4. Gewinnmitnahme. Markt ist werktags offen -> Teilverkaeufe sofort.
    #    partial_week=True: ATR ohne die angebrochene Woche (siehe Kopf).
    pt_results = exit_manager.run_profit_taking_checks(
        journal_data, dry_run=dry_run, defer_sells=False, partial_week=True,
    )
    journal_data = trade_journal.apply_profit_taking(journal_data, pt_results)

    for r in pt_results:
        acts = r.get("actions_taken", [])
        print(f"[PROFIT] {r['symbol']}: {', '.join(acts) if acts else 'keine Aktion'}")

    # 5. Dashboard aktualisieren (Depot-Equity, Performance-Seite, Index).
    #    Nur bei echtem Lauf — im Dry-Run soll nichts auf der Seite landen,
    #    was nicht tatsaechlich passiert ist. Der volle Report inkl. Marktampel
    #    bleibt Samstag vorbehalten (main.py); hier wird nur die zuletzt
    #    gespeicherte Ampel (docs/data/ampel.json) weiterverwendet.
    #
    #    Fehler hier duerfen NICHT die Mail mit den tatsaechlich ausgefuehrten
    #    Stop-/Profit-Taking-Aktionen verschlucken (die sind bereits passiert
    #    und muessen gemeldet werden) — deshalb kein harter Abbruch, aber der
    #    Fehler wird sichtbar in die Mail aufgenommen statt nur ins Log.
    dashboard_error = None
    if not dry_run:
        try:
            _refresh_dashboard(journal_data, portfolio)
        except Exception as e:
            dashboard_error = str(e)
            print(f"[DASHBOARD] Aktualisierung fehlgeschlagen: {e}")

    actions_any = any(r.get("actions_taken") for r in pt_results)
    if not (actions_any or uncovered or refreshed or dashboard_error):
        print("[DAILY] Keine Aktion noetig — keine Mail.")
        return 0

    send_email(build_mail(pt_results, uncovered, refreshed, dry_run, dashboard_error),
               subject_suffix="Depot-Alert")
    print("[DAILY] Mail verschickt.")
    return 0


def _refresh_dashboard(journal_data: dict, portfolio: dict) -> None:
    from pathlib import Path
    import portfolio_performance
    from report_builder import build_index_page

    trade_journal.build_and_save_html(journal_data)

    port_history = alpaca_client.get_portfolio_history()
    portfolio_performance.build_and_save(port_history, live_portfolio=portfolio)

    docs_reports_dir = Path("docs/reports")
    Path("docs/index.html").write_text(
        build_index_page(docs_reports_dir, "https://weekly-market-condition.pages.dev"),
        encoding="utf-8",
    )
    print("[DASHBOARD] Depot-Equity, Performance-Seite und Index aktualisiert.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Taeglicher Schutzlauf fuer das Aktiendepot")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur pruefen, keine Alpaca-Aenderungen")
    args = ap.parse_args()

    try:
        return run(dry_run=args.dry_run)
    except DailyAlertError as e:
        print(f"[DAILY] ❌ {e}")
        send_error_mail(str(e))
        return 1
    except Exception:
        tb = traceback.format_exc()
        print(f"[DAILY] ❌ Unerwarteter Fehler:\n{tb}")
        send_error_mail(tb)
        return 1


if __name__ == "__main__":
    sys.exit(main())
