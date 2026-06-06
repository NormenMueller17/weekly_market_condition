"""
report.py — Generiert den wöchentlichen Zertifikate-HTML-Report.

Struktur:
  1. Marktampel (Header, immer sichtbar)
  2. Neue Kaufkandidaten (nur wenn Markt != ROT)
  3. Portfolio-Positionen (Ampelstatus)
  4. Roll-Kandidaten

Erweiterung: neue Sektionen als _section_*-Funktion hinzufügen
und in build_report() einbinden.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Optional

from zertifikate.ampel import Ampel, MarktampelResult

# Farbpalette (konsistent mit Portfolio-UI)
_FARBEN = {
    "gruen":  "#27ae60",
    "gelb":   "#f39c12",
    "rot":    "#e74c3c",
    "grau":   "#7f8c8d",
    "dunkel": "#2c3e50",
    "hell":   "#ecf0f1",
    "weiss":  "#ffffff",
    "blau":   "#2980b9",
}

_AMPEL_BG = {
    "gruen": "#d5f5e3",
    "gelb":  "#fef9e7",
    "rot":   "#fadbd8",
}


# ── Öffentliche API ───────────────────────────────────────────────────────────

def build_report(
    markt: MarktampelResult,
    kandidaten: list[dict],
    positionen: list[dict],
    roll_kandidaten: list[dict],
    report_date: Optional[str] = None,
    universe_all: Optional[list[dict]] = None,
    company_info: Optional[dict] = None,
) -> str:
    """
    Gibt einen vollständigen, selbst-enthaltenen HTML-String zurück.

    Parameters
    ----------
    markt           : MarktampelResult
    kandidaten      : list[dict]  — Ausgabe von scanner.screen_kandidaten()
    positionen      : list[dict]  — Ausgabe von portfolio.enrich_positions()
    roll_kandidaten : list[dict]  — gefilterte Teilmenge aus positionen
    report_date     : str         — ISO-Datum, default heute
    universe_all    : list[dict]  — Ausgabe von scanner.screen_universe_full()
    company_info    : dict        — {ticker: {name, sector}} aus universe.fetch_company_info()
    """
    if report_date is None:
        report_date = date.today().isoformat()

    sections = [
        _section_marktampel(markt),
    ]

    if markt.status in (Ampel.ROT, Ampel.GELB):
        sections.append(_section_markt_warnung(markt))
    else:
        if kandidaten:
            sections.append(_section_kandidaten(kandidaten, markt))
        else:
            sections.append(_section_keine_kandidaten(markt))

    if positionen:
        sections.append(_section_portfolio(positionen))

    if roll_kandidaten:
        sections.append(_section_roll(roll_kandidaten))

    if universe_all:
        sections.append(_section_universe_overview(universe_all, company_info or {}))

    sections.append(_section_footer(report_date))

    body = "\n".join(sections)
    return _wrap_html(body, report_date)


def save_report(html: str, report_date: str, output_dir: str = "docs/zertifikate") -> Path:
    """Speichert den Report und aktualisiert index.html."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report_path = out / f"{report_date}.html"
    report_path.write_text(html, encoding="utf-8")
    print(f"[REPORT] Gespeichert: {report_path}")

    _update_index(report_date, out)
    return report_path


# ── HTML-Wrapper ─────────────────────────────────────────────────────────────

