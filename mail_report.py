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


def _n(v, decimals: int = 2, plus: bool = False, tausender: bool = False) -> str:
    """Zahl in deutscher Schreibweise: Komma als Dezimaltrennzeichen.

    Der Brief ist auf Deutsch, also gehoert dort 1.234,56 und nicht 1,234.56.
    Python formatiert englisch; die beiden Trennzeichen werden ueber einen
    Platzhalter getauscht, weil ein direktes Ersetzen sie sonst gegenseitig
    ueberschreibt.

    ACHTUNG: nicht fuer SVG-Koordinaten benutzen. Dort sind Punkt und Komma
    Syntax (`x,y`-Paare in `points`), kein Zahlenformat — ein Dezimalkomma
    zerlegt den Pfad in doppelt so viele, falsche Punkte.
    """
    if v is None:
        return "–"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "–"
    s = f"{v:,.{decimals}f}" if tausender else f"{v:.{decimals}f}"
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return ("+" + s) if (plus and v > 0) else s


def _pct(v: Optional[float], decimals: int = 2) -> str:
    if v is None:
        return "–"
    return f"{_n(v, decimals, plus=True)} %"


def _geld(v: Optional[float], plus: bool = False) -> str:
    if v is None:
        return "–"
    s = "+" if plus and v > 0 else ""
    return f"{s}${_n(v, 0, tausender=True)}"


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


def _rs(v) -> str:
    """RS ohne Nachkommastelle — der Wert ist ein Perzentil von 1 bis 99."""
    try:
        return f"{float(v):.0f}"
    except (TypeError, ValueError):
        return "–"


def position_details(positions: list, journal_open: list,
                     rs_now_map: Optional[dict] = None) -> list:
    """Depotpositionen mit Stop-Abstand in Prozent UND in R.

    R ist das anfaengliche Risiko je Aktie (Einstand minus initialer Stop).
    Der Gewinn in R sagt, ob eine Position ihr eingesetztes Risiko verdient
    hat — eine Position bei +8 % mit engem Stop kann mehr geleistet haben als
    eine bei +15 % mit weitem. Die Zahl fehlte im alten Report vollstaendig.
    """
    j = {t["symbol"]: t for t in journal_open or []}
    rs_now_map = rs_now_map or {}
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

        # RS bei Kauf steht im Journal (aus den Signal-Metadaten), RS heute
        # kommt aus dem laufenden Screener. Die Differenz sagt, ob der Titel
        # seine relative Staerke seit dem Einstieg gehalten hat — ein Verlust
        # an RS bei steigendem Kurs heisst: der Markt laeuft schneller.
        rs_kauf = t.get("rs_score")
        rs_heute = rs_now_map.get(sym)
        try:
            rs_delta = (float(rs_heute) - float(rs_kauf)
                        if rs_heute is not None and rs_kauf is not None else None)
        except (TypeError, ValueError):
            rs_delta = None

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
            "rs_kauf":    rs_kauf,
            "rs_heute":   rs_heute,
            "rs_delta":   rs_delta,
            "ohne_stop":  stop is None,
        })
    out.sort(key=lambda d: (d["gewinn_pct"] is None, -(d["gewinn_pct"] or 0)))
    return out


# ── Lagebericht ───────────────────────────────────────────────────────────────

