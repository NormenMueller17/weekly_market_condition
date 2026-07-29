"""mail_report.py — der woechentliche Boersenbrief als E-Mail.

Getrennt von `report_builder.build_html_report`, das den WEB-Report baut.

Warum getrennt
--------------
Bis 2026-07-29 wurde dasselbe HTML zweimal gebaut und einmal davon verschickt —
unterschieden nur durch `pages_url`. Die Mail erbte damit Dinge, die in einem
Mailclient nicht funktionieren: die sortierbare Tabelle braucht JavaScript, die
Ampel-Erklaerung haengt an einem CSS-Hover-Tooltip, die Navigationsleiste ist
`position: sticky`. Nichts davon ueberlebt den Versand.

Dieses Modul baut deshalb bewusst anders:
  * keine Skripte, kein Hover, keine Fixierung — alles statisch
  * Stile inline am Element, nicht nur im <style>-Block (viele Clients werfen
    <style> weg)
  * Diagramme als Inline-SVG statt <canvas> oder externem Bild (externe Bilder
    werden ohnehin blockiert, bis der Empfaenger sie freigibt)
  * verdichtet statt vollstaendig: was ausfuehrlich sein soll, steht im
    Web-Report, auf den unten verlinkt wird

Aufbau des Briefs
-----------------
  1. Marktampel mit allen sechs Kriterien im Klartext
  2. Lagebericht — der eine Absatz, der sagt was los ist
  3. Depot gegen Vorwoche und gegen den S&P 500, mit Kurve
  4. Positionen im Detail — inklusive Abstand zum Stop in Prozent UND in R
  5. Kaufsignale bzw. die besten Kandidaten als Steckbrief
  6. Marktlage kompakt
"""
from __future__ import annotations

import datetime
from typing import Optional

# ── Stil ──────────────────────────────────────────────────────────────────────

BLAU     = "#003d99"
GRAU     = "#555"
GRUEN    = "#155724"
ROT      = "#721c24"
RAHMEN   = "#d0d5e8"
KOPFBG   = "#eef2fa"

_TD  = f"border:1px solid {RAHMEN};padding:.45em .8em;text-align:right;"
_TDL = f"border:1px solid {RAHMEN};padding:.45em .8em;text-align:left;"
_TH  = (f"border:1px solid {RAHMEN};padding:.45em .8em;text-align:right;"
        f"background:{KOPFBG};color:{BLAU};font-weight:600;white-space:nowrap;")
_THL = _TH.replace("text-align:right", "text-align:left")
_TABLE = "border-collapse:collapse;width:100%;font-size:.92em;margin-bottom:1.6em;"
_H2 = (f"color:{BLAU};font-size:1.05em;margin:1.8em 0 .6em;"
       f"border-bottom:2px solid {KOPFBG};padding-bottom:.3em;")


def _farbe(v: Optional[float]) -> str:
    if v is None:
        return GRAU
    return GRUEN if v > 0 else (ROT if v < 0 else GRAU)


def _pct(v: Optional[float], decimals: int = 2) -> str:
    if v is None:
        return "–"
    return f"{v:+.{decimals}f} %"


def _geld(v: Optional[float], plus: bool = False) -> str:
    if v is None:
        return "–"
    s = "+" if plus and v > 0 else ""
    return f"{s}${v:,.0f}"


# ── Kurve ─────────────────────────────────────────────────────────────────────

