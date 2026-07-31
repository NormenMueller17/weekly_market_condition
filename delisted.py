"""delisted.py — Register broker-seitig toter Positionen.

Eine Position kann im Alpaca-Konto liegen bleiben, obwohl das Wertpapier nicht
mehr existiert: bei Delisting durch Übernahme setzt Alpaca das Asset auf
inaktiv, storniert die ruhenden Orders und friert den letzten Kurs ein. Die
Stücke bleiben im Bestand, sind aber weder handelbar noch absicherbar — auch
nicht von Hand über die Weboberfläche.

Anlass (2026-07-30): AVNS, delisted am 2026-07-27 nach der Übernahme durch
American Industrial Partners. 608 Stück, eingefroren bei $24,99, seit dem
Delisting ohne Stop-Order. Der Coverage-Check versuchte jeden Werktag erneut
einen Stop zu setzen und bekam "not active" zurück, `trade_journal.sync` hätte
den Titel nach jedem Abschluss wieder als offene Position eingetragen.

Warum ein eigenes Register und nicht dead_tickers.py: dort geht es um Symbole,
für die YAHOO keine Kurse mehr liefert, und der Ausschluss greift ausdrücklich
nur im Screening-Universum — offene Positionen bleiben dort unberührt ("was wir
halten, wird weiter bewertet"). Hier ist es umgekehrt: der BROKER kennt das
Papier nicht mehr, und genau die gehaltene Position ist betroffen.

Drei Stellen lesen dieses Register:
  * trade_journal.sync            — traegt den Titel nicht erneut als offen ein
  * alpaca_client.check_sell_order_coverage — versucht keinen Stop mehr
  * mail_report                   — weist die eingefrorene Bewertung im Brief aus

  python delisted.py --list
  python delisted.py --add AVNS --datum 2026-07-27 --grund "Uebernahme" --kurs 24.99
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REGISTRY = Path("docs") / "data" / "delisted.json"


def load() -> dict:
    if not REGISTRY.exists():
        return {}
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # Fail-open: ein kaputtes Register darf den Wochenlauf nicht kippen,
        # aber es muss sichtbar sein.
        print(f"[DELISTED] ⚠️  {REGISTRY} nicht lesbar: {e}")
        return {}


def save(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def symbols() -> set:
    return set(load())


def is_delisted(symbol: str) -> bool:
    return symbol in load()


def add(symbol: str, delisted_date: str, grund: str,
        letzter_kurs: float | None = None, menge: float | None = None) -> dict:
    data = load()
    data[symbol] = {
        "delisted_date": delisted_date,
        "grund":         grund,
        "letzter_kurs":  letzter_kurs,
        "menge":         menge,
        "erfasst_am":    date.today().isoformat(),
    }
    save(data)
    return data


def eingefrorener_wert(positions: list) -> float:
    """Marktwert der Positionen, die auf einem eingefrorenen Kurs stehen.

    Die Equity des Kontos enthaelt diese Stuecke weiter — der Broker bewertet
    sie mit dem letzten bekannten Kurs. Fuer den Vergleich gegen den S&P 500
    ist das ein toter Block, der weder steigt noch faellt.
    """
    tot = symbols()
    if not tot:
        return 0.0
    summe = 0.0
    for p in positions or []:
        if p.get("symbol") in tot:
            try:
                summe += float(p.get("market_value") or 0)
            except (TypeError, ValueError):
                pass
    return summe


def main() -> int:
    ap = argparse.ArgumentParser(description="Register broker-seitig toter Positionen")
    ap.add_argument("--list", action="store_true", help="Register anzeigen")
    ap.add_argument("--add", metavar="SYMBOL", help="Symbol eintragen")
    ap.add_argument("--datum", help="Delisting-Datum (YYYY-MM-DD)")
    ap.add_argument("--grund", help="Grund, z. B. 'Uebernahme durch X'")
    ap.add_argument("--kurs", type=float, help="letzter bekannter Kurs")
    ap.add_argument("--menge", type=float, help="gehaltene Stueckzahl")
    args = ap.parse_args()

    if args.add:
        if not (args.datum and args.grund):
            print("--add braucht --datum und --grund")
            return 1
        add(args.add, args.datum, args.grund, args.kurs, args.menge)
        print(f"[DELISTED] {args.add} eingetragen.")

    reg = load()
    if not reg:
        print("[DELISTED] Register leer.")
        return 0
    for sym, e in sorted(reg.items()):
        kurs = f"${e['letzter_kurs']:.2f}" if e.get("letzter_kurs") else "–"
        print(f"  {sym:6} {e['delisted_date']}  {kurs:>9}  {e.get('grund','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