def muster_liste(leaders, limit: int = 12) -> list:
    """Titel mit erkanntem VCP oder Launchpad, nach RS sortiert.

    Quelle ist der Screener-Frame: `VCP` / `Launchpad` sagen, dass das MUSTER
    da ist; `VCP Entry` / `Launchpad Entry`, dass der Ausbruch laeuft. Hier
    zaehlt das Muster — der Zweck ist, sich den Chart anzusehen, bevor etwas
    passiert.

    Pivot ist das VCP-Breakout-Level bzw. der Launchpad-Pivot; `pivot_abstand`
    sagt, wie weit der Kurs davon entfernt ist (negativ = noch darunter).
    """
    if leaders is None or getattr(leaders, "empty", True):
        return []

    def _b(row, col):
        v = row.get(col, False)
        try:
            return bool(v) and v == v      # NaN ist nicht gleich sich selbst
        except (TypeError, ValueError):
            return False

    def _f(row, col):
        try:
            v = float(row.get(col))
            return v if v == v else None
        except (TypeError, ValueError):
            return None

    out = []
    for ticker, row in leaders.iterrows():
        hat_vcp   = _b(row, "VCP")
        hat_lp    = _b(row, "Launchpad")
        if not (hat_vcp or hat_lp):
            continue

        close = _f(row, "Close")
        if close is None or close <= 0:
            continue

        if hat_vcp and hat_lp:
            name, pivot = "VCP + Launchpad", _f(row, "Launchpad Pivot") or _f(row, "VCP Breakout Level")
        elif hat_vcp:
            name, pivot = "VCP", _f(row, "VCP Breakout Level")
        else:
            name, pivot = "Launchpad", _f(row, "Launchpad Pivot")

        detail = []
        if hat_vcp:
            wellen = _f(row, "VCP Waves")
            if wellen:
                detail.append(f"{int(wellen)} Wellen")
            if _b(row, "VCP Entry"):
                detail.append("Ausbruch")
        if hat_lp:
            wochen = _f(row, "Launchpad Weeks")
            spanne = _f(row, "Launchpad Range (%)")
            if wochen:
                detail.append(f"{int(wochen)} Wo. Basis")
            if spanne is not None:
                detail.append(f"Spanne {_n(spanne, 1)} %")
            if _b(row, "Launchpad Entry"):
                detail.append("Ausbruch")

        out.append({
            "ticker":         ticker,
            "company":        row.get("Company", ""),
            "muster":         name,
            "detail":         " · ".join(detail),
            "close":          close,
            "pivot":          pivot,
            "pivot_abstand":  ((close / pivot - 1) * 100) if pivot else None,
            "rs":             _f(row, "RS (O'Neil)"),
            "dist_52w":       _f(row, "Dist to 52W High (%)"),
            "link":           row.get("SA", ""),
        })

    out.sort(key=lambda d: (d["rs"] is None, -(d["rs"] or 0)))
    return out[:limit]


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
        s.append(f"Der Markt trägt: {score} von 6 Ampelkriterien sind erfüllt, "
                 f"Neueinstiege sind zulässig.")
    elif label == "Defensiv":
        s.append(f"Der Markt ist defensiv: nur {score} von 6 Kriterien sind "
                 f"erfüllt — keine Neueinstiege.")
    else:
        s.append(f"Der Markt ist uneinheitlich: {score} von 6 Kriterien sind erfüllt. "
                 f"Nicht erfüllt: "
                 + ", ".join(k["name"] for k in ampel.get("criteria", []) if not k["met"])
                 + ".")

    if breadth_jetzt is not None and breadth_vor is not None:
        delta = breadth_jetzt - breadth_vor
        richtung = "verbessert" if delta > 1 else ("verschlechtert" if delta < -1 else "kaum verändert")
        s.append(f"Die Marktbreite hat sich {richtung} "
                 f"({_n(breadth_vor, 1)} % → {_n(breadth_jetzt, 1)} % über der 10-Wochen-Linie).")

    dw, sw = perf.get("depot_woche"), perf.get("spy_woche")
    if dw is not None and sw is not None:
        diff = dw - sw
        wort = "besser als" if diff > 0.1 else ("schlechter als" if diff < -0.1 else "gleichauf mit")
        s.append(f"Das Depot liegt diese Woche bei {_n(dw, 2, plus=True)} % und damit {wort} "
                 f"dem S&P 500 ({_n(sw, 2, plus=True)} %).")
    elif dw is not None:
        s.append(f"Das Depot liegt diese Woche bei {_n(dw, 2, plus=True)} %.")

    ds, ss = perf.get("depot_start"), perf.get("spy_start")
    if ds is not None and ss is not None:
        s.append(f"Seit Handelsstart stehen {_n(ds, 1, plus=True)} % gegen {_n(ss, 1, plus=True)} % im Index "
                 f"({_n(ds - ss, 1, plus=True)} Prozentpunkte).")

    ohne_stop = [p["symbol"] for p in positionen if p["ohne_stop"]]
    if ohne_stop:
        s.append(f"<b style='color:{ROT};'>Ohne Stop-Order: "
                 f"{', '.join(ohne_stop)} — prüfen.</b>")

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
           f'{"–" if diff_w is None else f"{_n(diff_w, 2, plus=True)} pp"}</td>'
           f'<td style="{_TD}color:{_farbe(diff_s)};">'
           f'{"–" if diff_s is None else f"{_n(diff_s, 1, plus=True)} pp"}</td></tr>')
        + "</table>"
        + (f'<div style="margin-bottom:1.6em;">{svg}</div>' if svg else "")
    )