def equity_svg(labels: list, equity: list, spy: list,
               breite: int = 620, hoehe: int = 180) -> str:
    """Depot gegen S&P 500 als Inline-SVG, beide auf 100 normiert.

    Inline-SVG statt Bild: ein externes Bild wird von den meisten Mailclients
    blockiert, bis der Empfaenger es freigibt — die wichtigste Grafik des
    Briefs waere dann ein graues Kaestchen.
    """
    paare = [(e, s) for e, s in zip(equity, spy or []) if e and s]
    if len(paare) < 2:
        # Ohne Vergleichsreihe wenigstens die Depotkurve zeigen
        werte = [e for e in equity if e]
        if len(werte) < 2:
            return ""
        paare = [(e, None) for e in werte]

    e0 = paare[0][0]
    s0 = paare[0][1]
    eq_n  = [e / e0 * 100.0 for e, _ in paare]
    spy_n = [s / s0 * 100.0 for _, s in paare] if s0 else []

    alle = eq_n + spy_n
    lo, hi = min(alle), max(alle)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    pad_l, pad_r, pad_t, pad_b = 42, 8, 12, 22
    iw = breite - pad_l - pad_r
    ih = hoehe - pad_t - pad_b

    def punkte(reihe: list) -> str:
        n = len(reihe)
        if n < 2:
            return ""
        return " ".join(
            f"{pad_l + i / (n - 1) * iw:.1f},{pad_t + (hi - v) / (hi - lo) * ih:.1f}"
            for i, v in enumerate(reihe)
        )

    # Nulllinie (100 = Start) und Achsenbeschriftung
    y100 = pad_t + (hi - 100.0) / (hi - lo) * ih
    teile = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {breite} {hoehe}" '
        f'width="100%" height="{hoehe}" style="max-width:{breite}px;">',
        f'<rect x="0" y="0" width="{breite}" height="{hoehe}" fill="#fbfcff"/>',
        f'<line x1="{pad_l}" y1="{y100:.1f}" x2="{breite - pad_r}" y2="{y100:.1f}" '
        f'stroke="#c9d2e8" stroke-width="1" stroke-dasharray="3,3"/>',
        f'<text x="4" y="{y100 + 4:.1f}" font-size="10" fill="{GRAU}" '
        f'font-family="Arial">100</text>',
        f'<text x="4" y="{pad_t + 8}" font-size="10" fill="{GRAU}" '
        f'font-family="Arial">{hi:.0f}</text>',
        f'<text x="4" y="{pad_t + ih:.0f}" font-size="10" fill="{GRAU}" '
        f'font-family="Arial">{lo:.0f}</text>',
    ]
    if spy_n:
        teile.append(f'<polyline points="{punkte(spy_n)}" fill="none" '
                     f'stroke="#9aa7c7" stroke-width="1.6"/>')
    teile.append(f'<polyline points="{punkte(eq_n)}" fill="none" '
                 f'stroke="{BLAU}" stroke-width="2.2"/>')
    teile.append(
        f'<text x="{pad_l}" y="{hoehe - 6}" font-size="10" fill="{BLAU}" '
        f'font-family="Arial">■ Depot</text>'
    )
    if spy_n:
        teile.append(
            f'<text x="{pad_l + 60}" y="{hoehe - 6}" font-size="10" fill="#9aa7c7" '
            f'font-family="Arial">■ S&amp;P 500</text>'
        )
    teile.append("</svg>")
    return "".join(teile)


# ── Kennzahlen ────────────────────────────────────────────────────────────────

def handelsstart(history: Optional[dict]) -> Optional[str]:
    """Erster Tag, an dem das Depot sich ueberhaupt bewegt hat.

    Die Equity-Reihe beginnt mit dem Anlagedatum des Kontos, nicht mit dem
    ersten Trade — davor steht monatelang unveraendert der Startbetrag. Ohne
    diesen Schnitt vergleicht der Brief die wenigen Monate echten Handels gegen
    ein volles Indexjahr, und die Differenz ist frei erfunden. Konkret am
    2026-07-28: 183 flache Punkte vom 2025-07-29 bis zum ersten Trade am
    2026-04-21, gemeldet worden waeren -9,5 % gegen +17,6 %.
    """
    if not history:
        return None
    ts   = history.get("timestamps", [])
    eq   = history.get("equity", [])
    base = history.get("base_value")
    if base is None or not ts:
        return None
    for t, v in zip(ts, eq):
        if v is None or abs(v - base) <= 0.01:
            continue
        try:
            return datetime.datetime.utcfromtimestamp(int(t)).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    return None


def _wert_vor(labels: list, werte: list, stichtag: str):
    """Letzter Wert an oder vor `stichtag`. None, wenn die Reihe dort noch nicht beginnt."""
    treffer = None
    for lbl, v in zip(labels, werte):
        if v is None:
            continue
        if lbl <= stichtag:
            treffer = v
        else:
            break
    return treffer


