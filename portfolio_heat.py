"""portfolio_heat.py — Beobachtungsmodus fuer eine Obergrenze des offenen Portfoliorisikos.

Frage (2026-09-20): Jeder Trade hat ein Risikobudget von 1,5 %, aber nichts
begrenzt die SUMME der offenen Positionen. Bei 12 Positionen stehen bis zu 18 %
der Equity gleichzeitig am Stop; gemessen wurden bis zu 15 % (29.06., 11
Positionen), und die Wochenverluste vom Juni (-13,1 %, -9,9 %) lagen in der
Groessenordnung des gesamten offenen Risikos.

Dieses Modul STEUERT NICHTS. Es berechnet vor der Orderplatzierung des
Wochenlaufs,

  * wie hoch das offene Risiko bis zu den aktuellen Stops ist ("Heat", in % der
    Live-Equity), und
  * was eine Obergrenze mit den geplanten Kaeufen getan haette (voll / gekuerzt /
    gesperrt) -- fuer mehrere Grenzwerte parallel,

und haengt beides als eine Zeile an docs/data/heat_shadow.jsonl. Nach einigen
Wochen liegt damit eine echte Datenbasis vor, ob und in welcher Hoehe eine
Grenze aktiviert werden sollte. Fehler hier duerfen den Lauf nie kippen.

Messgroesse: (aktueller Kurs - aktueller Stop) * Stueckzahl je Position. Gewinner
zaehlen also mit dem Betrag, den sie bis zum Stop zurueckgeben koennten, nicht
nur mit dem Risiko ab Einstand. Broker-seitig tote Positionen (delisted.py) sind
nicht handelbar und nicht absicherbar und zaehlen nicht mit.

  python portfolio_heat.py --report      # Verlauf aus heat_shadow.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable, Optional

SHADOW_FILE = Path("docs") / "data" / "heat_shadow.jsonl"

# Getestete Grenzen in % der Equity. Die letzte Variante koppelt die Grenze an
# den Kauffilter: bullisch 15 %, sonst 8 %.
FLAT_CAPS      = (10.0, 15.0)
MARKET_CAP_BULL = 15.0
MARKET_CAP_BEAR = 8.0


def open_risk(positions: Iterable[dict], journal_open: Iterable[dict],
              equity: float, frozen: Iterable[str] = ()) -> dict:
    """Offenes Risiko bis zu den aktuellen Stops.

    positions    -- alpaca_client.get_portfolio()["positions"]
    journal_open -- trade_journal-Eintraege (Stops stehen dort, nicht bei Alpaca)
    frozen       -- broker-seitig tote Symbole (delisted.symbols())

    Positionen ohne bekannten Stop werden NICHT als risikolos verbucht, sondern
    unter `ohne_stop` gemeldet -- ihr Risiko ist unbekannt, nicht null.
    """
    stops = {t["symbol"]: (t.get("current_stop") or t.get("initial_stop"))
             for t in journal_open}
    frozen = set(frozen)
    rows, ohne_stop, ausgenommen = [], [], []
    for p in positions:
        sym = p["symbol"]
        if sym in frozen:
            ausgenommen.append(sym)
            continue
        stop = stops.get(sym)
        price, qty = float(p["current_price"]), float(p["qty"])
        if not stop or stop <= 0:
            ohne_stop.append(sym)
            continue
        risk = max(0.0, (price - float(stop)) * qty)
        rows.append({"symbol": sym, "qty": qty, "price": round(price, 2),
                     "stop": round(float(stop), 2), "risk_usd": round(risk, 2),
                     "risk_pct": round(risk / equity * 100, 3) if equity else None})
    total = sum(r["risk_usd"] for r in rows)
    return {"rows": rows, "total_usd": round(total, 2),
            "total_pct": round(total / equity * 100, 2) if equity else None,
            "ohne_stop": ohne_stop, "ausgenommen": ausgenommen}


def simulate(picks: list[dict], heat_pct: float, cap_pct: float) -> dict:
    """Was eine Obergrenze `cap_pct` mit den geplanten Kaeufen taete.

    picks    -- [{"ticker", "risk_pct"}] in Rang-Reihenfolge; risk_pct ist das
                Risiko des Kaufs in % der Equity (Position x Stop-Abstand)
    heat_pct -- bereits offenes Risiko in % der Equity

    Reihenfolge der Kaeufe = Rang. Ein Kauf, der nicht mehr voll unter die Grenze
    passt, wird auf den verbleibenden Spielraum gekuerzt; ist keiner mehr da,
    gesperrt.
    """
    heat = heat_pct
    out = []
    for pk in picks:
        risk = float(pk["risk_pct"])
        room = cap_pct - heat
        if risk <= 0:
            out.append({"ticker": pk["ticker"], "status": "voll", "faktor": 1.0})
        elif room >= risk:
            heat += risk
            out.append({"ticker": pk["ticker"], "status": "voll", "faktor": 1.0})
        elif room > 0:
            heat += room
            out.append({"ticker": pk["ticker"], "status": "gekuerzt",
                        "faktor": round(room / risk, 2)})
        else:
            out.append({"ticker": pk["ticker"], "status": "gesperrt", "faktor": 0.0})
    return {"cap_pct": cap_pct, "heat_nach_pct": round(heat, 2), "kaeufe": out}


def build_record(report_date: str, equity: float, market_bullish: bool,
                 ampel_score: Optional[int], risk: dict, picks: list[dict]) -> dict:
    heat = risk["total_pct"] or 0.0
    varianten = {f"{c:g}": simulate(picks, heat, c) for c in FLAT_CAPS}
    markt_cap = MARKET_CAP_BULL if market_bullish else MARKET_CAP_BEAR
    varianten[f"markt_{MARKET_CAP_BULL:g}/{MARKET_CAP_BEAR:g}"] = simulate(picks, heat, markt_cap)
    return {
        "datum":          report_date,
        "equity":         round(equity, 2),
        "market_bullish": bool(market_bullish),
        "ampel_score":    ampel_score,
        "heat_pct":       risk["total_pct"],
        "heat_usd":       risk["total_usd"],
        "positionen":     risk["rows"],
        "ohne_stop":      risk["ohne_stop"],
        "ausgenommen":    risk["ausgenommen"],
        "geplant":        [{"ticker": p["ticker"], "risk_pct": round(p["risk_pct"], 3)} for p in picks],
        "geplant_pct":    round(sum(p["risk_pct"] for p in picks), 2),
        "varianten":      varianten,
    }


def append_record(rec: dict, path: Path = SHADOW_FILE) -> None:
    """Haengt eine Zeile an. Eine Zeile je Datum: ein Neulauf am selben Tag ersetzt sie."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                if json.loads(ln).get("datum") == rec["datum"]:
                    continue
            except ValueError:
                pass
            lines.append(ln)
    lines.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_shadow(alpaca_portfolio: Optional[dict], journal_open: list, top_picks: list,
               equity: float, market_bullish: bool, ampel_score: Optional[int],
               report_date: str, frozen: Iterable[str] = (),
               path: Path = SHADOW_FILE, persist: bool = True) -> Optional[dict]:
    """Berechnen, protokollieren, im Log ausweisen. Gibt den Datensatz zurueck.

    Kein Portfolio -> kein Datensatz (ein Heat ohne Bestand waere 0 % und damit
    eine erfundene Zahl).
    """
    if not alpaca_portfolio or not equity:
        print("[HEAT] Kein Portfolio oder keine Equity — Beobachtungsmodus übersprungen")
        return None
    risk = open_risk(alpaca_portfolio.get("positions", []), journal_open, equity, frozen)
    picks = [{"ticker": s.ticker, "risk_pct": float(s.risk_on_equity_pct) * 100}
             for s in top_picks]
    rec = build_record(report_date, equity, market_bullish, ampel_score, risk, picks)
    if persist:
        append_record(rec, path)
    else:
        print("[HEAT] Testmodus — Datensatz wird nicht in heat_shadow.jsonl geschrieben")

    print(f"[HEAT] Offenes Risiko bis zu den Stops: ${risk['total_usd']:,.0f} = "
          f"{risk['total_pct']:.1f} % der Equity ({len(risk['rows'])} Positionen)"
          + (f", + {rec['geplant_pct']:.1f} % durch {len(picks)} geplante Käufe" if picks else ""))
    if risk["ohne_stop"]:
        print(f"[HEAT] ⚠️  Ohne bekannten Stop, Risiko nicht enthalten: "
              f"{', '.join(risk['ohne_stop'])}")
    for name, v in rec["varianten"].items():
        if not picks:
            break
        teile = ", ".join(f"{k['ticker']} {k['status']}" +
                          (f" ({k['faktor']:.0%})" if k["status"] == "gekuerzt" else "")
                          for k in v["kaeufe"])
        print(f"[HEAT]   Grenze {name} %: {teile} → Heat danach {v['heat_nach_pct']:.1f} %")
    return rec


