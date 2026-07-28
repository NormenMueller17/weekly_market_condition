"""Setzt die Sektorfelder im Tradetagebuch auf die Pipeline-Taxonomie zurueck.

Hintergrund (2026-07-28): Bei Trades, deren Originalsignal aelter ist als die
vorhandenen signals_meta-Dateien, steht im Journal ein INDUSTRIE- statt eines
Sektornamens — "Semiconductors" statt "Technology", "Medical specialties" statt
"Healthcare", "Pharmaceuticals: other" statt "Healthcare". Die signals_meta
selbst sind sauber; nur das Journal ist verschmutzt.

Das war lange kosmetisch. Seit der Sektor-Cap die schon gehaltenen Positionen
mitzaehlt (`signal_generator._filter_sector_limit`), ist es das nicht mehr:
`main._journal_sector` dient als Rueckfall, wenn eine offene Position nicht in
`leaders` steht. Zaehlt AVNS dann als "Medical specialties" und NTRA als
"Healthcare", sind zwei Healthcare-Positionen zwei verschiedene Koerbe und der
Cap greift zu spaet.

Quelle der Wahrheit ist dieselbe wie in der Pipeline: yfinance `info["sector"]`
ueber `fetch_quote_data.batch_fetch_quote_data` — nicht eine handgepflegte
Industrie-zu-Sektor-Tabelle, die beim naechsten neuen Titel wieder falsch waere.

  python repair_journal_sectors.py            # Vorschau, schreibt nichts
  python repair_journal_sectors.py --apply    # schreibt trades.json + trades.html
"""
from __future__ import annotations

import argparse
import sys

import trade_journal
from fetch_quote_data import batch_fetch_quote_data

# Sektoren, die yfinance tatsaechlich vergibt. Alles andere im Journal ist ein
# Industrie-Name und damit verdaechtig.
GICS_SEKTOREN = {
    "Basic Materials", "Communication Services", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Financial Services", "Healthcare",
    "Industrials", "Real Estate", "Technology", "Utilities",
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Aenderungen schreiben (ohne Flag nur Vorschau)")
    ap.add_argument("--alle", action="store_true",
                    help="alle Trades neu setzen, nicht nur die verdaechtigen")
    args = ap.parse_args()

    data = trade_journal.load()
    trades = data.get("open", []) + data.get("closed", [])
    if not trades:
        print("[SEKTOR] Journal ist leer.")
        return 0

    verdaechtig = sorted({
        t["symbol"] for t in trades
        if t.get("symbol") and (
            args.alle or (t.get("sector") or "") not in GICS_SEKTOREN
        )
    })
    if not verdaechtig:
        print("[SEKTOR] Alle Sektorfelder sind schon Pipeline-Taxonomie — "
              "nichts zu tun.")
        return 0

    print(f"[SEKTOR] {len(verdaechtig)} Symbol(e) zu pruefen: "
          f"{', '.join(verdaechtig)}")
    quotes = batch_fetch_quote_data(verdaechtig)

    aenderungen: list[tuple[str, str, str]] = []
    ungeklaert: list[str] = []
    for sym in verdaechtig:
        neu = (quotes.get(sym) or {}).get("Sector")
        if not neu or str(neu).strip().lower() in ("", "nan", "none", "n/a"):
            ungeklaert.append(sym)
            continue
        neu = str(neu).strip()
        if neu not in GICS_SEKTOREN:
            # yfinance liefert etwas Unerwartetes — nicht stillschweigend
            # uebernehmen, sondern melden.
            print(f"  [?] {sym}: yfinance liefert '{neu}', kein bekannter "
                  f"GICS-Sektor — uebersprungen")
            ungeklaert.append(sym)
            continue
        alt = next((t.get("sector") or "" for t in trades
                    if t.get("symbol") == sym), "")
        if alt != neu:
            aenderungen.append((sym, alt, neu))

    if ungeklaert:
        print(f"[SEKTOR] ohne verwertbaren Sektor: {', '.join(ungeklaert)}")

    if not aenderungen:
        print("[SEKTOR] Keine Aenderung notwendig.")
        return 0

    print(f"\n{'Symbol':8}{'alt':34}{'neu':22}")
    print("-" * 64)
    for sym, alt, neu in aenderungen:
        print(f"{sym:8}{alt or '(leer)':34}{neu:22}")

    if not args.apply:
        print(f"\n{len(aenderungen)} Aenderung(en) — Vorschau, nichts geschrieben. "
              f"Mit --apply anwenden.")
        return 0

    neu_map = {sym: neu for sym, _, neu in aenderungen}
    n = 0
    for bucket in ("open", "closed"):
        for t in data.get(bucket, []):
            if t.get("symbol") in neu_map:
                t["sector"] = neu_map[t["symbol"]]
                n += 1
    trade_journal.save(data)
    trade_journal.build_and_save_html(data)
    print(f"\n[SEKTOR] {n} Journal-Eintrag/-Eintraege aktualisiert, "
          f"trades.json und trades.html neu geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
