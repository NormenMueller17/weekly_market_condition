"""Vergleicht den unterstellten Edge in rules.json mit dem realisierten.

Anlass (2026-07-28): `rules.json` fuehrt einen Sizing-Block mit `win_rate 0.59`
und `win_loss_ratio 4.04` — Blueprint-Werte, nie an der eigenen Historie
geprueft. Real liegt die Trefferquote bei 2 von 17.

Wichtig zur Einordnung, damit die Zahlen unten nicht ueberinterpretiert werden:

  * Die Kelly-Parameter werden von KEINEM Code gelesen. Das Sizing ist
    risk-first (`position = max_risk_per_trade_pct / stop_pct`, gedeckelt auf
    `max_position_pct`). Ein falscher `win_rate` hat also nie eine Order
    dimensioniert — er hat nur die Wahl von `max_risk_per_trade_pct`
    plausibilisiert, und das ist der eigentliche Hebel.
  * Bei n=17 ist eine Trefferquote statistisch kaum bestimmbar. Das Skript
    weist darum das Wilson-Intervall aus. Wer bei 2/17 die Kelly-Formel
    woertlich nimmt, bekommt "gar nicht handeln" — das ist keine Erkenntnis
    ueber den Edge, sondern ueber die Stichprobengroesse.
  * Die Historie stammt aus der Zeit VOR dem Stop-Floor und dem
    Wiedereinstieg. Sie misst ein System, das es so nicht mehr gibt.

Deshalb schreibt dieses Skript nichts automatisch in rules.json zurueck.
Es liefert die Zahlen; die Risikoentscheidung trifft der Mensch.

  python kelly_check.py
  python kelly_check.py --min-trades 30   # Schwelle fuer eine belastbare Aussage
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

import trade_journal


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson-Konfidenzintervall fuer einen Anteil — bei kleinem n deutlich
    ehrlicher als die Normalapproximation, und es entartet nicht bei 0 oder 1.
    """
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    d = 1 + z * z / n
    zentrum = (p + z * z / (2 * n)) / d
    rand = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, zentrum - rand), min(1.0, zentrum + rand))


def kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
    """Kelly-Anteil f* = W - (1-W)/R. Negativ = kein Einsatz gerechtfertigt."""
    if win_loss_ratio <= 0:
        return 0.0
    return win_rate - (1.0 - win_rate) / win_loss_ratio


def r_multiples(closed: list[dict]) -> list[float]:
    """Ergebnis je Trade in Risikoeinheiten (R).

    Unter risk-first-Sizing ist R die natuerliche Einheit: Jede Position wird so
    dimensioniert, dass der Weg bis zum Initial-Stop gleich viel Kapital kostet.
    Prozentrenditen sind dagegen nicht vergleichbar, weil ein weiter Stop eine
    kleinere Position bedeutet.
    """
    out = []
    for t in closed:
        plpc = t.get("realized_plpc")
        entry = t.get("entry_price")
        stop = t.get("initial_stop")
        if plpc is None or not entry or not stop or entry <= 0:
            continue
        risiko_pct = (entry - stop) / entry * 100.0
        if risiko_pct <= 0:
            continue
        out.append(plpc / risiko_pct)
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-trades", type=int, default=30,
                    help="ab wie vielen Trades die Schaetzung als belastbar gilt")
    args = ap.parse_args()

    rules = json.loads(Path("rules.json").read_text(encoding="utf-8"))
    sizing = rules.get("sizing", {})
    data = trade_journal.load()
    closed = [t for t in data.get("closed", [])
              if t.get("realized_plpc") is not None]

    if not closed:
        print("[KELLY] Keine geschlossenen Trades im Journal.")
        return 0

    pl = [t["realized_plpc"] for t in closed]
    wins = [p for p in pl if p > 0]
    losses = [p for p in pl if p <= 0]
    n = len(pl)
    wr = len(wins) / n
    lo, hi = wilson(len(wins), n)

    avg_win = st.mean(wins) if wins else 0.0
    avg_loss = abs(st.mean(losses)) if losses else 0.0
    wlr = avg_win / avg_loss if avg_loss else float("inf")

    print(f"\n{'':32}{'unterstellt':>14}{'realisiert':>14}")
    print("-" * 60)
    print(f"{'Trefferquote':32}{sizing.get('win_rate', 0):>13.0%}{wr:>14.1%}")
    print(f"{'Gewinn/Verlust-Verhaeltnis':32}"
          f"{sizing.get('win_loss_ratio', 0):>13.2f}{wlr:>14.2f}")
    print(f"{'Trades':32}{'—':>14}{n:>14d}")
    print(f"\n95-%-Intervall der Trefferquote (Wilson): {lo:.1%} bis {hi:.1%}")

    f_soll = kelly_fraction(sizing.get("win_rate", 0.0),
                            sizing.get("win_loss_ratio", 0.0))
    f_ist = kelly_fraction(wr, wlr if math.isfinite(wlr) else 0.0)
    frak = sizing.get("kelly_fraction", 0.33)
    print(f"\nKelly-Anteil f*        unterstellt {f_soll:+.3f}   "
          f"realisiert {f_ist:+.3f}")
    print(f"davon {frak:.0%} (fractional)  unterstellt {f_soll * frak:+.3f}   "
          f"realisiert {f_ist * frak:+.3f}")

    rs = r_multiples(closed)
    if rs:
        pos = sum(1 for r in rs if r > 0)
        print(f"\nErgebnis in Risikoeinheiten (n={len(rs)}): "
              f"Median {st.median(rs):+.2f} R   Mittel {st.mean(rs):+.2f} R   "
              f"positiv {pos}/{len(rs)}")
        print("  Erwartungswert je eingesetzter Risikoeinheit: "
              f"{st.mean(rs):+.2f} R")

    print("\n" + "=" * 60)
    if n < args.min_trades:
        print(f"BEFUND: {n} Trades sind zu wenig fuer eine Edge-Schaetzung.")
        print(f"Das Intervall {lo:.0%}–{hi:.0%} laesst alles offen zwischen "
              f"'kein Edge' und 'brauchbar'.")
        print("Die Historie stammt zudem aus der Zeit vor Stop-Floor und")
        print("Wiedereinstieg — sie misst ein System, das es nicht mehr gibt.")
        print(f"\nEMPFEHLUNG: max_risk_per_trade_pct "
              f"({sizing.get('max_risk_per_trade_pct')} %) unveraendert lassen "
              f"und ab {args.min_trades} Trades erneut messen.")
    elif f_ist <= 0:
        print("BEFUND: Der realisierte Edge ist nicht positiv. Kelly wuerde")
        print("keinen Einsatz rechtfertigen. Risiko je Trade reduzieren oder")
        print("die Titelauswahl ueberarbeiten, bevor mehr Kapital fliesst.")
    else:
        empf = f_ist * frak * 100
        print(f"BEFUND: Realisierter fractional-Kelly-Einsatz {empf:.2f} % "
              f"gegen aktuell {sizing.get('max_risk_per_trade_pct')} % je Trade.")

    print("\nHinweis: Die Kelly-Felder in rules.json steuern nichts — das Sizing")
    print("ist risk-first. Sie dienen der Plausibilisierung von")
    print("max_risk_per_trade_pct, und genau dafuer ist dieser Vergleich da.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