def report(path: Path = SHADOW_FILE) -> int:
    if not path.exists():
        print(f"Noch keine Daten ({path}).")
        return 1
    recs = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            try:
                recs.append(json.loads(ln))
            except ValueError:
                continue
    if not recs:
        print("Keine lesbaren Datensätze.")
        return 1
    namen = list(recs[-1]["varianten"].keys())
    print(f"{'Datum':10s}  {'Heat':>6s}  {'+geplant':>8s}  {'Bull':>4s}  {'Ampel':>5s}  "
          + "  ".join(f"{'Grenze ' + n:>22s}" for n in namen))
    for r in recs:
        zellen = []
        for n in namen:
            v = r["varianten"].get(n)
            if not v:
                zellen.append(f"{'–':>22s}")
                continue
            k = v["kaeufe"]
            zellen.append(f"{sum(1 for x in k if x['status']=='voll')}v/"
                          f"{sum(1 for x in k if x['status']=='gekuerzt')}g/"
                          f"{sum(1 for x in k if x['status']=='gesperrt')}s → {v['heat_nach_pct']:4.1f} %".rjust(22))
        print(f"{r['datum']:10s}  {r['heat_pct'] or 0:5.1f}%  {r['geplant_pct']:7.1f}%  "
              f"{'ja' if r['market_bullish'] else 'nein':>4s}  {str(r['ampel_score'] if r['ampel_score'] is not None else '–'):>5s}  "
              + "  ".join(zellen))
    print("\nv = voll, g = gekürzt, s = gesperrt (je geplantem Kauf); nach dem Pfeil: Heat nach den Käufen.")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.exit(report())
    print(__doc__)
