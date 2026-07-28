"""Selbstpflegende Liste von Symbolen, für die Yahoo keine Daten mehr liefert.

Anlass (2026-07-28): Der Wochenlauf baute nur 2689 von 2806 Zeitreihen — 117
Symbole fielen aus, darunter BK, MMC, HOLX, EXAS, CMA, SNV, PSTG. Das Log meldet
sie als "wahrscheinlich de-listed" und geht still darüber hinweg.

Geprüft und widerlegt wurden dabei zwei naheliegende Verdachte: Es liegt weder an
der yfinance-Version (Yahoos Chart-API antwortet direkt mit HTTP 404) noch am
HTTP-Cache (auch ohne Cache reproduzierbar). Yahoos Such-Endpunkt kennt die
Symbole überhaupt nicht mehr, auch nicht unter einem Nachfolgernamen. Mehrere
davon sind reale Übernahmen — Comerica, CyberArk, Synovus. Die Universumsdatei
stammt aus 11/2025 und altert; genau diese Alterung ist die eigentliche Ursache.

Die bisherige Antwort darauf war `data_sources.TICKER_BLACKLIST`: eine
handgepflegte Menge mit 14 Einträgen, während jede Woche 117 Symbole scheitern.
Das skaliert nicht und wurde nie nachgepflegt.

Dieses Modul führt stattdessen Buch. Kernpunkte:

  * Ein Symbol wird erst nach `min_fails` AUFEINANDERFOLGENDEN Fehlläufen
    ausgeschlossen (Vorgabe 3, also drei Wochen). Ein einzelner Yahoo-Ausfall
    darf das Universum nicht dauerhaft schrumpfen.
  * Jeder Erfolg setzt den Zähler zurück. Kommt ein Symbol zurück, ist es sofort
    wieder dabei.
  * Der Ausschluss greift nur im Screening-Universum. Offene Positionen werden
    ohnehin einzeln abgefragt (exit_manager, trade_journal) und sind davon nicht
    berührt — was wir halten, wird weiter bewertet.

  * Scheitert mehr als MAX_FAIL_SHARE eines Laufs, wird gar nichts gebucht.
    Ein Ausfall auf Yahoo-Seite darf das Universum nicht stilllegen.
  * Offene Positionen werden nie ausgeschlossen.

Die Registry liegt unter docs/data/, weil sie CI-Läufe überdauern muss: Der
Workflow committet dieses Verzeichnis zurück ins Repo. Gefüllt wird sie
ausschließlich vom Wochenlauf — ein separates Scan-Werkzeug gab es kurzzeitig
und ist wieder entfernt worden: Es holte dieselben Daten in schneller Folge und
wurde dabei von Yahoo gedrosselt, was zu 1572 falschen Einträgen führte. Der
Wochenlauf holt die Daten ohnehin und tut es langsam genug.

  python dead_tickers.py --list             # Registry anzeigen
  python dead_tickers.py --revive BK MMC    # Symbole manuell rehabilitieren
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

REGISTRY = Path("docs/data/dead_tickers.json")
MIN_FAILS = 3

# Scheitert mehr als dieser Anteil eines Laufs, ist das ein Ausfall oder eine
# Drosselung — keine Massen-Delistings. Dann wird NICHTS gebucht.
#
# Die Schwelle stammt aus einem Fehlschlag beim Bau dieses Moduls: Ein
# Komplett-Scan des Universums in schneller Folge lieferte 1572 von 2806
# Symbolen als "ohne Daten" (56 %), darunter zwei offene Positionen. Der
# reguläre Wochenlauf, der dieselben Daten langsamer holt, hatte 117 (4,2 %).
# Yahoo drosselt, und ohne diese Schranke hätte ein einziger solcher Lauf das
# halbe Universum stillgelegt.
MAX_FAIL_SHARE = 0.20

# Unterhalb dieser Universumsgröße greift die Anteilsschranke nicht: Bei den
# Mini-CSVs im Repo (…_mini.csv, …_test_3.csv) wäre ein einzelner Ausfall
# zweistellig prozentual und würde jede Buchung blockieren.
MIN_UNIVERSUM = 100


def _heute() -> str:
    return datetime.date.today().isoformat()


def load(path: Path = REGISTRY) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("tickers"), dict):
            return d
    except Exception:
        pass
    return {"updated": "", "tickers": {}}


def save(data: dict, path: Path = REGISTRY) -> None:
    data["updated"] = _heute()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                               sort_keys=True), encoding="utf-8")


def gehaltene_positionen() -> set[str]:
    """Symbole offener Positionen — werden NIE ausgeschlossen.

    Was wir halten, muss weiter bewertet werden. Der Screening-Pfad ist zwar
    nicht derselbe wie der Exit-Pfad (exit_manager fragt Positionen einzeln ab),
    aber diese Garantie soll nicht davon abhängen, dass das so bleibt.
    """
    try:
        import trade_journal
        return {p.get("symbol") for p in trade_journal.load().get("open", [])
                if p.get("symbol")}
    except Exception:
        return set()


def excluded(data: dict | None = None, min_fails: int = MIN_FAILS) -> set[str]:
    """Symbole, die `min_fails` Läufe in Folge keine Daten geliefert haben."""
    d = data if data is not None else load()
    aus = {t for t, e in d.get("tickers", {}).items()
           if int(e.get("fails", 0)) >= min_fails}
    return aus - gehaltene_positionen()


def record(angefragt: set[str], erfolgreich: set[str],
           data: dict | None = None,
           max_fail_share: float = MAX_FAIL_SHARE,
           min_universum: int = MIN_UNIVERSUM) -> dict:
    """Fehlläufe hochzählen, Erfolge zurücksetzen. Gibt die Registry zurück.

    `angefragt` muss die Menge sein, die WIRKLICH abgefragt wurde — sonst
    zählen bereits ausgeschlossene Symbole weiter hoch und können nie
    zurückkommen. Deshalb nimmt der Aufrufer die gefilterte Liste.

    Scheitert mehr als `max_fail_share` des Laufs, wird NICHTS gebucht: Das ist
    dann ein Ausfall oder eine Drosselung auf Yahoo-Seite, und ein einziger
    solcher Lauf darf das Universum nicht stilllegen. Siehe MAX_FAIL_SHARE.
    """
    d = data if data is not None else load()
    tickers = d.setdefault("tickers", {})

    if not angefragt:
        return d

    fehlend = angefragt - erfolgreich
    anteil = len(fehlend) / len(angefragt)
    if len(angefragt) >= min_universum and anteil > max_fail_share:
        print(f"[DEAD] {len(fehlend)} von {len(angefragt)} Symbolen ohne Daten "
              f"({anteil:.0%}) — über der Schwelle von {max_fail_share:.0%}. "
              f"Das ist ein Datenausfall, keine Delistings: nichts gebucht.")
        return d

    for t in sorted(angefragt):
        if t in erfolgreich:
            # Erfolg: Eintrag verschwindet ganz, damit die Registry nicht
            # unbegrenzt wächst.
            tickers.pop(t, None)
            continue
        e = tickers.setdefault(t, {"fails": 0, "zuerst": _heute(), "zuletzt": ""})
        e["fails"] = int(e.get("fails", 0)) + 1
        e["zuletzt"] = _heute()
    return d


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="Registry anzeigen")
    ap.add_argument("--revive", nargs="+", metavar="TICKER",
                    help="Symbole aus der Registry entfernen")
    ap.add_argument("--min-fails", type=int, default=MIN_FAILS)
    args = ap.parse_args()

    d = load()

    if args.revive:
        weg = [t for t in args.revive if d["tickers"].pop(t, None) is not None]
        save(d)
        print(f"[DEAD] entfernt: {', '.join(weg) if weg else '(nichts gefunden)'}")
        return 0

    if args.list or not args.revive:
        tk = d.get("tickers", {})
        aus = excluded(d, args.min_fails)
        print(f"\nRegistry: {len(tk)} Symbole beobachtet, "
              f"{len(aus)} ab {args.min_fails} Fehlläufen ausgeschlossen")
        print(f"Stand: {d.get('updated') or '—'}\n")
        if tk:
            print(f"{'Symbol':10}{'Fehl':>6}{'zuerst':>13}{'zuletzt':>13}  Status")
            print("-" * 60)
            for t, e in sorted(tk.items(), key=lambda x: (-x[1].get("fails", 0), x[0])):
                st = "AUSGESCHLOSSEN" if t in aus else "beobachtet"
                print(f"{t:10}{e.get('fails', 0):>6}{e.get('zuerst', '—'):>13}"
                      f"{e.get('zuletzt', '—'):>13}  {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