def performance_block(labels: list, equity: list, spy: list) -> dict:
    """Depot- und SPY-Veraenderung ueber eine Woche und seit Start."""
    eq = [(l, v) for l, v in zip(labels, equity) if v]
    if not eq:
        return {}
    heute_lbl, heute_eq = eq[-1]
    vorwoche_lbl = (datetime.date.fromisoformat(heute_lbl)
                    - datetime.timedelta(days=7)).isoformat()

    eq_vor = _wert_vor(labels, equity, vorwoche_lbl)
    out = {
        "equity":        heute_eq,
        "start_equity":  eq[0][1],
        "depot_woche":   (heute_eq / eq_vor - 1) * 100 if eq_vor else None,
        "depot_start":   (heute_eq / eq[0][1] - 1) * 100 if eq[0][1] else None,
        "spy_woche":     None,
        "spy_start":     None,
    }
    if spy:
        spy_paare = [(l, v) for l, v in zip(labels, spy) if v]
        if spy_paare:
            spy_heute = spy_paare[-1][1]
            spy_erst  = spy_paare[0][1]
            spy_vor   = _wert_vor(labels, spy, vorwoche_lbl)
            out["spy_woche"] = (spy_heute / spy_vor - 1) * 100 if spy_vor else None
            out["spy_start"] = (spy_heute / spy_erst - 1) * 100 if spy_erst else None
    return out


def position_details(positions: list, journal_open: list) -> list:
    """Depotpositionen mit Stop-Abstand in Prozent UND in R.

    R ist das anfaengliche Risiko je Aktie (Einstand minus initialer Stop).
    Der Gewinn in R sagt, ob eine Position ihr eingesetztes Risiko verdient
    hat — eine Position bei +8 % mit engem Stop kann mehr geleistet haben als
    eine bei +15 % mit weitem. Die Zahl fehlte im alten Report vollstaendig.
    """
    j = {t["symbol"]: t for t in journal_open or []}
    heute = datetime.date.today()
    out = []
    for p in positions or []:
        sym = p["symbol"]
        t   = j.get(sym, {})
        einstand = float(t.get("entry_price") or p.get("avg_entry_price") or 0)
        kurs     = float(p.get("current_price") or 0)
        stop     = t.get("current_stop") or t.get("initial_stop")
        init     = t.get("initial_stop")

        r_einheit = (einstand - float(init)) if init and einstand else None
        gewinn_r  = ((kurs - einstand) / r_einheit) if r_einheit and r_einheit > 0 else None
        stop_abst = ((kurs - float(stop)) / kurs * 100) if stop and kurs else None

        tage = None
        if t.get("entry_date"):
            try:
                tage = (heute - datetime.date.fromisoformat(t["entry_date"])).days
            except ValueError:
                pass

        out.append({
            "symbol":     sym,
            "company":    t.get("company", ""),
            "sector":     t.get("sector", ""),
            "qty":        p.get("qty"),
            "einstand":   einstand,
            "kurs":       kurs,
            "gewinn_pct": p.get("unrealized_plpc"),
            "gewinn_usd": p.get("unrealized_pl"),
            "marktwert":  p.get("market_value"),
            "stop":       float(stop) if stop else None,
            "stop_abst":  stop_abst,
            "gewinn_r":   gewinn_r,
            "tage":       tage,
            "rs":         t.get("rs_score"),
            "ohne_stop":  stop is None,
        })
    out.sort(key=lambda d: (d["gewinn_pct"] is None, -(d["gewinn_pct"] or 0)))
    return out


# ── Lagebericht ───────────────────────────────────────────────────────────────