def _positionen_block(positionen: list, cash, equity,
                      eingefroren: float = 0.0) -> str:
    if not positionen:
        return _h2("2) Positionen") + "<p>Keine offenen Positionen.</p>"

    zeilen = ""
    for p in positionen:
        stop_txt = "–" if p["stop"] is None else f'${_n(p["stop"])}'
        if p["ohne_stop"]:
            stop_txt = f'<span style="color:{ROT};font-weight:bold;">kein Stop</span>'
        abst = ("–" if p["stop_abst"] is None
                else f'{_n(p["stop_abst"], 1)} %')
        r_txt = "–" if p["gewinn_r"] is None else f'{_n(p["gewinn_r"], 2, plus=True)} R'
        zeilen += (
            f'<tr>'
            f'<td style="{_TDL}"><b>{p["symbol"]}</b><br>'
            f'<span style="color:{GRAU};font-size:.85em;">{p["company"][:28]}</span></td>'
            f'<td style="{_TDL}font-size:.88em;">{p["sector"]}</td>'
            f'<td style="{_TD}">${_n(p["einstand"])}</td>'
            f'<td style="{_TD}">${_n(p["kurs"])}</td>'
            f'<td style="{_TD}color:{_farbe(p["gewinn_pct"])};font-weight:600;">'
            f'{_pct(p["gewinn_pct"], 1)}<br>'
            f'<span style="font-weight:400;font-size:.85em;">{_geld(p["gewinn_usd"], True)}</span></td>'
            f'<td style="{_TD}color:{_farbe(p["gewinn_r"])};">{r_txt}</td>'
            f'<td style="{_TD}">{stop_txt}<br>'
            f'<span style="color:{GRAU};font-size:.85em;">{abst} entfernt</span></td>'
            f'<td style="{_TD}">{p["tage"] if p["tage"] is not None else "–"}</td>'
            f'<td style="{_TD}">{_rs(p["rs_heute"])}'
            + (f'<br><span style="font-weight:400;font-size:.85em;'
               f'color:{_farbe(p["rs_delta"])};">{_n(p["rs_delta"], 0, plus=True)} seit Kauf</span>'
               if p["rs_delta"] is not None else
               f'<br><span style="font-weight:400;font-size:.85em;color:{GRAU};">'
               f'Kauf {_rs(p["rs_kauf"])}</span>')
            + '</td></tr>'
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
            f'R = anfängliches Risiko je Aktie (Einstand minus initialer Stop). '
            f'+2 R heisst: die Position hat das Doppelte dessen verdient, was sie '
            f'riskiert hat.</p>')

    # Delistete Positionen bleiben im Broker-Bestand liegen und werden mit
    # ihrem letzten Kurs bewertet. Sie zaehlen damit in die Equity und in jeden
    # Vergleich gegen den S&P 500 — als Block, der weder steigt noch faellt.
    # Das gehoert benannt, sonst liest sich die Performance genauer als sie ist.
    hinweis = ""
    if eingefroren and equity:
        try:
            anteil = eingefroren / float(equity) * 100
            hinweis = (
                f'<p style="background:#fff3cd;border-left:4px solid #ffc107;'
                f'padding:.6em .9em;font-size:.9em;margin:-.8em 0 1.4em;">'
                f'Die Equity enthält {_geld(eingefroren)} ({_n(anteil, 0)} %) in delisteten '
                f'Positionen, die mit ihrem letzten Kurs eingefroren sind. '
                f'Der Vergleich gegen den S&amp;P 500 ist in diesem Umfang unscharf.</p>'
            )
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    return (_h2("2) Positionen") + kopfzeile
            + f'<table style="{_TABLE}">{kopf}{zeilen}</table>' + fuss + hinweis)


