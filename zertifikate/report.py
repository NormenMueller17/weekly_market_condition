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

    if markt.status == Ampel.ROT:
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
  <div class="container">
    <header>
      <h1>📊 Zertifikate-Scanner</h1>
      <p class="subtitle">Wochenbericht {report_date} &mdash; Low-Vol Momentum Screener</p>
      <nav>
        <a href="index.html">← Alle Reports</a>
        &nbsp;|&nbsp;
        <a href="../zertifikate/portfolio.html">Portfolio verwalten</a>
        &nbsp;|&nbsp;
        <a href="../index.html">Hauptreport</a>
      </nav>
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
    .container { max-width: 1100px; margin: 0 auto; padding: 20px; }
    header { background: #2c3e50; color: white; padding: 20px 24px;
             border-radius: 8px; margin-bottom: 20px; }
    header h1 { font-size: 1.6em; margin-bottom: 4px; }
    header .subtitle { opacity: 0.8; font-size: 0.9em; margin-bottom: 8px; }
    header nav a { color: #7fb3d3; text-decoration: none; font-size: 0.85em; }
    header nav a:hover { color: white; }
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
    return f"""
<div class="section">
  <div class="warnung">
    ⛔ Marktampel ROT — Keine neuen Kaufkandidaten werden angezeigt.<br>
    Bestehende Positionen mit engem Stopp halten. Bei gleichzeitiger Rot-Einzelampel sofort aussteigen.
  </div>
</div>"""


def _section_kandidaten(kandidaten: list[dict], markt: MarktampelResult) -> str:
    markt_hinweis = ""
    if markt.status == Ampel.GELB:
        markt_hinweis = '<div class="warnung" style="margin-bottom:12px">⚠️ Marktampel GELB — Kandidaten mit Vorsicht handeln. Stopps enger setzen, keine Aufstockung bestehender Positionen.</div>'

    rows = ""
    for k in kandidaten:
        score = k.get("score", 0)
        bar_w = min(int(score), 100)
        e3 = k.get("e3_confirmed", 0)
        e3_icons = ("✅" if k.get("e3_macd_dreht")    else "·") + \
                   ("✅" if k.get("e3_momentum_pos")  else "·") + \
                   ("✅" if k.get("e3_volumen_ok")    else "·")

        os_emp = k.get("os_empfehlung", {})

        rows += f"""<tr>
          <td><strong>{k['ticker']}</strong></td>
          <td>{k.get('close', '—')}</td>
          <td>{k.get('ma50', '—')}</td>
          <td>{k.get('perf_52w_pct', '—')}%</td>
          <td>{k.get('adx', '—')}</td>
          <td>{k.get('pullback_pct', '—')}%</td>
          <td>{k.get('rsi', '—')}</td>
          <td>{k.get('williams_r', '—')}</td>
          <td>{k.get('hv30', '—')}%</td>
          <td>{k.get('beta', '—')}</td>
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

        details = p.get("einzel_details", {})
        ausstieg = p.get("ausstieg", {})
        ausstieg_hinweis = ausstieg.get("empfehlung", "—")
        ausstieg_style = 'color:#e74c3c;font-weight:700' if "Sofort" in ausstieg_hinweis else ''

        rows += f"""<tr>
          <td><strong>{p.get('basiswert','—')}</strong></td>
          <td style="font-size:0.82em">{p.get('schein_name','—')}</td>
          <td>{p.get('kauf_datum','—')}</td>
          <td>{p.get('kauf_kurs_schein','—')}</td>
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
        <th>Restlaufzeit</th><th>Einzelampel</th><th>Zeitampel</th><th>Signal</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
</div>"""


def _section_roll(roll_kandidaten: list[dict]) -> str:
    rows = ""
    for p in roll_kandidaten:
        z_s = p.get("zeitampel", "?")
        dringlichkeit = "🔴 Dringend" if z_s == "rot" else "🟡 Prüfen"
        rows += f"""<tr>
          <td><strong>{p.get('basiswert','—')}</strong></td>
          <td style="font-size:0.82em">{p.get('schein_name','—')}</td>
          <td>{p.get('faelligkeitsdatum','—')}</td>
          <td>{p.get('restlaufzeit_monate','—')} Monate</td>
          <td>{dringlichkeit}</td>
          <td style="font-size:0.82em;color:#2980b9">
            Neuen Call OS kaufen: gleiche Parameter, Laufzeit +18 Monate,
            Hebel ~{p.get('hebel_kauf', 3)}
          </td>
        </tr>"""

    return f"""
<div class="section">
  <h2>Roll-Kandidaten ({len(roll_kandidaten)})</h2>
  <p style="margin-bottom:10px;font-size:0.88em;color:#555">
    Zeitwert-Ampel GELB oder ROT bei intaktem Einzeltrend.
    Empfehlung: neuen Schein mit gleichen Parametern kaufen (Laufzeit +18 Monate, Hebel ~3),
    alten Schein verkaufen.
  </p>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Basiswert</th><th>Schein</th><th>Fälligkeit</th>
        <th>Restlaufzeit</th><th>Dringlichkeit</th><th>Empfehlung</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
</div>"""


def _section_universe_overview(universe_all: list[dict], company_info: dict) -> str:
    """Zeigt alle Titel mit ihren 12 Screening-Metriken als grün/rot gefärbte Tabelle."""

    def _cell(ok: bool, val: str) -> str:
        bg    = "#d5f5e3" if ok else "#fadbd8"
        color = "#1e8449" if ok else "#922b21"
        return (
            f'<td style="background:{bg};color:{color};text-align:center;'
            f'white-space:nowrap;font-size:0.82em">{val}</td>'
        )

    rows = ""
    for t in universe_all:
        ticker  = t["ticker"]
        info    = company_info.get(ticker, {})
        name    = info.get("name", ticker)
        sector  = info.get("sector", "n/a")
        erfuellt = t["kriterien_erfuellt"]

        if erfuellt >= 9:
            badge_bg, badge_color = "#d5f5e3", "#1e8449"
        elif erfuellt >= 6:
            badge_bg, badge_color = "#fef9e7", "#9a7d0a"
        else:
            badge_bg, badge_color = "#fadbd8", "#922b21"

        rows += f"""<tr>
          <td><strong>{ticker}</strong></td>
          <td style="font-size:0.82em;white-space:nowrap">{name}</td>
          <td style="font-size:0.78em;color:#555;white-space:nowrap">{sector}</td>
          {_cell(t["e1_ma200"],    f">MA200<br>{t['close']}")}
          {_cell(t["e1_ma50"],     ">MA50")}
          {_cell(t["e1_adx"],      f"ADX<br>{t['adx_val']}")}
          {_cell(t["e1_perf"],     f"52W<br>{t['perf_52w']}%")}
          {_cell(t["e2_pullback"], f"PB%<br>{t['pullback_pct']}%")}
          {_cell(t["e2_rsi"],      f"RSI<br>{t['rsi_val']}")}
          {_cell(t["e2_williams"], f"W%R<br>{t['williams_val']}")}
          {_cell(t["e2_hv"],       f"HV30<br>{t['hv30_val']}%")}
          {_cell(t["e2_beta"],     f"Beta<br>{t['beta_val']}")}
          {_cell(t["e3_macd"],     "MACD↑<br>" + ("✓" if t["e3_macd"]     else "✗"))}
          {_cell(t["e3_momentum"], "Mom↑<br>"  + ("✓" if t["e3_momentum"] else "✗"))}
          {_cell(t["e3_volumen"],  "Vol↑<br>"  + ("✓" if t["e3_volumen"]  else "✗"))}
          <td style="text-align:center;font-weight:700;font-size:0.95em;
                     background:{badge_bg};color:{badge_color}">{erfuellt}/12</td>
        </tr>"""

    return f"""
<div class="section" style="max-width:100%">
  <h2>Universum-Übersicht — {len(universe_all)} Large-/Mega-Caps &nbsp;|&nbsp; 12 Kriterien</h2>
  <p style="margin-bottom:10px;font-size:0.85em;color:#555">
    Sortiert nach Anzahl erfüllter Kriterien (absteigend).
    <strong style="color:#1e8449">Grün</strong> = Kriterium erfüllt &nbsp;|&nbsp;
    <strong style="color:#922b21">Rot</strong> = nicht erfüllt.<br>
    <span style="opacity:0.8">E1: Trendqualität (alle 4 Pflicht) &nbsp;·&nbsp;
    E2: Pullback-Profil (5 Kriterien) &nbsp;·&nbsp;
    E3: Wiederanlauf-Signale (mind. 2/3 für Kandidaten)</span>
  </p>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Ticker</th>
        <th>Name</th>
        <th>Sektor</th>
        <th title="Kurs über 200W-MA">E1.1<br>&gt;MA200</th>
        <th title="Kurs über 50W-MA">E1.2<br>&gt;MA50</th>
        <th title="ADX ≥ 25 (Trendstärke)">E1.3<br>ADX</th>
        <th title="52-Wochen-Performance ≥ 0%">E1.4<br>52W%</th>
        <th title="Pullback 5–15% vom 3M-Hoch">E2.1<br>PB%</th>
        <th title="RSI 40–55">E2.2<br>RSI</th>
        <th title="Williams %%R −80 bis −60">E2.3<br>W%%R</th>
        <th title="Hist. Volatilität 30T &lt; 25%%">E2.4<br>HV30</th>
        <th title="Beta &lt; 0,9">E2.5<br>Beta</th>
        <th title="MACD dreht nach oben">E3.1<br>MACD↑</th>
        <th title="Momentum-Wechsel positiv">E3.2<br>Mom↑</th>
        <th title="Bullische Kerze + Überdurchschn. Volumen">E3.3<br>Vol↑</th>
        <th>Erfüllt</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
</div>"""


def _section_footer(report_date: str) -> str:
    return f"""
<div class="footer">
  Generiert am {report_date} &mdash; Zertifikate-Scanner &mdash;
  Alle Angaben ohne Gewähr. Keine Anlageberatung.
</div>"""


# ── Index-Seite ────────────────────────────────────────────────────────────────

def _update_index(report_date: str, out_dir: Path) -> None:
    index_path = out_dir / "index.html"
    reports = sorted(
        [f.stem for f in out_dir.glob("????-??-??.html")],
        reverse=True,
    )

    links = "\n".join(
        f'    <li><a href="{r}.html">{r}</a></li>'
        for r in reports
    )

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>Zertifikate-Scanner — Alle Reports</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 600px;
            margin: 40px auto; padding: 20px; color: #2c3e50; }}
    h1 {{ margin-bottom: 16px; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ margin-bottom: 8px; }}
    a {{ color: #2980b9; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    nav {{ margin-bottom: 20px; font-size: 0.9em; }}
  </style>
</head>
<body>
  <nav><a href="../index.html">← Hauptreport</a> &nbsp;|&nbsp;
       <a href="portfolio.html">Portfolio verwalten</a></nav>
  <h1>📊 Zertifikate-Scanner</h1>
  <p>Alle wöchentlichen Reports:</p>
  <ul>
{links}
  </ul>
</body>
</html>"""

    index_path.write_text(html, encoding="utf-8")