def lagebericht(ampel: dict, perf: dict, positionen: list,
                n_signale: int, breadth_jetzt, breadth_vor) -> str:
    """Der eine Absatz, der sagt was los ist — in ganzen Saetzen.

    Ersetzt das alte Einzeiler-Fazit. Alles hier ist aus den Zahlen abgeleitet,
    nichts geraten.
    """
    s = []
    score = ampel.get("score", 0)
    label = ampel.get("label", "Neutral")
    if label == "Bullish":
        s.append(f"Der Markt traegt: {score} von 6 Ampelkriterien sind erfuellt, "
                 f"Neueinstiege sind zulaessig.")
    elif label == "Defensiv":
        s.append(f"Der Markt ist defensiv: nur {score} von 6 Kriterien sind "
                 f"erfuellt — keine Neueinstiege.")
    else:
        s.append(f"Der Markt ist uneinheitlich: {score} von 6 Kriterien sind erfuellt. "
                 f"Nicht erfuellt: "
                 + ", ".join(k["name"] for k in ampel.get("criteria", []) if not k["met"])
                 + ".")

    if breadth_jetzt is not None and breadth_vor is not None:
        delta = breadth_jetzt - breadth_vor
        richtung = "verbessert" if delta > 1 else ("verschlechtert" if delta < -1 else "kaum veraendert")
        s.append(f"Die Marktbreite hat sich {richtung} "
                 f"({breadth_vor:.1f} % → {breadth_jetzt:.1f} % ueber der 10-Wochen-Linie).")

    dw, sw = perf.get("depot_woche"), perf.get("spy_woche")
    if dw is not None and sw is not None:
        diff = dw - sw
        wort = "besser als" if diff > 0.1 else ("schlechter als" if diff < -0.1 else "gleichauf mit")
        s.append(f"Das Depot liegt diese Woche bei {dw:+.2f} % und damit {wort} "
                 f"dem S&P 500 ({sw:+.2f} %).")
    elif dw is not None:
        s.append(f"Das Depot liegt diese Woche bei {dw:+.2f} %.")

    ds, ss = perf.get("depot_start"), perf.get("spy_start")
    if ds is not None and ss is not None:
        s.append(f"Seit Handelsstart stehen {ds:+.1f} % gegen {ss:+.1f} % im Index "
                 f"({ds - ss:+.1f} Prozentpunkte).")

    ohne_stop = [p["symbol"] for p in positionen if p["ohne_stop"]]
    if ohne_stop:
        s.append(f"<b style='color:{ROT};'>Ohne Stop-Order: "
                 f"{', '.join(ohne_stop)} — pruefen.</b>")

    eng = [p["symbol"] for p in positionen
           if p["stop_abst"] is not None and p["stop_abst"] < 3.0]
    if eng:
        s.append(f"Dicht am Stop (unter 3 %): {', '.join(eng)}.")

    if n_signale:
        s.append(f"{n_signale} neues Kaufsignal{'e' if n_signale != 1 else ''} diese Woche.")
    else:
        s.append("Keine neuen Kaufsignale — die Kandidatenliste unten zeigt, woran es lag.")
    return " ".join(s)


# ── Bausteine ─────────────────────────────────────────────────────────────────

def _h2(text: str) -> str:
    return f'<h2 style="{_H2}">{text}</h2>'


def _ampel_block(ampel: dict) -> str:
    """Ampel mit allen sechs Kriterien SICHTBAR.

    Im Web-Report stecken sie in einem Hover-Tooltip. Der funktioniert in der
    Mail nicht — und selbst wenn: die Begruendung der Marktlage gehoert in
    einen Boersenbrief, nicht hinter eine Mausbewegung.
    """
    kriterien = ampel.get("criteria", [])
    zeilen = "".join(
        f'<tr><td style="{_TDL}width:2em;">{"✅" if k["met"] else "❌"}</td>'
        f'<td style="{_TDL}">{k["name"]}</td>'
        f'<td style="{_TD}">{k["value"]}</td></tr>'
        for k in kriterien
    )
    return (
        f'<div style="background:{ampel.get("bg", KOPFBG)};padding:.6em 1em;'
        f'font-size:1.15em;font-weight:bold;color:{ampel.get("color", BLAU)};'
        f'margin:.4em 0 .8em;">'
        f'{ampel.get("emoji", "")} Marktampel: {ampel.get("label", "?")} '
        f'({ampel.get("score", "?")}/6)</div>'
        + (f'<table style="{_TABLE}">{zeilen}</table>' if zeilen else "")
    )