def _mio(v, waehrung: str = "USD") -> str:
    """Grosse Betraege lesbar: Mrd. ab einer Milliarde, sonst Mio."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "–"
    zeichen = "$" if waehrung in (None, "USD") else ""
    if abs(v) >= 1e9:
        return f"{zeichen}{_n(v / 1e9, 2, tausender=True)} Mrd."
    if abs(v) >= 1e6:
        return f"{zeichen}{_n(v / 1e6, 0, tausender=True)} Mio."
    return f"{zeichen}{_n(v, 2, tausender=True)}"


def _reihen_tabelle(titel: str, reihe: list, waehrung: str,
                    als_betrag: bool = True) -> str:
    """Quartals- oder Jahresreihe als schmale Tabelle mit Vorjahresvergleich."""
    if not reihe:
        return ""
    kopf = "".join(f'<th style="{_TH}">{p["periode"]}</th>' for p in reihe)

    def _wert(p) -> str:
        return _mio(p["wert"], waehrung) if als_betrag else _n(p["wert"])

    def _yoy(p) -> str:
        if p["yoy"] is None:
            return "–"
        return f"{_n(p['yoy'], 1, plus=True)} %"

    werte = "".join(
        f'<td style="{_TD}">{_wert(p)}</td>' for p in reihe
    )
    yoy = "".join(
        f'<td style="{_TD}color:{_farbe(p["yoy"])};font-size:.85em;">'
        f'{_yoy(p)}</td>'
        for p in reihe
    )
    return (
        f'<table style="{_TABLE}margin-bottom:.8em;">'
        f'<tr><th style="{_THL}">{titel}</th>{kopf}</tr>'
        f'<tr><td style="{_TDL}">Wert</td>{werte}</tr>'
        f'<tr><td style="{_TDL}font-size:.85em;color:{GRAU};">ggü. Vorjahr</td>{yoy}</tr>'
        f'</table>'
    )


def _portraet(s, profil: dict, begruendung: list) -> str:
    """Unternehmensportraet zu einem Kaufsignal."""
    if not profil and not begruendung:
        return ""
    waehrung = (profil or {}).get("waehrung") or "USD"

    kopfzeilen = []
    for wert, name in ((profil.get("industrie"), None), (profil.get("land"), None)):
        if wert:
            kopfzeilen.append(str(wert))
    if profil.get("mitarbeiter"):
        kopfzeilen.append(f'{profil["mitarbeiter"]:,} Mitarbeitende'.replace(",", "."))

    beschreibung = profil.get("beschreibung", "")
    beschr_html = (
        f'<p style="margin:.6em 0;">{beschreibung}</p>'
        f'<p style="color:{GRAU};font-size:.82em;margin:-.3em 0 .8em;">'
        f'Unternehmensbeschreibung im Original (englisch), gekürzt.</p>'
        if beschreibung else ""
    )

    gruende = "".join(f"<li style='margin-bottom:.25em;'>{g}</li>" for g in begruendung)
    gruende_html = (
        f'<div style="font-weight:600;color:{BLAU};margin-top:.6em;">Warum im Depot</div>'
        f'<ul style="margin:.4em 0 .2em;padding-left:1.2em;">{gruende}</ul>'
        if gruende else ""
    )

    return (
        beschr_html
        + (f'<p style="color:{GRAU};font-size:.88em;margin:.2em 0 .8em;">'
           f'{" · ".join(kopfzeilen)}</p>' if kopfzeilen else "")
        + _reihen_tabelle("Umsatz je Quartal", profil.get("umsatz_q", []), waehrung)
        + _reihen_tabelle("Umsatz je Jahr", profil.get("umsatz_j", []), waehrung)
        + _reihen_tabelle("Gewinn je Aktie (Quartal)", profil.get("eps_q", []),
                          waehrung, als_betrag=False)
        + gruende_html
    )


def _kandidaten_block(signale: list, kandidaten: list, report_url: str,
                      profile: Optional[dict] = None) -> str:
    if signale:
        profile = profile or {}
        karten = ""
        for s in signale:
            link = (f'<a href="{s.sa_link}" style="color:{BLAU};text-decoration:none;">'
                    f'{s.ticker}</a>' if s.sa_link else s.ticker)
            karten += (
                f'<div style="border:1px solid {RAHMEN};border-left:4px solid {GRUEN};'
                f'padding:.9em 1.1em;margin-bottom:1.4em;">'
                f'<div style="font-size:1.1em;font-weight:bold;color:{BLAU};">'
                f'{link} — {s.company}</div>'
                f'<div style="color:{GRAU};font-size:.88em;margin-bottom:.5em;">'
                f'{s.industry} · Muster {s.pattern} · RS {_rs(s.rs_score)}</div>'
                f'<table style="{_TABLE}margin-bottom:.8em;">'
                f'<tr><th style="{_TH}">Kurs</th><th style="{_TH}">Buy-Stop</th>'
                f'<th style="{_TH}">Stop</th><th style="{_TH}">Risiko</th>'
                f'<th style="{_TH}">Position</th></tr>'
                f'<tr><td style="{_TD}">${_n(s.entry_price)}</td>'
                f'<td style="{_TD}">${_n(s.buy_stop)}</td>'
                f'<td style="{_TD}">${_n(s.stop_loss)} '
                f'<span style="color:{GRAU};">({_n(s.stop_loss_pct * 100, 1)} %)</span></td>'
                f'<td style="{_TD}">{_n(s.risk_on_equity_pct * 100, 2)} %</td>'
                f'<td style="{_TD}">{_geld(s.position_value)}</td></tr>'
                f'</table>'
                + _portraet(s, profile.get(s.ticker, {}),
                            (profile.get(s.ticker, {}) or {}).get("_begruendung", []))
                + '</div>'
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


def _muster_block(muster: list) -> str:
    """Titel mit aktuell erkanntem VCP oder Launchpad — zum Nachschauen im Chart.

    Bewusst NICHT auf Kaufsignale eingeschraenkt: gefragt ist das Muster, nicht
    der Ausbruch. Ein VCP ohne Volumenausbruch und ein Launchpad, das noch unter
    dem Pivot liegt, sind genau die Faelle, die man sich vorher ansehen will.
    Der Titel verlinkt auf StockAnalysis, wo der Chart liegt — den Chart selbst
    kann eine Mail nicht sinnvoll transportieren.
    """
    if not muster:
        return (_h2("4) Muster zum Ansehen")
                + f'<p style="color:{GRAU};">Diese Woche kein VCP und kein '
                  f'Launchpad im Universum erkannt.</p>')

    zeilen = ""
    for m in muster:
        titel = (f'<a href="{m["link"]}" style="color:{BLAU};text-decoration:none;">'
                 f'<b>{m["ticker"]}</b></a>' if m.get("link") else f'<b>{m["ticker"]}</b>')
        pivot = f'${_n(m["pivot"])}' if m.get("pivot") else "–"
        abst  = (f'{_n(m["pivot_abstand"], 1, plus=True)} %' if m.get("pivot_abstand") is not None
                 else "–")
        d52 = m.get("dist_52w")
        zeilen += (
            f'<tr>'
            f'<td style="{_TDL}">{titel}<br>'
            f'<span style="color:{GRAU};font-size:.85em;">{str(m.get("company", ""))[:26]}</span></td>'
            f'<td style="{_TDL}"><b>{m["muster"]}</b><br>'
            f'<span style="color:{GRAU};font-size:.85em;">{m.get("detail", "")}</span></td>'
            f'<td style="{_TD}">${_n(m["close"])}</td>'
            f'<td style="{_TD}">{pivot}<br>'
            f'<span style="color:{GRAU};font-size:.85em;">{abst}</span></td>'
            f'<td style="{_TD}">{_rs(m.get("rs"))}</td>'
            f'<td style="{_TD}">{"–" if d52 is None else f"{_n(d52, 1)} %"}</td>'
            f'</tr>'
        )

    return (
        _h2("4) Muster zum Ansehen")
        + f'<p style="color:{GRAU};margin:.2em 0 .8em;">'
          f'{len(muster)} Titel mit erkanntem Muster — unabhängig davon, ob '
          f'daraus ein Kaufsignal wurde. Ticker anklicken für den Chart.</p>'
        + f'<table style="{_TABLE}">'
          f'<tr><th style="{_THL}">Titel</th><th style="{_THL}">Muster</th>'
          f'<th style="{_TH}">Kurs</th><th style="{_TH}">Pivot</th>'
          f'<th style="{_TH}">RS</th><th style="{_TH}">zum 52W-Hoch</th></tr>'
          f'{zeilen}</table>'
    )


def _markt_block(breadth_rows: list, sector_rows: list, idx_rows: list) -> str:
    teile = [_h2("5) Marktlage")]

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
                f'<td style="{_TD}color:{_farbe(r["chg"])};">{_n(r["chg"], 2, plus=True)} %</td></tr>'
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
                werte.append(f"{_n(v, 1)} %")
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
    eingefroren:   float = 0.0,
    signale:       list,
    kandidaten:    list,
    muster:        list,
    profile:       Optional[dict] = None,
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
        f'Vollständigen Report ansehen →</a></p>' if report_url else ""
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
  {_positionen_block(positionen, cash, equity, eingefroren)}
  {_kandidaten_block(signale, kandidaten, report_url, profile)}
  {_muster_block(muster)}
  {_markt_block(breadth_rows, sector_rows, markt_extra)}
  {link}
</div>"""