def _wrap_html(body: str, report_date: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Zertifikate-Scanner {report_date}</title>
  {_css()}
</head>
<body>
  <nav class="g-nav">
    <a href="../index.html" class="g-brand">📈 Weekly Screener</a>
    <a href="../trades.html">Trade Journal</a>
    <a href="../performance.html">Performance</a>
    <a href="index.html" class="active">Zertifikate</a>
    <a href="portfolio.html">Portfolio</a>
    <a href="regelwerk.html">Regelwerk</a>
    <a href="../blueprint.html">Blueprint</a>
  </nav>
  <div class="container">
    <header>
      <h1>📊 Zertifikate-Scanner</h1>
      <p class="subtitle">Wochenbericht {report_date} &mdash; Low-Vol Momentum Screener</p>
    </header>
    {body}
  </div>
</body>
</html>"""


def _css() -> str:
    return """<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f5f6fa; color: #2c3e50; font-size: 14px; }
    .g-nav   { background: #003d99; display: flex; align-items: center; padding: 0 1.5em;
               box-shadow: 0 2px 6px rgba(0,0,0,.22); flex-wrap: wrap; }
    .g-brand { font-weight: bold; color: #fff; text-decoration: none; padding: .72em 1.1em .72em 0;
               margin-right: .5em; border-right: 1px solid rgba(255,255,255,.25);
               white-space: nowrap; font-size: .95em; }
    .g-nav a { color: rgba(255,255,255,.82); text-decoration: none; padding: .72em .85em;
               font-size: .84em; white-space: nowrap; }
    .g-nav a:hover  { color: #fff; background: rgba(255,255,255,.12); }
    .g-nav a.active { color: #fff; box-shadow: inset 0 -3px rgba(255,255,255,.8); font-weight: 600; }
    .container { max-width: 1100px; margin: 0 auto; padding: 20px; }
    header { background: #2c3e50; color: white; padding: 20px 24px;
             border-radius: 8px; margin-bottom: 20px; }
    header h1 { font-size: 1.6em; margin-bottom: 4px; }
    header .subtitle { opacity: 0.8; font-size: 0.9em; }
    .section { background: white; border-radius: 8px; padding: 20px;
               margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
    .section h2 { font-size: 1.1em; margin-bottom: 14px; padding-bottom: 8px;
                  border-bottom: 2px solid #ecf0f1; color: #2c3e50; }
    .ampel-box { border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; }
    .ampel-gruen { background: #d5f5e3; border-left: 5px solid #27ae60; }
    .ampel-gelb  { background: #fef9e7; border-left: 5px solid #f39c12; }
    .ampel-rot   { background: #fadbd8; border-left: 5px solid #e74c3c; }
    .ampel-status { font-size: 1.3em; font-weight: 700; margin-bottom: 4px; }
    .ampel-aktion { font-size: 0.9em; opacity: 0.85; }
    .ampel-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .ampel-card { border-radius: 6px; padding: 12px; text-align: center; }
    .ampel-card .label { font-size: 0.75em; text-transform: uppercase;
                         letter-spacing: 0.05em; opacity: 0.7; margin-bottom: 4px; }
    .ampel-card .value { font-size: 1.05em; font-weight: 600; }
    .warnung { background: #fadbd8; border: 2px solid #e74c3c; border-radius: 8px;
               padding: 16px; font-weight: 600; color: #922b21; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88em; }
    th { background: #2c3e50; color: white; padding: 8px 10px;
         text-align: left; font-weight: 600; white-space: nowrap; }
    td { padding: 7px 10px; border-bottom: 1px solid #ecf0f1; vertical-align: middle; }
    tr:hover td { background: #f8f9fa; }
    tr:last-child td { border-bottom: none; }
    .score-bar { display: inline-block; height: 8px; border-radius: 4px;
                 background: #27ae60; vertical-align: middle; margin-right: 6px; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
             font-size: 0.78em; font-weight: 600; }
    .badge-gruen { background: #d5f5e3; color: #1e8449; }
    .badge-gelb  { background: #fef9e7; color: #9a7d0a; }
    .badge-rot   { background: #fadbd8; color: #922b21; }
    .badge-grau  { background: #eaecee; color: #555; }
    .os-empfehlung { font-size: 0.82em; color: #2980b9; font-style: italic; }
    .info-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;
                 margin-bottom: 16px; }
    .info-card { background: #f8f9fa; border-radius: 6px; padding: 10px 14px; }
    .info-card .label { font-size: 0.75em; color: #7f8c8d; margin-bottom: 2px; }
    .info-card .value { font-size: 1.1em; font-weight: 700; }
    .keine { color: #7f8c8d; font-style: italic; padding: 8px 0; }
    .footer { text-align: center; color: #95a5a6; font-size: 0.8em; padding: 16px 0 8px; }
    @media (max-width: 700px) {
      .ampel-grid, .info-grid { grid-template-columns: 1fr 1fr; }
      table { font-size: 0.8em; }
    }
  </style>"""


# ── Sektionen ─────────────────────────────────────────────────────────────────

def _section_marktampel(markt: MarktampelResult) -> str:
    s = markt.status
    css = f"ampel-{s.value}"
    d = markt.details

    ema_ok   = "✅" if markt.ema_fast_above_slow else "❌"
    ma200_ok = "✅" if markt.index_above_200ma    else "❌"
    vix_icon = "✅" if markt.vix <= d.get("vix_gruen_schwelle", 20) else (
               "⚠️" if markt.vix < d.get("vix_rot_schwelle", 25) else "❌"
    )

    fp = d.get('ema_fast_period', 10)
    sp = d.get('ema_slow_period', 50)
    fv = d.get('ema_fast_val', '—')
    sv = d.get('ema_slow_val', '—')
    ema_richtung = 'über' if markt.ema_fast_above_slow else 'unter'

    return f"""
<div class="section">
  <h2>Ampel 1 — Marktampel</h2>
  <div class="ampel-box {css}">
    <div class="ampel-status">{s.emoji} {s.label} — {markt.aktion()}</div>
    <div class="ampel-aktion">
      {ema_ok} {fp}W-EMA {ema_richtung} {sp}W-EMA
      &nbsp;<span style="opacity:0.7;font-size:0.9em">({fp}W: {fv} / {sp}W: {sv} / Abstand: {markt.ema_distance_pct:.1f}%)</span>
      &nbsp;|&nbsp;
      {ma200_ok} S&amp;P 500 {'über' if markt.index_above_200ma else 'unter'} 200W-MA &nbsp;|&nbsp;
      {vix_icon} VIX: {markt.vix:.1f}
    </div>
  </div>
  <div class="ampel-grid">
    <div class="ampel-card" style="background:{_AMPEL_BG[s.value]}">
      <div class="label">S&amp;P 500</div>
      <div class="value">{d.get('index_close', '—')}</div>
    </div>
    <div class="ampel-card" style="background:{_AMPEL_BG[s.value]}">
      <div class="label">200-MA</div>
      <div class="value">{d.get('ma200', '—')}</div>
    </div>
    <div class="ampel-card" style="background:{_AMPEL_BG[s.value]}">
      <div class="label">VIX</div>
      <div class="value">{markt.vix:.1f}</div>
    </div>
  </div>
</div>"""


def _section_markt_warnung(markt: MarktampelResult) -> str:
    if markt.status == Ampel.ROT:
        msg = (
            "⛔ Marktampel ROT — Keine neuen Kaufkandidaten. "
            "Bestehende Positionen mit engem Stopp halten. "
            "Bei gleichzeitiger Rot-Einzelampel sofort aussteigen."
        )
    else:
        msg = (
            "⚠️ Marktampel GELB — Keine neuen Kaufkandidaten. "
            "Signale gemischt: Stopps enger setzen, keine Aufstockung bestehender Positionen. "
            "Neue Longs erst wieder bei GRÜNER Marktampel."
        )
    return f"""
<div class="section">
  <div class="warnung">{msg}</div>
</div>"""


def _section_kandidaten(kandidaten: list[dict], markt: MarktampelResult) -> str:
    markt_hinweis = ""
    if markt.status == Ampel.GELB:
        markt_hinweis = '<div class="warnung" style="margin-bottom:12px">⚠️ Marktampel GELB — Kandidaten mit Vorsicht handeln. Stopps enger setzen, keine Aufstockung bestehender Positionen.</div>'

    _G = "background:#d5f5e3;color:#1e8449"  # grün
    _N = ""                                   # neutral (kein Hintergrund)

    def _td(ok: bool, content: str) -> str:
        style = _G if ok else _N
        return f'<td style="{style}">{content}</td>'

    rows = ""
    for k in kandidaten:
        score = k.get("score", 0)
        bar_w = min(int(score), 100)
        e3    = k.get("e3_confirmed", 0)

        e3_icons = ("✅" if k.get("e3_macd_dreht")   else "·") + \
                   ("✅" if k.get("e3_momentum_pos") else "·") + \
                   ("✅" if k.get("e3_volumen_ok")   else "·")

        os_emp = k.get("os_empfehlung", {})

        is_recovery = k.get("e2_mode") == "recovery"
        mode_badge  = (' <span title="Recovery: 40W-MA-Kreuz von unten" '
                       'style="background:#e8f4fd;color:#1a5276;border:1px solid #aed6f1;'
                       'border-radius:3px;padding:1px 5px;font-size:0.75em">🔄 Recovery</span>'
                       if is_recovery else "")
        pb_label = "MA-Kreuz" if is_recovery else f"{k.get('pullback_pct','—')}%"

        rows += f"""<tr>
          <td><strong>{k['ticker']}</strong>{mode_badge}</td>
          {_td(True,  k.get('close', '—'))}
          {_td(True,  k.get('ma50',  '—'))}
          {_td(True,  f"{k.get('perf_52w_pct','—')}%")}
          {_td(True,  k.get('adx',   '—'))}
          {_td(True,  pb_label)}
          {_td(k.get('e2_rsi_ok',      False), k.get('rsi',       '—'))}
          {_td(k.get('e2_williams_ok', False), k.get('williams_r', '—'))}
          {_td(True,  f"{k.get('hv30','—')}%")}
          {_td(k.get('e2_beta_ok',     False), k.get('beta',       '—'))}
          <td><span style="font-size:1.1em">{e3_icons}</span> ({e3}/3)</td>
          <td>
            <span class="score-bar" style="width:{bar_w}px"></span>
            <strong>{score}</strong>
          </td>
          <td class="os-empfehlung">{os_emp.get('hinweis','—')}</td>
        </tr>"""

    return f"""
<div class="section">
  <h2>Neue Kaufkandidaten ({len(kandidaten)})</h2>
  {markt_hinweis}
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Ticker</th><th>Kurs</th><th>MA50</th><th>52W%</th>
        <th>ADX</th><th>Pullback%</th><th>RSI</th><th>Will.%R</th>
        <th>HV30</th><th>Beta</th><th>E3</th><th>Score</th>
        <th>OS-Empfehlung</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
  <p style="margin-top:10px;font-size:0.82em;color:#7f8c8d">
    E3: MACD dreht / Momentum+ / Volumen-Bestätigung &nbsp;|&nbsp;
    OS-Empfehlung: Strike, Laufzeit und Hebel sind Richtwerte — bitte im Broker-Tool verifizieren.
  </p>
</div>"""


def _section_keine_kandidaten(markt: MarktampelResult) -> str:
    grund = "Kein Titel hat alle drei Einstiegs-Ebenen bestanden." if markt.status != Ampel.ROT else ""
    return f"""
<div class="section">
  <h2>Neue Kaufkandidaten</h2>
  <p class="keine">Diese Woche keine Kaufkandidaten. {grund}</p>
</div>"""


def _section_portfolio(positionen: list[dict]) -> str:
    if not positionen:
        return f"""
<div class="section">
  <h2>Portfolio-Positionen</h2>
  <p class="keine">Keine offenen Positionen. <a href="../zertifikate/portfolio.html">Position erfassen →</a></p>
</div>"""

    rows = ""
    for p in positionen:
        e_s = p.get("einzelampel", "unbekannt")
        z_s = p.get("zeitampel", "unbekannt")
        e_badge = f'<span class="badge badge-{e_s}">{Ampel(e_s).emoji if e_s in ("gruen","gelb","rot") else "?"} {e_s.upper()}</span>' if e_s in ("gruen","gelb","rot") else f'<span class="badge badge-grau">{e_s}</span>'
        z_badge = f'<span class="badge badge-{z_s}">{Ampel(z_s).emoji if z_s in ("gruen","gelb","rot") else "?"} {z_s.upper()}</span>' if z_s in ("gruen","gelb","rot") else f'<span class="badge badge-grau">{z_s}</span>'

        details  = p.get("einzel_details", {})
        ausstieg = p.get("ausstieg", {})
        ausstieg_hinweis = ausstieg.get("empfehlung", "—")
        ausstieg_style   = 'color:#e74c3c;font-weight:700' if "Sofort" in ausstieg_hinweis else ''

        tp        = p.get("tp_signal", {})
        tp_text   = tp.get("aktion", "—")
        basis_pct = tp.get("basis_perf_pct")
        tp_style  = ''
        if "Teilverkauf" in tp_text:
            tp_style = 'color:#1e8449;font-weight:700'
        elif "Hälfte" in tp_text:
            tp_style = 'color:#117a65;font-weight:600'
        elif "Verlust" in tp_text:
            tp_style = 'color:#e74c3c'

        hebel_est = p.get("hebel_aktuell_est")
        strike_roll = p.get("strike_roll_signal", False)
        hebel_text  = f"{hebel_est}×" if hebel_est is not None else "—"
        hebel_style = 'color:#e74c3c;font-weight:700' if strike_roll else ''

        basis_text = f"{basis_pct:+.1f}%" if basis_pct is not None else "—"

        rows += f"""<tr>
          <td><strong>{p.get('basiswert','—')}</strong></td>
          <td style="font-size:0.82em">{p.get('schein_name','—')}</td>
          <td>{p.get('kauf_datum','—')}</td>
          <td>{p.get('kauf_kurs_schein','—')}</td>
          <td style="text-align:center">{basis_text}</td>
          <td style="{tp_style};font-size:0.85em">{tp_text}</td>
          <td style="{hebel_style};text-align:center">{hebel_text}</td>
          <td>{p.get('restlaufzeit_monate','—')} Monate</td>
          <td>{e_badge}<br><span style="font-size:0.78em;color:#555">ADX:{details.get('adx','—')} RSI:{details.get('rsi','—')}</span></td>
          <td>{z_badge}</td>
          <td style="{ausstieg_style}">{ausstieg_hinweis}</td>
        </tr>"""

    return f"""
<div class="section">
  <h2>Portfolio-Positionen ({len(positionen)} offen)</h2>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Basiswert</th><th>Schein</th><th>Kauf</th><th>Kauf-Kurs</th>
        <th>Basis Δ</th><th>Schein-Gewinn est.</th><th>Hebel est.</th>
        <th>Restlaufzeit</th><th>Einzelampel</th><th>Zeitampel</th><th>Stopp-Signal</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
  <p style="margin-top:8px;font-size:0.8em;color:#7f8c8d">
    Schein-Gewinn est. = Basiswert-Performance × Hebel (Kaufzeitpunkt) — Näherung ohne Theta/Vega.
    Hebel est. = Hebel<sub>kauf</sub> × Kurs<sub>kauf</sub> / Kurs<sub>aktuell</sub>.
    Rot = Strike-Roll prüfen (Hebel &lt; 2).
  </p>
</div>"""


def _section_roll(roll_kandidaten: list[dict]) -> str:
    rows = ""
    for p in roll_kandidaten:
        z_s         = p.get("zeitampel", "?")
        strike_roll = p.get("strike_roll_signal", False)
        hebel_est   = p.get("hebel_aktuell_est")

        if strike_roll and z_s in ("gelb", "rot"):
            grund = "🔴 Strike + Zeit"
        elif strike_roll:
            grund = "🟠 Strike-Roll (Hebel gesunken)"
        elif z_s == "rot":
            grund = "🔴 Zeitwert dringend"
        else:
            grund = "🟡 Zeitwert prüfen"

        if strike_roll:
            empfehlung = (
                f"Hebel est. {hebel_est}× — unter Mindestschwelle. "
                f"Neuen Call OS: Strike höher setzen, Laufzeit +18 Monate, Ziel-Hebel ~3."
            )
        else:
            empfehlung = (
                f"Restlaufzeit {p.get('restlaufzeit_monate','—')} Monate. "
                f"Neuen Call OS: gleiche Parameter, Laufzeit +18 Monate, Hebel ~{p.get('hebel_kauf', 3)}."
            )

        rows += f"""<tr>
          <td><strong>{p.get('basiswert','—')}</strong></td>
          <td style="font-size:0.82em">{p.get('schein_name','—')}</td>
          <td>{p.get('faelligkeitsdatum','—')}</td>
          <td style="text-align:center">{p.get('restlaufzeit_monate','—')} M</td>
          <td>{grund}</td>
          <td style="font-size:0.82em;color:#2980b9">{empfehlung}</td>
        </tr>"""

    return f"""
<div class="section">
  <h2>Roll-Kandidaten ({len(roll_kandidaten)})</h2>
  <p style="margin-bottom:10px;font-size:0.88em;color:#555">
    Zeitwert-Roll: Restlaufzeit &lt; 6 Monate bei intaktem Trend. &nbsp;|&nbsp;
    Strike-Roll: Hebel durch Kursanstieg unter 2 gesunken.
    Empfehlung: neuen Schein kaufen, alten Schein verkaufen.
  </p>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Basiswert</th><th>Schein</th><th>Fälligkeit</th>
        <th>Restlaufzeit</th><th>Grund</th><th>Empfehlung</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
</div>"""


def _section_universe_overview(universe_all: list[dict], company_info: dict) -> str:
    """Zeigt alle Titel mit ihren 12 Screening-Metriken als grün/rot gefärbte Tabelle.
    Erste 3 Spalten sind sticky (fixiert beim Horizontal-Scrollen).
    Alle Spalten sind durch Klick auf den Header auf- und absteigend sortierbar.
    """

    def _cell(ok: bool, display: str, sort_val: str) -> str:
        bg    = "#d5f5e3" if ok else "#fadbd8"
        color = "#1e8449" if ok else "#922b21"
        return (
            f'<td data-val="{sort_val}" style="background:{bg};color:{color};'
            f'text-align:center;white-space:nowrap;font-size:0.82em">{display}</td>'
        )

    rows = ""
    for t in universe_all:
        ticker   = t["ticker"]
        info     = company_info.get(ticker, {})
        name     = info.get("name", ticker)
        sector   = info.get("sector", "n/a")
        erfuellt = t["kriterien_erfuellt"]

        if erfuellt >= 9:
            badge_bg, badge_color = "#d5f5e3", "#1e8449"
        elif erfuellt >= 6:
            badge_bg, badge_color = "#fef9e7", "#9a7d0a"
        else:
            badge_bg, badge_color = "#fadbd8", "#922b21"

        rows += f"""<tr>
          <td class="u-sticky u-col0" data-val="{ticker}"><strong>{ticker}</strong></td>
          <td class="u-sticky u-col1" data-val="{name}" style="font-size:0.82em;white-space:nowrap">{name}</td>
          <td class="u-sticky u-col2" data-val="{sector}" style="font-size:0.78em;color:#555;white-space:nowrap">{sector}</td>
          <td data-val="{erfuellt}" style="text-align:center;font-weight:700;font-size:0.95em;
              background:{badge_bg};color:{badge_color}">{erfuellt}/12</td>
          {_cell(t["e1_ma200"],    f"&gt;MA200<br>{t['close']}",  str(int(t['e1_ma200'])))}
          {_cell(t["e1_ma50"],     f"&gt;MA50",                   str(int(t['e1_ma50'])))}
          {_cell(t["e1_adx"],      f"ADX<br>{t['adx_val']}",      str(t['adx_val']))}
          {_cell(t["e1_perf"],     f"52W<br>{t['perf_52w']}%",    str(t['perf_52w']))}
          {_cell(t["e2_pullback"], f"PB%<br>{t['pullback_pct']}%",str(t['pullback_pct']))}
          {_cell(t["e2_rsi"],      f"RSI<br>{t['rsi_val']}",      str(t['rsi_val']))}
          {_cell(t["e2_williams"], f"W%R<br>{t['williams_val']}",  str(t['williams_val']))}
          {_cell(t["e2_hv"],       f"HV30<br>{t['hv30_val']}%",   str(t['hv30_val']))}
          {_cell(t["e2_beta"],     f"Beta<br>{t['beta_val']}",     str(t['beta_val']))}
          {_cell(t["e3_macd"],     "MACD↑<br>" + ("✓" if t["e3_macd"]     else "✗"), str(int(t["e3_macd"])))}
          {_cell(t["e3_momentum"], "Mom↑<br>"  + ("✓" if t["e3_momentum"] else "✗"), str(int(t["e3_momentum"])))}
          {_cell(t["e3_volumen"],  "Vol↑<br>"  + ("✓" if t["e3_volumen"]  else "✗"), str(int(t["e3_volumen"])))}
        </tr>"""

    return f"""
<style>
  #univ-wrap {{ overflow-x: auto; }}
  #univ-tbl  {{ border-collapse: collapse; min-width: 100%; }}
  #univ-tbl th, #univ-tbl td {{ padding: 7px 10px; border-bottom: 1px solid #ecf0f1;
      white-space: nowrap; vertical-align: middle; }}
  #univ-tbl thead th {{ background: #2c3e50; color: #fff; font-size: 0.83em;
      font-weight: 600; text-align: center; cursor: pointer; user-select: none; }}
  #univ-tbl thead th:hover {{ background: #3d5370; }}
  #univ-tbl tbody tr:hover td {{ background: #f0f4f8 !important; }}
  #univ-tbl tbody tr:hover td.u-sticky {{ background: #f0f4f8 !important; }}
  .u-sticky {{ position: sticky; z-index: 1; background: #fff; }}
  #univ-tbl thead th.u-sticky {{ background: #2c3e50; z-index: 2; }}
  #univ-tbl thead th.u-sticky:hover {{ background: #3d5370; }}
  .u-col0 {{ left: 0;     min-width: 68px;  max-width: 68px;  }}
  .u-col1 {{ left: 68px;  min-width: 180px; max-width: 180px;
             box-shadow: none; overflow: hidden; text-overflow: ellipsis; }}
  .u-col2 {{ left: 248px; min-width: 120px; max-width: 140px;
             box-shadow: 3px 0 6px -2px rgba(0,0,0,0.15); }}
  .sort-icon {{ font-size: 0.75em; opacity: 0.6; margin-left: 3px; }}
</style>

<div class="section" style="max-width:100%">
  <h2>Universum-Übersicht — {len(universe_all)} Large-/Mega-Caps &nbsp;|&nbsp; 12 Kriterien</h2>
  <p style="margin-bottom:10px;font-size:0.85em;color:#555">
    Auf Spalten-Header klicken zum Sortieren &nbsp;·&nbsp;
    <strong style="color:#1e8449">Grün</strong> = Kriterium erfüllt &nbsp;|&nbsp;
    <strong style="color:#922b21">Rot</strong> = nicht erfüllt.<br>
    <span style="opacity:0.8">E1: Trendqualität (alle 4 Pflicht) &nbsp;·&nbsp;
    E2: Pullback-Profil (5 Kriterien) &nbsp;·&nbsp;
    E3: Wiederanlauf-Signale (mind. 2/3 für Kandidaten)</span>
  </p>
  <div id="univ-wrap">
  <table id="univ-tbl">
    <thead>
      <tr>
        <th class="u-sticky u-col0" onclick="univSort(0)">Ticker<span class="sort-icon">⇅</span></th>
        <th class="u-sticky u-col1" onclick="univSort(1)">Name<span class="sort-icon">⇅</span></th>
        <th class="u-sticky u-col2" onclick="univSort(2)">Sektor<span class="sort-icon">⇅</span></th>
        <th title="Anzahl erfüllter Kriterien"        onclick="univSort(3)">Erfüllt<span class="sort-icon"> ▼</span></th>
        <th title="Kurs über 40W-MA (≈200-Tage-MA)" onclick="univSort(4)">E1.1<br>&gt;MA200<span class="sort-icon">⇅</span></th>
        <th title="Kurs über 10W-MA (≈50-Tage-MA)"  onclick="univSort(5)">E1.2<br>&gt;MA50<span class="sort-icon">⇅</span></th>
        <th title="ADX ≥ 25 (Trendstärke)"           onclick="univSort(6)">E1.3<br>ADX<span class="sort-icon">⇅</span></th>
        <th title="52-Wochen-Performance ≥ 0%"        onclick="univSort(7)">E1.4<br>52W%<span class="sort-icon">⇅</span></th>
        <th title="Pullback 5–15% vom 3M-Hoch"        onclick="univSort(8)">E2.1<br>PB%<span class="sort-icon">⇅</span></th>
        <th title="RSI 40–55"                         onclick="univSort(9)">E2.2<br>RSI<span class="sort-icon">⇅</span></th>
        <th title="Williams %R −80 bis −60"           onclick="univSort(10)">E2.3<br>W%R<span class="sort-icon">⇅</span></th>
        <th title="Hist. Volatilität 30T &lt; 25%"   onclick="univSort(11)">E2.4<br>HV30<span class="sort-icon">⇅</span></th>
        <th title="Beta &lt; 0,9"                     onclick="univSort(12)">E2.5<br>Beta<span class="sort-icon">⇅</span></th>
        <th title="MACD dreht nach oben"              onclick="univSort(13)">E3.1<br>MACD↑<span class="sort-icon">⇅</span></th>
        <th title="Momentum-Wechsel positiv"          onclick="univSort(14)">E3.2<br>Mom↑<span class="sort-icon">⇅</span></th>
        <th title="Bullische Kerze + Überdurchschn. Volumen" onclick="univSort(15)">E3.3<br>Vol↑<span class="sort-icon">⇅</span></th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
</div>

<script>
(function() {{
  var _sortCol = 3, _sortAsc = false;  // Standard: Erfüllt absteigend

  window.univSort = function(col) {{
    var tbl  = document.getElementById('univ-tbl');
    var tbody = tbl.querySelector('tbody');
    var rows  = Array.from(tbody.querySelectorAll('tr'));
    var ths   = tbl.querySelectorAll('thead th');

    _sortAsc = (col === _sortCol) ? !_sortAsc : true;
    _sortCol = col;

    // Sort-Icons zurücksetzen
    ths.forEach(function(th) {{
      th.querySelector('.sort-icon').textContent = '⇅';
    }});
    ths[col].querySelector('.sort-icon').textContent = _sortAsc ? ' ▲' : ' ▼';

    rows.sort(function(a, b) {{
      var av = a.cells[col].dataset.val || '';
      var bv = b.cells[col].dataset.val || '';
      var an = parseFloat(av), bn = parseFloat(bv);
      var cmp = (!isNaN(an) && !isNaN(bn))
                ? an - bn
                : av.localeCompare(bv, 'de', {{sensitivity: 'base'}});
      return _sortAsc ? cmp : -cmp;
    }});

    rows.forEach(function(r) {{ tbody.appendChild(r); }});
  }};
}})();
</script>"""


def _section_footer(report_date: str) -> str:
    return f"""
<div class="footer">
  Generiert am {report_date} &mdash; Zertifikate-Scanner &mdash;
  Alle Angaben ohne Gewähr. Keine Anlageberatung.
</div>"""


# ── Regelwerk-Seite ───────────────────────────────────────────────────────────

def build_regelwerk_page(rules: dict) -> str:
    """Generiert eine statische HTML-Seite, die das Screening-Regelwerk beschreibt."""
    e1 = rules.get("einstieg", {}).get("ebene1", {})
    e2 = rules.get("einstieg", {}).get("ebene2", {})
    e3 = rules.get("einstieg", {}).get("ebene3", {})
    ma = rules.get("marktampel", {})

    ma_long_w = e1.get("ma_long", 40)
    ma_mid_w  = e1.get("ma_mid",  10)
    ma_long_d = ma_long_w * 5   # Wochenäquivalent → Tage
    ma_mid_d  = ma_mid_w  * 5
    adx_min   = e1.get("adx_min", 25)
    perf_min  = e1.get("performance_52w_min_pct", 0)

    pb_min      = e2.get("pullback_min_pct", 5)
    pb_max      = e2.get("pullback_max_pct", 15)
    rsi_min     = e2.get("rsi_min", 40)
    rsi_max     = e2.get("rsi_max", 65)
    wr_min      = e2.get("williams_r_min", -80)
    wr_max      = e2.get("williams_r_max", -60)
    wr_hard_max = e2.get("williams_r_hard_max", -50)
    hv_max      = e2.get("hv30_max", 25)
    beta_max    = e2.get("beta_max", 0.9)
    rec_weeks         = e2.get("recovery_ma_cross_weeks", 8)
    rec_score_base    = e2.get("recovery_score_base", 50)
    rec_vol_bonus     = e2.get("recovery_vol_bonus_max", 10)
    rec_macd_bonus    = e2.get("recovery_macd_bonus_max", 10)
    max_dist_pct      = e2.get("max_distance_from_ma_pct", 20)
    hv_max_rec   = e2.get("hv30_max_recovery", 40)
    w_e2      = int(rules.get("scoring", {}).get("ebene2_weight", 0.6) * 100)
    w_e3      = int(rules.get("scoring", {}).get("ebene3_weight", 0.4) * 100)

    min_conf  = e3.get("min_confirmed", 2)
    vol_mult  = e3.get("volume_multiplier", 1.3)
    mom_len   = e3.get("momentum_length", 4)
    macd_lb   = e3.get("macd_lookback", 3)

    u_cap     = rules.get("universe", {}).get("min_market_cap_b", 150)
    lw        = rules.get("universe", {}).get("lookback_weeks", 60)

    from datetime import date as _date
    generated = _date.today().isoformat()

    def _row(label, value, detail=""):
        det = f'<br><span style="font-size:0.82em;color:#7f8c8d">{detail}</span>' if detail else ""
        return f"""<tr>
          <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;font-weight:600">{label}</td>
          <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;color:#2980b9;font-weight:700">{value}</td>
          <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;color:#555;font-size:0.88em">{detail}</td>
        </tr>"""

    def _section(title, color, badge, rows_html, intro):
        return f"""
<div style="background:#fff;border-radius:8px;padding:24px;margin-bottom:20px;
            box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:5px solid {color}">
  <h2 style="margin-bottom:6px;color:#2c3e50">{badge} {title}</h2>
  <p style="color:#555;font-size:0.9em;margin-bottom:16px">{intro}</p>
  <table style="width:100%;border-collapse:collapse">
    <thead>
      <tr style="background:#f8f9fa">
        <th style="padding:8px 14px;text-align:left;font-size:0.82em;color:#7f8c8d;
                   font-weight:600;text-transform:uppercase;letter-spacing:.04em">Kriterium</th>
        <th style="padding:8px 14px;text-align:left;font-size:0.82em;color:#7f8c8d;
                   font-weight:600;text-transform:uppercase;letter-spacing:.04em">Schwellenwert</th>
        <th style="padding:8px 14px;text-align:left;font-size:0.82em;color:#7f8c8d;
                   font-weight:600;text-transform:uppercase;letter-spacing:.04em">Bedeutung</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""

    e1_rows = (
        _row("E1.1 &nbsp; Kurs &gt; MA200",
             f"Kurs &gt; {ma_long_w}W-MA",
             f"Entspricht dem ≈{ma_long_d}-Tage-MA (Langfristtrend intakt). "
             f"Aktien unter ihrem 200-Tage-MA haben statistisch schlechtere Aufwärtschancen.") +
        _row("E1.2 &nbsp; Kurs &gt; MA50",
             f"Kurs &gt; {ma_mid_w}W-MA",
             f"Entspricht dem ≈{ma_mid_d}-Tage-MA (mittelfristiger Trend intakt). "
             f"Bestätigt, dass auch der kurzfristigere Impuls aufwärts gerichtet ist.") +
        _row("E1.3 &nbsp; ADX (Trendstärke)",
             f"ADX ≥ {adx_min}",
             f"Average Directional Index: Misst die Stärke des Trends unabhängig von seiner Richtung. "
             f"Werte &lt; 20 = Seitwärts, ≥ {adx_min} = klarer Trend. Verhindert Fehlsignale in trendlosen Märkten.") +
        _row("E1.4 &nbsp; 52-Wochen-Performance",
             f"Perf ≥ {perf_min} % &nbsp;(Ausnahme: 🔄 Recovery)",
             f"Die Aktie darf in den letzten 52 Wochen nicht gefallen sein. "
             "Relative Stärke gegenüber dem Markt ist ein zentrales Minervini-Kriterium. "
             "<strong>Ausnahme:</strong> Wird für Pfad B (Recovery) übersprungen — nach einem "
             "marktbedingten Crash ist die 52W-Performance systembedingt negativ, obwohl der "
             "Titel fundamental intakt ist. E1.1 und E1.2 (MA-Bedingungen) bleiben auch auf "
             "Pfad B verpflichtend.")
    )

    e2_rows = (
        _row("E2.1 &nbsp; Einstiegs-Profil (Entweder/Oder)",
             f"Pfad A: Pullback {pb_min}–{pb_max}% &nbsp;|&nbsp; Pfad B: 🔄 Recovery",
             f"<strong>Pfad A — Klassischer Pullback:</strong> Die Aktie hat sich vom Hoch der letzten "
             f"13 Wochen um {pb_min}–{pb_max}% zurückgezogen. Hartfilter: W%R &gt; {wr_hard_max} oder "
             f"HV30 ≥ {hv_max}% disqualifizieren (zu teures Optionsschein-Prämium). "
             f"<br><strong>Pfad B — Recovery nach Marktrückgang:</strong> Die Aktie hat den "
             f"{ma_long_w}W-MA (≈{ma_long_w * 5}T) in den letzten {rec_weeks} Wochen von unten "
             f"durchbrochen. Tritt nach marktbedingten Einbrüchen auf. "
             f"<strong>Dynamischer Score:</strong> Basis {rec_score_base} "
             f"+ bis zu {rec_vol_bonus} Punkte Volumen-Bonus (≥1.3× Ø-Vol) "
             f"+ bis zu {rec_macd_bonus} Punkte MACD-Bonus (dreht aufwärts + über Signal). "
             f"Max. Score Pfad B: {rec_score_base + rec_vol_bonus + rec_macd_bonus}/100 "
             f"(bewusst unter Pfad-A-Maximum, um Qualitätsunterschied abzubilden). "
             f"<br><strong>Pfad-B-Hartfilter:</strong> "
             f"(1) Kurs darf maximal {max_dist_pct}% über dem {ma_long_w}W-MA liegen — sonst ist der "
             f"Einstiegszeitpunkt verpasst. "
             f"(2) HV30 &lt; {hv_max_rec}% — bei höherer Volatilität ist das Optionsschein-Zeitwertpremium "
             f"zu teuer (W%R-Hartfilter entfällt weiterhin auf Pfad B). "
             f"Mindestens einer der beiden Pfade muss erfüllt sein.") +
        _row("E2.2 &nbsp; RSI",
             f"{rsi_min} – {rsi_max}",
             f"Relative Strength Index (14 Perioden). Werte von {rsi_min}–{rsi_max} zeigen "
             "eine abgekühlte, aber noch nicht überverkaufte Situation — idealer Einstiegsbereich "
             "nach einem Pullback in einem intakten Aufwärtstrend.") +
        _row("E2.3 &nbsp; Williams %R",
             f"≤ {wr_hard_max} (Hartfilter) · Optimal: {wr_min} bis {wr_max}",
             f"Misst die Position des Kurses relativ zum Hoch-Tief-Bereich der letzten 14 Wochen. "
             f"<strong>Hartfilter:</strong> W%R &gt; {wr_hard_max} disqualifiziert sofort "
             f"(kein echter Momentum-Pullback — Aktie zu nah am Hoch). "
             f"Optimalbereich {wr_min} bis {wr_max}: Aktie hat sich deutlich vom Hoch entfernt, "
             "aber ohne extremen Verkaufsdruck — günstiger Wiedereinstiegsbereich.") +
        _row("E2.4 &nbsp; Historische Volatilität (HV30)",
             f"&lt; {hv_max} % (Hartfilter)",
             f"Annualisierte Standardabweichung der Wochenrenditen der letzten 30 Wochen. "
             f"<strong>Hartfilter:</strong> HV30 ≥ {hv_max}% disqualifiziert sofort — "
             "hohe Volatilität treibt die Optionsschein-Prämie (Zeitwert) in die Höhe "
             "und erhöht das Verlustrisiko bei stagnierendem Kurs erheblich.") +
        _row("E2.5 &nbsp; Beta",
             f"&lt; {beta_max}",
             f"Korrelation zur Marktbewegung (SPY, 52 Wochen). Beta &lt; {beta_max} bedeutet: "
             "die Aktie schwankt weniger als der Gesamtmarkt. Bei Optionsscheinen mit Hebel ~3 "
             "bleibt der effektive Gesamthebel dadurch kontrollierbar.")
    )

    e2_scoring = f"""
<div style="background:#fef9e7;border-radius:6px;padding:12px 16px;margin-top:14px;
            font-size:0.88em;color:#7d6608">
  <strong>Hartfilter auf Pfad A (Pullback) — führen sofort zu Score 0:</strong>
  <ul style="margin:.4em 0 .6em 1.2em;line-height:1.7">
    <li>Pullback außerhalb {pb_min}–{pb_max}%</li>
    <li>Williams %R &gt; {wr_hard_max} (kein echter Momentum-Pullback)</li>
    <li>HV30 ≥ {hv_max}% (zu teures Zeitwertpremium)</li>
  </ul>
  <strong>Pfad B (Recovery)</strong> überspringt den W%R-Hartfilter, hat aber eigene Hartfilter:
  Kurs ≤ {max_dist_pct}% über {ma_long_w}W-MA &amp; HV30 &lt; {hv_max_rec}% — dynamischer Score: Basis {rec_score_base} + Volumen- + MACD-Bonus (max. {rec_score_base + rec_vol_bonus + rec_macd_bonus}).<br>
  <strong>Score-Berechnung (Pfad A):</strong> RSI, W%R (Optimalbereich), HV30, Beta und Pullback
  liefern je einen Teilscore 0–100 (100 = perfekt in der Mitte des Idealbereichs).
  Der E2-Gesamtscore ist der Durchschnitt dieser 5 Teilscores.
  <br>Gewichtung im Gesamtscore: E2 = {w_e2}%, E3 = {w_e3}%.
</div>"""

    e3_rows = (
        _row("E3.1 &nbsp; MACD dreht nach oben",
             "MACD-Linie steigend",
             f"Die MACD-Linie (12/26/9 Wochen) ist in der aktuellen Woche höher als in der Vorwoche "
             f"und hat über die letzten {macd_lb} Wochen ein lokales Tief gebildet. "
             "Frühes Signal einer Trendwende nach dem Pullback.") +
        _row("E3.2 &nbsp; Momentum-Wechsel positiv",
             "Mom(4W): − → +",
             f"Das {mom_len}-Wochen-Momentum (Kurs heute minus Kurs vor {mom_len} Wochen) "
             f"wechselt von negativ auf positiv. Bestätigt, dass der Kurs wieder Fahrt aufnimmt.") +
        _row("E3.3 &nbsp; Volumen-Bestätigung",
             f"Vol ≥ {vol_mult}× Ø",
             f"Die aktuelle Wochenkerze ist bullisch (Close &gt; Close Vorwoche) "
             f"und das Volumen liegt mindestens {vol_mult}× über dem 20-Wochen-Durchschnitt. "
             "Institutionelles Kaufinteresse als Bestätigung des Wiederanlaufs.")
    )

    e3_note = f"""
<div style="background:#eaf4fb;border-radius:6px;padding:12px 16px;margin-top:14px;
            font-size:0.88em;color:#1a5276">
  <strong>Mindestanforderung für Kaufkandidaten:</strong> Mindestens {min_conf} von 3
  E3-Kriterien müssen erfüllt sein. Für die Universum-Übersicht werden alle 3 einzeln
  angezeigt, unabhängig davon ob der Titel die anderen Ebenen besteht.
</div>"""

    universe_box = f"""
<div style="background:#fff;border-radius:8px;padding:24px;margin-bottom:20px;
            box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:5px solid #7f8c8d">
  <h2 style="margin-bottom:6px;color:#2c3e50">🌍 Universum</h2>
  <p style="color:#555;font-size:0.9em;margin-bottom:12px">
    Der Scanner läuft über alle Titel aus dem <strong>iShares Russell 1000 ETF (IWB)</strong>,
    gefiltert auf einen Marktwert ≥ <strong>{u_cap} Mrd. USD</strong>
    (Large-/Mega-Caps). Datengrundlage: wöchentliche OHLCV-Daten
    der letzten <strong>{lw} Wochen</strong>.
  </p>
  <p style="color:#555;font-size:0.88em">
    Alle drei Ebenen werden für jeden Titel berechnet. Kaufkandidaten müssen
    <em>alle</em> E1-Kriterien bestehen, einen positiven E2-Score erzielen
    und mindestens {min_conf}/3 E3-Signale zeigen.
    Die Universum-Übersicht im Report zeigt <em>alle</em> Titel, unabhängig davon ob sie die
    Filter bestehen — so behältst du den Gesamtüberblick.
  </p>
</div>"""

    ma_index    = ma.get("index", "^GSPC")
    ma_ema_fast = ma.get("ema_fast", 10)
    ma_ema_slow = ma.get("ema_slow", 50)
    ma_long_ma  = ma.get("ma_long", 200)
    vix_green   = ma.get("vix_green_max", 20)
    vix_red     = ma.get("vix_red_min", 25)

    marktampel_box = f"""
<div style="background:#fff;border-radius:8px;padding:24px;margin-bottom:20px;
            box-shadow:0 1px 4px rgba(0,0,0,0.08);border-left:5px solid #2c3e50">
  <h2 style="margin-bottom:6px;color:#2c3e50">🚦 Vorgelagerter Filter — Marktampel</h2>
  <p style="color:#555;font-size:0.9em;margin-bottom:16px">
    Die Marktampel ist der <strong>Türsteher</strong> des Screeners: Sie wird
    <em>vor</em> allen Einzeltitel-Ebenen geprüft. Nur bei <strong>GRÜNER</strong>
    Marktampel werden Kaufkandidaten angezeigt. GELB und ROT sperren Neukäufe —
    unabhängig davon, wie gut ein Einzeltitel in E1–E3 abschneidet.
    Die Universum-Übersicht (alle Scores) bleibt in jedem Marktregime sichtbar.
  </p>
  <table style="width:100%;border-collapse:collapse">
    <thead>
      <tr style="background:#f8f9fa">
        <th style="padding:8px 14px;text-align:left;font-size:0.82em;color:#7f8c8d;font-weight:600;text-transform:uppercase;letter-spacing:.04em">Status</th>
        <th style="padding:8px 14px;text-align:left;font-size:0.82em;color:#7f8c8d;font-weight:600;text-transform:uppercase;letter-spacing:.04em">Bedingung</th>
        <th style="padding:8px 14px;text-align:left;font-size:0.82em;color:#7f8c8d;font-weight:600;text-spacing:.04em">Aktion</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;font-weight:700;color:#1e8449">🟢 GRÜN</td>
        <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;color:#2980b9;font-weight:600">
          {ma_ema_fast}W-EMA &gt; {ma_ema_slow}W-EMA &nbsp;&amp;&amp;&nbsp;
          {ma_index} &gt; {ma_long_ma}W-MA &nbsp;&amp;&amp;&nbsp;
          VIX &lt; {vix_green}
        </td>
        <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;color:#555;font-size:0.88em">
          Neue Longs erlaubt — Kaufkandidaten werden angezeigt.
        </td>
      </tr>
      <tr>
        <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;font-weight:700;color:#9a7d0a">🟡 GELB</td>
        <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;color:#2980b9;font-weight:600">
          Signale gemischt: weniger als 2 negative Signale, aber nicht alle 3 grün
        </td>
        <td style="padding:10px 14px;border-bottom:1px solid #ecf0f1;color:#555;font-size:0.88em">
          <strong>Keine Neukäufe.</strong> Bestehende Positionen halten,
          Stopps enger setzen, keine Aufstockung.
        </td>
      </tr>
      <tr>
        <td style="padding:10px 14px;font-weight:700;color:#922b21">🔴 ROT</td>
        <td style="padding:10px 14px;color:#2980b9;font-weight:600">
          Mindestens 2 von 3 negativen Signalen:<br>
          {ma_ema_fast}W-EMA &lt; {ma_ema_slow}W-EMA &nbsp;/&nbsp;
          {ma_index} &lt; {ma_long_ma}W-MA &nbsp;/&nbsp;
          VIX &ge; {vix_red}
        </td>
        <td style="padding:10px 14px;color:#555;font-size:0.88em">
          <strong>Keine Neukäufe.</strong> Bestand mit engem Stopp überwachen.
          Bei gleichzeitiger Rot-Einzelampel: sofort aussteigen.
        </td>
      </tr>
    </tbody>
  </table>
  <div style="background:#fef9e7;border-radius:6px;padding:12px 16px;margin-top:14px;font-size:0.88em;color:#7d6608">
    <strong>Warum GELB = keine Neukäufe?</strong> Bei Optionsscheinen mit Hebel ~3 genügt
    eine moderate Marktkorrektur, um auch technisch saubere Einzeltitel-Setups
    zu 50–70% im Wert zu halbieren. Ein gemischtes Marktbild ist kein akzeptables
    Umfeld für neue Hebelpositionen.
  </div>
</div>"""

    r = rules.get("rollen", {})
    a = rules.get("ausstieg", {})
    ma_exit      = a.get("ma_exit", 50)
    rsi_ob       = a.get("rsi_overbought", 70)
    wr_ob        = a.get("williams_r_overbought", -20)
    hebel_min_r  = r.get("hebel_min", 2)
    tp_halb      = r.get("tp_halb_pct", 40)
    tp_empf      = r.get("tp_empfohlen_pct", 60)
    ziel_laufz   = r.get("ziel_laufzeit_monate", 18)
    ziel_hebel   = r.get("ziel_hebel", 3)
    zeit_roll_m  = r.get("zeitwert_min_restlaufzeit_monate", 6)

    exit_rows = (
        _row("Stop-Loss — Sofortausstieg",
             f"Wochenschlusskurs unter {ma_exit}W-MA<br>+ MACD-Kreuz unter Signallinie",
             "Beide Bedingungen zusammen lösen sofortigen Ausstieg aus. "
             "Der wöchentliche Schlusskurs ist maßgeblich — Intraday-Ausreißer ignorieren.") +
        _row("Stop-Loss — Früh-Signal",
             f"Kurs unter {ma_exit}W-MA ODER MACD unter Nulllinie",
             "Erste Warnstufe: Stopp enger setzen, Position beobachten. "
             "Noch kein zwingender Ausstieg, aber Handlungsbereitschaft herstellen.") +
        _row("Gewinnmitnahme — Teilverkauf",
             f"Schein-Gewinn est. ≥ {tp_halb}% → Hälfte nehmen<br>"
             f"Schein-Gewinn est. ≥ {tp_empf}% → Teilverkauf empfohlen",
             f"Proxy-Berechnung: Basiswert-Performance × Hebel (Kaufzeitpunkt). "
             f"Kein exaktes Options-Pricing — Theta und Vega werden nicht modelliert. "
             f"Bei ≥ {tp_halb}%: Hälfte realisieren, Rest laufen lassen. "
             f"Bei ≥ {tp_empf}%: aktiv Teilverkauf prüfen.") +
        _row("Gewinnmitnahme — Überkauft-Signal",
             f"RSI &gt; {rsi_ob} &amp;&amp; Williams %R &gt; {wr_ob}",
             "Beide Indikatoren zeigen überkaufte Zone — kurzfristige Gegenreaktion möglich. "
             "Nicht zwingend aussteigen, aber Gewinnmitnahme erwägen.")
    )

    roll_rows = (
        _row("Zeitwert-Roll",
             f"Restlaufzeit &lt; {zeit_roll_m} Monate bei intaktem Trend",
             f"Ab {zeit_roll_m} Monaten Restlaufzeit beschleunigt sich der Theta-Verlust. "
             f"Empfehlung: neuen Call OS mit Laufzeit +{ziel_laufz} Monate und Hebel ~{ziel_hebel} kaufen, "
             "alten Schein verkaufen. Trend muss intakt sein (Einzelampel GRÜN).") +
        _row("Strike-Roll",
             f"Hebel est. &lt; {hebel_min_r}×",
             f"Wenn der Basiswert stark gestiegen ist, sinkt der effektive Hebel. "
             f"Unter {hebel_min_r}× lohnt sich der Hebel-Effekt nicht mehr. "
             f"Empfehlung: neuen Call OS mit höherem Strike kaufen (Ziel-Hebel ~{ziel_hebel}), "
             "alten Schein mit Gewinn verkaufen.")
    )

    exit_note = f"""
<div style="background:#fadbd8;border-radius:6px;padding:12px 16px;margin-top:14px;
            font-size:0.88em;color:#922b21">
  <strong>Disclaimer:</strong> Schein-Gewinn-Schätzungen sind Näherungswerte ohne
  vollständiges Options-Pricing (kein Delta/Theta/Vega-Modell). Sie dienen als
  Orientierung, nicht als exaktes Verkaufssignal.
  Maßgeblich ist immer der tatsächliche Schein-Kurs im Broker-System.
</div>"""

    body = (
        marktampel_box +
        universe_box +
        _section("Ebene 1 — Trendqualität", "#27ae60", "🟢",
                 e1_rows,
                 "Alle 4 Kriterien müssen erfüllt sein. Ist auch nur eines nicht erfüllt, "
                 "wird der Titel als Kaufkandidat ausgeschlossen.") +
        _section("Ebene 2 — Pullback-Profil", "#f39c12", "🟡",
                 e2_rows + e2_scoring,
                 "Bewertet die Qualität des Pullbacks auf einer Skala von 0–100. "
                 "Ein Pullback außerhalb des Bereichs 5–15 % führt direkt zum Ausschluss.") +
        _section("Ebene 3 — Wiederanlauf-Bestätigung", "#2980b9", "🔵",
                 e3_rows + e3_note,
                 f"Mind. {min_conf} von 3 Signalen müssen vorliegen, um einen Wiedereinstieg zu bestätigen.") +
        _section("Ausstiegs-Regelwerk", "#e74c3c", "🔴",
                 exit_rows + exit_note,
                 "Stop-Loss und Gewinnmitnahme-Regeln für offene Optionsschein-Positionen.") +
        _section("Roll-Regelwerk", "#8e44ad", "🟣",
                 roll_rows,
                 "Wann und wie ein Optionsschein gerollt wird (Zeitwert-Roll oder Strike-Roll).")
    )

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Regelwerk — Zertifikate-Scanner</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f5f6fa; color: #2c3e50; font-size: 14px; line-height: 1.6; }}
    .container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
    header {{ background: #2c3e50; color: white; padding: 20px 24px;
             border-radius: 8px; margin-bottom: 20px; }}
    header h1 {{ font-size: 1.6em; margin-bottom: 4px; }}
    header .subtitle {{ opacity: 0.8; font-size: 0.9em; margin-bottom: 8px; }}
    header nav a {{ color: #7fb3d3; text-decoration: none; font-size: 0.85em; }}
    header nav a:hover {{ color: white; }}
    .footer {{ text-align:center;color:#95a5a6;font-size:0.8em;padding:16px 0 8px; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>📋 Regelwerk — Zertifikate-Scanner</h1>
      <p class="subtitle">Drei-Ebenen-Screening-System &mdash; Low-Vol Momentum Screener</p>
      <nav>
        <a href="index.html">← Alle Reports</a>
        &nbsp;|&nbsp;
        <a href="../zertifikate/portfolio.html">Portfolio verwalten</a>
        &nbsp;|&nbsp;
        <a href="../index.html">Hauptreport</a>
      </nav>
    </header>

    {body}

    <div class="footer">Generiert am {generated} &mdash; Alle Schwellenwerte aus zertifikate/rules.json</div>
  </div>
</body>
</html>"""


def save_regelwerk(html: str, output_dir: str = "docs/zertifikate") -> None:
    """Speichert die Regelwerk-Seite."""
    path = Path(output_dir) / "regelwerk.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"[REPORT] Regelwerk gespeichert: {path}")


# ── Index-Seite ────────────────────────────────────────────────────────────────

def _ampel_badge(report_file: Path) -> str:
    """Extract ampel status from a generated report HTML file and return a badge.

    Sucht nach dem Marktampel-Box-Div (ampel-box ampel-*), nicht nach den
    CSS-Klassendefinitionen, die in jedem Report vorhanden sind.
    """
    try:
        content = report_file.read_text(encoding="utf-8")
        # Nur das konkrete <div class="ampel-box ampel-X"> treffen, nicht die CSS-Regel
        if 'ampel-box ampel-gruen' in content:
            return '<span class="badge badge-green">🟢 Bullish</span>'
        if 'ampel-box ampel-rot' in content:
            return '<span class="badge badge-red">🔴 Bearish</span>'
        if 'ampel-box ampel-gelb' in content:
            return '<span class="badge badge-yellow">🟡 Neutral</span>'
    except Exception:
        pass
    return ""


def _update_index(report_date: str, out_dir: Path) -> None:
    index_path = out_dir / "index.html"
    report_files = sorted(out_dir.glob("????-??-??.html"), reverse=True)

    # weekday names in German
    _WD = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    _MO = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
           "Juli", "August", "September", "Oktober", "November", "Dezember"]

    def _human_date(stem: str) -> str:
        try:
            import datetime as dt
            d = dt.date.fromisoformat(stem)
            return f"{_WD[d.weekday()]}, {d.day:02d}. {_MO[d.month]} {d.year}"
        except Exception:
            return stem

    report_items = ""
    for f in report_files:
        r = f.stem
        badge = _ampel_badge(f)
        human = _human_date(r)
        badge_html = f'\n        {badge}' if badge else ""
        report_items += f"""
      <li class="report-item">
        <div>
          <a href="{r}.html">{r}</a>
          <div class="ri-meta">{human}</div>
        </div>{badge_html}
      </li>"""

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Zertifikate-Scanner — Alle Reports</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: Arial, sans-serif; margin: 0; background: #f0f3fa; color: #333; }}
    .g-nav   {{ background: #003d99; display: flex; align-items: center; padding: 0 1.5em;
                box-shadow: 0 2px 6px rgba(0,0,0,.22); flex-wrap: wrap; }}
    .g-brand {{ font-weight: bold; color: #fff; text-decoration: none; padding: .72em 1.1em .72em 0;
                margin-right: .5em; border-right: 1px solid rgba(255,255,255,.25);
                white-space: nowrap; font-size: .95em; }}
    .g-nav a {{ color: rgba(255,255,255,.82); text-decoration: none; padding: .72em .85em;
                font-size: .84em; white-space: nowrap; }}
    .g-nav a:hover  {{ color: #fff; background: rgba(255,255,255,.12); }}
    .g-nav a.active {{ color: #fff; box-shadow: inset 0 -3px rgba(255,255,255,.8); font-weight: 600; }}
    .page {{ max-width: 800px; margin: 0 auto; padding: 2em 1em 3em; }}
    .page-title {{ color: #003d99; font-size: 1.5em; margin: 0 0 .2em; }}
    .page-sub   {{ color: #888; font-size: .88em; margin: 0 0 1.8em; }}
    .section-h {{ color: #003d99; font-size: .78em; text-transform: uppercase;
                  letter-spacing: .08em; margin: 0 0 .75em;
                  padding-bottom: .35em; border-bottom: 2px solid #003d99; }}
    .quick-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: .85em; margin-bottom: 2.2em; }}
    .quick-card {{ background: #fff; border-radius: 8px; padding: .9em 1.1em;
                   box-shadow: 0 1px 4px rgba(0,0,0,.08); text-decoration: none; color: inherit;
                   border-left: 4px solid #27ae60; display: block;
                   transition: box-shadow .15s, transform .12s; }}
    .quick-card:hover {{ box-shadow: 0 4px 14px rgba(0,0,0,.13); transform: translateY(-1px); }}
    .qc-title {{ font-weight: bold; color: #27ae60; font-size: .92em; margin-bottom: .2em; }}
    .qc-desc  {{ font-size: .79em; color: #999; }}
    .quick-card.qc-blue {{ border-left-color: #2980b9; }}
    .quick-card.qc-blue .qc-title {{ color: #2980b9; }}
    .report-list {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5em; }}
    .report-item {{ background: #fff; border-radius: 8px; padding: .8em 1.1em;
                    box-shadow: 0 1px 4px rgba(0,0,0,.07);
                    display: flex; align-items: center; justify-content: space-between; gap: 1em; }}
    .report-item a {{ color: #003d99; text-decoration: none; font-weight: 600; font-size: .95em; }}
    .report-item a:hover {{ text-decoration: underline; }}
    .report-item .ri-meta {{ font-size: .78em; color: #aaa; margin-top: .15em; }}
    .badge {{ display: inline-flex; align-items: center; gap: .35em; padding: .25em .75em;
              border-radius: 20px; font-size: .78em; font-weight: 600; white-space: nowrap; }}
    .badge-green  {{ background: #d5f5e3; color: #1e8449; }}
    .badge-yellow {{ background: #fef9e7; color: #9a7d0a; }}
    .badge-red    {{ background: #fadbd8; color: #922b21; }}
    @media (max-width: 500px) {{ .quick-row {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <nav class="g-nav">
    <a href="../index.html" class="g-brand">📈 Weekly Screener</a>
    <a href="../trades.html">Trade Journal</a>
    <a href="../performance.html">Performance</a>
    <a href="index.html" class="active">Zertifikate</a>
    <a href="../blueprint.html">Blueprint</a>
  </nav>

  <div class="page">
    <h1 class="page-title">📊 Zertifikate-Scanner</h1>
    <p class="page-sub">Low-Vol Momentum Screener für Hebelprodukte &amp; Zertifikate</p>

    <h2 class="section-h">Schnellzugriff</h2>
    <div class="quick-row">
      <a href="portfolio.html" class="quick-card">
        <div class="qc-title">📁 Portfolio verwalten</div>
        <div class="qc-desc">Positionen, Einstellungen &amp; Transaktionen</div>
      </a>
      <a href="regelwerk.html" class="quick-card qc-blue">
        <div class="qc-title">📋 Regelwerk</div>
        <div class="qc-desc">Einstiegskriterien &amp; Ampel-Logik</div>
      </a>
    </div>

    <h2 class="section-h">Wochenreports</h2>
    <ul class="report-list">{report_items}
    </ul>
  </div>
</body>
</html>"""

    index_path.write_text(html, encoding="utf-8")