def _perf_block(perf: dict, svg: str) -> str:
    if not perf:
        return ""

    def zeile(name, woche, start):
        return (f'<tr><td style="{_TDL}"><b>{name}</b></td>'
                f'<td style="{_TD}color:{_farbe(woche)};">{_pct(woche)}</td>'
                f'<td style="{_TD}color:{_farbe(start)};">{_pct(start, 1)}</td></tr>')

    dw, sw = perf.get("depot_woche"), perf.get("spy_woche")
    ds, ss = perf.get("depot_start"), perf.get("spy_start")
    diff_w = (dw - sw) if (dw is not None and sw is not None) else None
    diff_s = (ds - ss) if (ds is not None and ss is not None) else None

    return (
        _h2("1) Depot gegen den Markt") +
        f'<table style="{_TABLE}">'
        f'<tr><th style="{_THL}"></th><th style="{_TH}">Woche</th>'
        f'<th style="{_TH}">seit Start</th></tr>'
        + zeile("Depot", dw, ds)
        + zeile("S&amp;P 500", sw, ss)
        + (f'<tr><td style="{_TDL}">Differenz</td>'
           f'<td style="{_TD}color:{_farbe(diff_w)};">'
           f'{"–" if diff_w is None else f"{diff_w:+.2f} pp"}</td>'
           f'<td style="{_TD}color:{_farbe(diff_s)};">'
           f'{"–" if diff_s is None else f"{diff_s:+.1f} pp"}</td></tr>')
        + "</table>"
        + (f'<div style="margin-bottom:1.6em;">{svg}</div>' if svg else "")
    )


def _positionen_block(positionen: list, cash, equity) -> str:
    if not positionen:
        return _h2("2) Positionen") + "<p>Keine offenen Positionen.</p>"

    zeilen = ""
    for p in positionen:
        stop_txt = "–" if p["stop"] is None else f'${p["stop"]:.2f}'
        if p["ohne_stop"]:
            stop_txt = f'<span style="color:{ROT};font-weight:bold;">kein Stop</span>'
        abst = ("–" if p["stop_abst"] is None
                else f'{p["stop_abst"]:.1f} %')
        r_txt = "–" if p["gewinn_r"] is None else f'{p["gewinn_r"]:+.2f} R'
        zeilen += (
            f'<tr>'
            f'<td style="{_TDL}"><b>{p["symbol"]}</b><br>'
            f'<span style="color:{GRAU};font-size:.85em;">{p["company"][:28]}</span></td>'
            f'<td style="{_TDL}font-size:.88em;">{p["sector"]}</td>'
            f'<td style="{_TD}">${p["einstand"]:.2f}</td>'
            f'<td style="{_TD}">${p["kurs"]:.2f}</td>'
            f'<td style="{_TD}color:{_farbe(p["gewinn_pct"])};font-weight:600;">'
            f'{_pct(p["gewinn_pct"], 1)}<br>'
            f'<span style="font-weight:400;font-size:.85em;">{_geld(p["gewinn_usd"], True)}</span></td>'
            f'<td style="{_TD}color:{_farbe(p["gewinn_r"])};">{r_txt}</td>'
            f'<td style="{_TD}">{stop_txt}<br>'
            f'<span style="color:{GRAU};font-size:.85em;">{abst} entfernt</span></td>'
            f'<td style="{_TD}">{p["tage"] if p["tage"] is not None else "–"}</td>'
            f'<td style="{_TD}">{p["rs"] if p["rs"] is not None else "–"}</td>'
            f'</tr>'
        )

    kopf = (f'<tr><th style="{_THL}">Position</th><th style="{_THL}">Sektor</th>'
            f'<th style="{_TH}">Einstand</th><th style="{_TH}">Kurs</th>'
            f'<th style="{_TH}">Gewinn</th><th style="{_TH}">in R</th>'
            f'<th style="{_TH}">Stop</th><th style="{_TH}">Tage</th>'
            f'<th style="{_TH}">RS</th></tr>')

    kopfzeile = (
        f'<p style="color:{GRAU};margin:.2em 0 1em;">'
        f'Equity {_geld(equity)} · Cash {_geld(cash)} · {len(positionen)} Positionen</p>'
    )
    fuss = (f'<p style="color:{GRAU};font-size:.85em;margin-top:-1.2em;">'
            f'R = anfaengliches Risiko je Aktie (Einstand minus initialer Stop). '
            f'+2 R heisst: die Position hat das Doppelte dessen verdient, was sie '
            f'riskiert hat.</p>')
    return (_h2("2) Positionen") + kopfzeile
            + f'<table style="{_TABLE}">{kopf}{zeilen}</table>' + fuss)


def _kandidaten_block(signale: list, kandidaten: list, report_url: str) -> str:
    if signale:
        karten = ""
        for s in signale:
            karten += (
                f'<div style="border:1px solid {RAHMEN};border-left:4px solid {GRUEN};'
                f'padding:.8em 1em;margin-bottom:.8em;">'
                f'<div style="font-size:1.05em;font-weight:bold;color:{BLAU};">'
                f'{s.ticker} — {s.company}</div>'
                f'<div style="color:{GRAU};font-size:.88em;margin-bottom:.5em;">'
                f'{s.industry} · Muster {s.pattern} · RS {s.rs_score or "–"}</div>'
                f'<table style="{_TABLE}margin-bottom:0;">'
                f'<tr><th style="{_TH}">Kurs</th><th style="{_TH}">Buy-Stop</th>'
                f'<th style="{_TH}">Stop</th><th style="{_TH}">Risiko</th>'
                f'<th style="{_TH}">Position</th></tr>'
                f'<tr><td style="{_TD}">${s.entry_price:.2f}</td>'
                f'<td style="{_TD}">${s.buy_stop:.2f}</td>'
                f'<td style="{_TD}">${s.stop_loss:.2f} '
                f'<span style="color:{GRAU};">({s.stop_loss_pct * 100:.1f} %)</span></td>'
                f'<td style="{_TD}">{s.risk_on_equity_pct * 100:.2f} %</td>'
                f'<td style="{_TD}">{_geld(s.position_value)}</td></tr>'
                f'</table></div>'
            )
        return _h2("3) Kaufsignale") + karten

    if not kandidaten:
        return (_h2("3) Kaufsignale")
                + "<p>Keine Signale und keine Kandidaten diese Woche.</p>")

    zeilen = ""
    for k in kandidaten[:5]:
        zeilen += (
            f'<tr><td style="{_TDL}"><b>{k.get("ticker", "")}</b><br>'
            f'<span style="color:{GRAU};font-size:.85em;">{str(k.get("company", ""))[:30]}</span></td>'
            f'<td style="{_TD}">{k.get("score", "–")}/8</td>'
            f'<td style="{_TD}">{k.get("rs", "–")}</td>'
            f'<td style="{_TDL}color:{ROT};font-size:.88em;">{k.get("fails", "")}</td></tr>'
        )
    return (
        _h2("3) Kaufsignale")
        + "<p>Keine Signale diese Woche. Die naechstbesten Kandidaten und "
          "woran sie scheitern:</p>"
        + f'<table style="{_TABLE}">'
          f'<tr><th style="{_THL}">Titel</th><th style="{_TH}">Score</th>'
          f'<th style="{_TH}">RS</th><th style="{_THL}">Scheitert an</th></tr>'
          f'{zeilen}</table>'
    )


def _markt_block(breadth_rows: list, sector_rows: list, idx_rows: list) -> str:
    teile = [_h2("4) Marktlage")]

    if breadth_rows:
        z = "".join(
            f'<tr><td style="{_TDL}">{r[0]}</td>'
            + "".join(f'<td style="{_TD}">{v}</td>' for v in r[1:])
            + "</tr>"
            for r in breadth_rows
        )
        teile.append(
            f'<table style="{_TABLE}">'
            f'<tr><th style="{_THL}">Marktbreite</th><th style="{_TH}">jetzt</th>'
            f'<th style="{_TH}">Woche −1</th><th style="{_TH}">Woche −4</th></tr>{z}</table>'
        )


    if sector_rows:
        # sector_rows: [{"ticker","name","chg"}], bereits absteigend sortiert.
        # Nur die drei staerksten und die drei schwaechsten — die Rotation ist
        # die Information, nicht die vollstaendige Liste.
        def sz(rows):
            return "".join(
                f'<tr><td style="{_TDL}">{r["name"]}</td>'
                f'<td style="{_TDL}">{r["ticker"]}</td>'
                f'<td style="{_TD}color:{_farbe(r["chg"])};">{r["chg"]:+.2f} %</td></tr>'
                for r in rows
            )
        teile.append(
            f'<table style="{_TABLE}">'
            f'<tr><th style="{_THL}">Sektor</th><th style="{_THL}">ETF</th>'
            f'<th style="{_TH}">Woche</th></tr>'
            f'{sz(sector_rows[:3])}'
            f'<tr><td colspan="3" style="{_TDL}color:{GRAU};font-size:.85em;">'
            f'… {max(0, len(sector_rows) - 6)} weitere …</td></tr>'
            f'{sz(sector_rows[-3:])}</table>'
        )

    if idx_rows:
        teile.append(idx_rows)
    return "".join(teile)


def breadth_rows_from_snapshot(snap, zeilen: Optional[list] = None) -> list:
    """Ausgewaehlte Zeilen aus dem Marktbreite-Snapshot als (Name, jetzt, −1, −4).

    Bewusst nicht alle sieben Zeilen: der Brief zeigt die drei, die die Lage
    tragen, der Rest steht im Web-Report.
    """
    if snap is None or getattr(snap, "empty", True):
        return []
    gewuenscht = zeilen or [
        "% über 10‑Wochen‑EMA",
        "1W-Kursgewinner (%)",
        "NH/(NH+NL) (%)",
    ]
    spalten = [c for c in ("Aktuelle Woche", "Woche −1", "Woche −4")
               if c in snap.columns]
    out = []
    for name in gewuenscht:
        if name not in snap.index:
            continue
        werte = []
        for c in spalten:
            v = snap.loc[name, c]
            try:
                werte.append(f"{float(v):.1f} %")
            except (TypeError, ValueError):
                werte.append(str(v))
        out.append((name.replace("‑", "-"), *werte))
    return out


# ── Zusammenbau ───────────────────────────────────────────────────────────────

def build_boersenbrief(
    *,
    report_date:   str,
    ampel:         dict,
    perf:          dict,
    svg:           str,
    positionen:    list,
    cash,
    equity,
    signale:       list,
    kandidaten:    list,
    breadth_rows:  list,
    sector_rows:   list,
    markt_extra:   str = "",
    bericht:       str = "",
    report_url:    str = "",
    test_mode:     bool = False,
) -> str:
    banner = (
        f'<div style="background:#fff3cd;border:1px solid #ffeeba;padding:.7em 1em;'
        f'margin-bottom:1.2em;">⚠️ <b>TEST-MODUS</b> — keine Orders platziert '
        f'oder storniert</div>' if test_mode else ""
    )
    link = (
        f'<p style="margin-top:2em;padding-top:1em;border-top:1px solid {RAHMEN};">'
        f'<a href="{report_url}" style="color:{BLAU};font-weight:bold;">'
        f'Vollstaendigen Report ansehen →</a></p>' if report_url else ""
    )
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:760px;
margin:0 auto;padding:1.5em 1.2em;color:#1a1a1a;line-height:1.45;">
  <div style="border-bottom:3px solid {BLAU};padding-bottom:.6em;margin-bottom:1.2em;">
    <div style="font-size:1.5em;font-weight:bold;color:{BLAU};">Weekly US Market Report</div>
    <div style="color:{GRAU};">Berichtswoche {report_date}</div>
  </div>
  {banner}
  {_ampel_block(ampel)}
  <div style="background:{KOPFBG};border-left:4px solid {BLAU};padding:.9em 1.1em;
       margin:1.2em 0 .5em;">{bericht}</div>
  {_perf_block(perf, svg)}
  {_positionen_block(positionen, cash, equity)}
  {_kandidaten_block(signale, kandidaten, report_url)}
  {_markt_block(breadth_rows, sector_rows, markt_extra)}
  {link}
</div>"""
