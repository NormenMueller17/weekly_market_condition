import datetime as dt
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple
import pandas as pd
from jinja2 import Template

from indicators import rsi, macd, pct_above_ma
from breadth import compute_breadth_snapshots_with_advancers as compute_breadth_snapshots


# Token-Referenzen statt fester Hex-Werte, damit Positiv-/Negativ-Faerbung
# im HTML automatisch mit dem Farbschema (hell/dunkel, siehe :root-Tokens im
# HTML_TMPL-Stylesheet) mitgeht, obwohl diese Konstanten per Jinja direkt in
# inline `style="..."` interpoliert werden.
COLOR_POSITIVE  = "var(--success-bg)"
COLOR_NEGATIVE  = "var(--danger-bg)"
COLOR_POS_TEXT  = "var(--success-text)"
COLOR_NEG_TEXT  = "var(--danger-text)"

SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLY":  "Consumer Discret.",
    "XLP":  "Consumer Staples",
    "XLB":  "Materials",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "XLC":  "Communication",
}

HTML_TMPL = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root {
            --bg: #eef1f7; --surface: #ffffff; --surface-2: #f6f8fc;
            --border: #dde3ef; --border-strong: #c3cde0;
            --text: #10192b; --text-secondary: #4d5b73; --text-muted: #8593ab;
            --accent: #1f4fa3; --accent-soft: #e8eefa; --accent-text: #163c80;
            --success: #1c7c4d; --success-bg: #e6f6ec; --success-text: #145c39;
            --danger: #b3261e; --danger-bg: #fcebea; --danger-text: #8f1e18;
            --gold: #9a7b1f; --silver: #6b7688;
            --font-sans: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            --font-mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", Consolas, monospace;
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --bg: #0a0f1a; --surface: #121a2b; --surface-2: #0f1626;
                --border: #223049; --border-strong: #2e4066;
                --text: #e8edf7; --text-secondary: #a3b1c9; --text-muted: #6b7a97;
                --accent: #6c9bef; --accent-soft: #16233e; --accent-text: #9dbdf5;
                --success: #4cbf83; --success-bg: #12291d; --success-text: #6fd4a0;
                --danger: #e5716a; --danger-bg: #2c1414; --danger-text: #ef938d;
                --gold: #d4b04a; --silver: #9aa5b8;
            }
        }
        *, *::before, *::after { box-sizing: border-box; }
        body    { font-family: var(--font-sans); margin: 0; background: var(--bg); color: var(--text); }
        h1, h2, h3 { font-weight: 600; letter-spacing: -.01em; text-wrap: balance; color: var(--text); }
        h2 { font-size: 1.3em; margin: 1.8em 0 .7em; padding-bottom: .35em; border-bottom: 1px solid var(--border); }
        a { color: var(--accent); }
        .g-nav   { background: var(--surface); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 0 1.5em;
                   flex-wrap: wrap; position: sticky; top: 0; z-index: 100; }
        .g-brand { font-family: var(--font-mono); font-weight: 600; color: var(--text-muted); text-decoration: none;
                   padding: .72em 1.1em .72em 0; margin-right: .5em; border-right: 1px solid var(--border);
                   white-space: nowrap; font-size: .78em; letter-spacing: .05em; text-transform: uppercase; }
        .g-nav a { color: var(--text-secondary); text-decoration: none; padding: .72em .85em;
                   font-size: .84em; white-space: nowrap; }
        .g-nav a:hover  { color: var(--accent); background: var(--accent-soft); }
        .g-nav a.active { color: var(--accent-text); box-shadow: inset 0 -2px var(--accent); font-weight: 600; }
        .page   { max-width: 1200px; margin: 0 auto; padding: 2em 1em 3em; }
        table   { border-collapse: collapse; margin-bottom: 2em; font-size: 0.93em; width: 100%; }
        th, td  { border: none; border-bottom: 1px solid var(--border); padding: 0.5em 0.9em; text-align: right; }
        th      { background: var(--surface-2); color: var(--text-muted); font-weight: 600; white-space: nowrap;
                   font-size: .78em; letter-spacing: .04em; text-transform: uppercase; border-bottom: 1px solid var(--border-strong); }
        th.left, td.left { text-align: left; }
        tbody tr:hover td, tr:hover td { background-color: var(--surface-2); }
        /* Zahlenspalten: mono + tabular-nums, erkannt an der uebliche Inline-Ausrichtung */
        td[style*="text-align:right"], td[style*="text-align: right"],
        td[style*="text-align:center"], td[style*="text-align: center"] {
            font-family: var(--font-mono); font-variant-numeric: tabular-nums;
        }
        .pos    { color: var(--success-text); }
        .neg    { color: var(--danger-text); }
        .btn-sa {
            display: inline-block;
            padding: 2px 6px;
            background-color: var(--accent);
            color: #ffffff;
            font-size: 0.8em;
            border-radius: 4px;
            text-decoration: none;
        }
        .btn-sa:hover { background-color: var(--accent-text); }
        /* Leader cards */
        .leader-grid { width: 100%; border-collapse: collapse; }
        .leader-cell { width: 50%; padding: 5px; vertical-align: top; }
        .leader-card {
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 12px 14px;
            background: var(--surface);
        }
        /* Portfolio cards */
        .portfolio-summary {
            background: var(--surface-2);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 12px 16px;
            margin-bottom: 1em;
            border-collapse: separate;
            width: 100%;
        }
        .portfolio-summary td {
            border: none;
            text-align: center;
            padding: 4px 12px;
            font-size: 0.95em;
        }
        .portfolio-summary .summary-label {
            font-size: 0.75em;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .portfolio-summary .summary-value {
            font-size: 1.1em;
            font-weight: bold;
            font-family: var(--font-mono);
        }
        @media only screen and (max-width: 600px) {
            body { margin: 0.8em; }
            .leader-cell { display: block !important; width: 100% !important; box-sizing: border-box; }
            .portfolio-summary td { display: block !important; width: 100% !important; text-align: left; padding: 2px 0; }
        }
        th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
        th.sortable:hover { background: var(--accent-soft); color: var(--accent-text); }
        th.sortable::after { content: ' ⇅'; font-size: 0.9em; color: var(--text-muted); }
        th.sortable.asc::after  { content: ' ▲'; color: var(--accent); }
        th.sortable.desc::after { content: ' ▼'; color: var(--accent); }
        /* Meter-Balken fuer Score/RS-Zellen */
        .meter { display: inline-flex; align-items: center; gap: .5em; justify-content: flex-end; }
        .meter-track { width: 46px; height: 5px; border-radius: 3px; background: var(--surface-2); overflow: hidden; flex-shrink: 0; }
        .meter-fill { height: 100%; background: var(--accent); border-radius: 3px; }
        .meter-num { font-family: var(--font-mono); font-weight: 600; min-width: 1.6em; text-align: right; }
        /* Marktampel */
        .ampel-wrap { position: relative; display: inline-block; margin-bottom: 1.5em; }
        .ampel-badge {
            display: inline-flex; align-items: center; gap: 8px;
            padding: 8px 18px; border-radius: 22px; font-weight: bold;
            font-size: 1.05em; cursor: default; border: 1px solid var(--border);
            user-select: none;
        }
        .ampel-tooltip {
            display: none; position: absolute; left: 0; top: 115%;
            background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
            padding: 10px 16px; min-width: 300px; z-index: 100;
            box-shadow: 0 8px 24px rgba(10,15,26,.18); font-size: 0.88em;
            white-space: nowrap;
        }
        .ampel-wrap:hover .ampel-tooltip { display: block; }
        .ampel-tooltip table { border: none; margin: 4px 0 0 0; font-size: 1em; }
        .ampel-tooltip td { border: none; padding: 3px 8px; }
        .ampel-dots { display: flex; gap: 5px; margin: 6px 2px 0; }
        .ampel-dot  { width: 9px; height: 9px; border-radius: 50%; background: var(--success); }
        .ampel-dot.unmet { background: transparent; border: 1.5px solid var(--border-strong); }
    </style>
    <script>
    function sortTable(th) {
        var table = th.closest('table');
        var tbody = table.querySelector('tbody') || table;
        var col   = th.cellIndex;
        var asc   = th.classList.toggle('asc');
        if (!asc) th.classList.toggle('desc');
        else th.classList.remove('desc');

        // Collect sortable row-groups (data row + optional scorecard row)
        var allRows = Array.from(tbody.querySelectorAll('tr')).slice(1); // skip header
        var groups = [];
        var i = 0;
        while (i < allRows.length) {
            var group = [allRows[i]];
            // If next row is a scorecard row (has colspan), attach it to this group
            if (allRows[i+1]) {
                var cells = allRows[i+1].querySelectorAll('td');
                if (cells.length === 1 && cells[0].colSpan > 1) {
                    group.push(allRows[i+1]);
                    i++;
                }
            }
            groups.push(group);
            i++;
        }

        var parse = function(txt) {
            var s = txt.replace(/[+%$,]/g, '').trim();
            var n = parseFloat(s);
            return isNaN(n) ? s.toLowerCase() : n;
        };

        groups.sort(function(a, b) {
            var ta = parse(a[0].cells[col] ? a[0].cells[col].innerText : '');
            var tb = parse(b[0].cells[col] ? b[0].cells[col].innerText : '');
            if (ta < tb) return asc ? -1 : 1;
            if (ta > tb) return asc ? 1 : -1;
            return 0;
        });

        // Clear header sort indicators on siblings
        th.closest('tr').querySelectorAll('th').forEach(function(t) {
            if (t !== th) { t.classList.remove('asc'); t.classList.remove('desc'); }
        });

        groups.forEach(function(g) { g.forEach(function(r) { tbody.appendChild(r); }); });
    }
    </script>
</head>
{%- macro rs_meter(val) -%}
  {%- set v = val | float(default=none) -%}
  {%- if v is not none -%}
  <div class="meter"><div class="meter-track"><div class="meter-fill" style="width:{{ [v, 100] | min }}%"></div></div><span class="meter-num">{{ '%.0f' % v }}</span></div>
  {%- else -%}
  <span class="meter-num">–</span>
  {%- endif -%}
{%- endmacro -%}
{%- macro leader_card(idx, row) -%}
        <div class="leader-card">
          <!-- Ticker + SA-Button + Close -->
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">
            <span>
              <strong style="font-size:1.05em;color:#003d99">{{ idx }}</strong>
              &nbsp;{{ row["SA"] | safe }}
            </span>
            <strong style="font-size:1.05em">{{ row["_card_close"] }}</strong>
          </div>
          <!-- Unternehmensname + Branche -->
          <div style="font-size:0.87em;color:#333;margin-bottom:1px">{{ row["Company"] }}</div>
          <div style="font-size:0.78em;color:#aaa;margin-bottom:8px">{{ row["Industry"] }}</div>
          <!-- Score + RS + ΔRS -->
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:5px">
            <span style="background:#e8f5e9;color:#2e7d32;padding:2px 7px;border-radius:4px;font-weight:bold;font-size:0.82em">⭐ {{ row["score"] | int }}/8</span>
            <span style="font-size:0.87em;display:inline-flex;align-items:center;gap:.4em">RS&nbsp;{{ rs_meter(row.get("RS (O'Neil)", none)) }}</span>
            <span style="font-size:0.87em;color:
              {%- if row["ΔRS 4W"] is number and row["ΔRS 4W"] > 0 %}#2e7d32
              {%- elif row["ΔRS 4W"] is number and row["ΔRS 4W"] < 0 %}#c62828
              {%- else %}#555{% endif %}">
              ΔRS&nbsp;<strong>{{ row["_card_drs"] }}</strong>
            </span>
          </div>
          <!-- Fundamentaldaten -->
          <div style="font-size:0.82em;color:#555;margin-bottom:5px">
            Rev:&nbsp;{{ row["_card_rev"] }}&nbsp;&nbsp;EPS:&nbsp;{{ row["_card_eps"] }}
          </div>
          <!-- Muster + Abstand 52W High -->
          <div style="font-size:0.82em;color:#555;display:flex;gap:8px;flex-wrap:wrap;margin-bottom:5px">
            {% if row["VCP"] or row["Launchpad"] %}
            <span style="background:#fff8e1;padding:1px 6px;border-radius:3px">
              📐&nbsp;{% if row["VCP"] and row["Launchpad"] %}VCP+Launchpad{% elif row["VCP"] %}VCP{% else %}Launchpad{% endif %}
            </span>
            {% endif %}
            {% if row["_card_dist"] != '–' %}
            <span>Dist 52W&nbsp;H:&nbsp;{{ row["_card_dist"] }}%</span>
            {% endif %}
          </div>
          <!-- Kauf-Filter-Status -->
          {% set fails = row.get("_filter_fails", "–") %}
          <div style="font-size:0.78em;font-weight:bold;
            {% if fails == '✅' %}color:#2e7d32{% else %}color:#c62828{% endif %}">
            {% if fails == '✅' %}✅ Kaufkandidat{% else %}❌ {{ fails | safe }}{% endif %}
          </div>
        </div>
{%- endmacro -%}
<body>
  <nav class="g-nav">
    <a href="../index.html" class="g-brand">📈 Weekly Screener</a>
    <a href="../trades.html">Trade Journal</a>
    <a href="../performance.html">Performance</a>
    <a href="../zertifikate/index.html">Zertifikate</a>
    <a href="../blueprint.html">Blueprint</a>
  </nav>
  <div class="page">
    {% if test_mode %}
    <div style="background:#b30000;color:#fff;font-weight:bold;font-size:1.1em;padding:10px 16px;border-radius:6px;margin-bottom:1em;letter-spacing:.03em;">
      ⚠️ TEST-MODUS — Keine Orders wurden platziert oder storniert
    </div>
    {% endif %}
    <h1>Weekly US Market Report</h1>
    <p><strong>Report-Woche:</strong> {{ report_date }}</p>

    {% if ampel or nhnl_badge %}
    <div style="display:flex;flex-wrap:wrap;gap:1em;align-items:flex-start;">
      {% if ampel %}
      <div class="ampel-wrap">
        <div class="ampel-badge" style="background:{{ ampel.bg }};color:{{ ampel.color }}">
          {{ ampel.emoji }} Marktampel: <strong>{{ ampel.label }}</strong> &nbsp;({{ ampel.score }}/6)
        </div>
        <div class="ampel-dots">
          {% for c in ampel.criteria %}
          <span class="ampel-dot {{ '' if c.met else 'unmet' }}" title="{{ c.name }}: {{ c.value }}"></span>
          {% endfor %}
        </div>
        <div class="ampel-tooltip">
          <strong>Marktampel-Kriterien</strong>
          <table>
            {% for c in ampel.criteria %}
            <tr>
              <td>{{ "✅" if c.met else "❌" }}</td>
              <td>{{ c.name }}</td>
              <td style="color:#888;padding-left:12px">{{ c.value }}</td>
            </tr>
            {% endfor %}
          </table>
        </div>
      </div>
      {% endif %}
      {% if nhnl_badge %}
      <div class="ampel-wrap">
        <div class="ampel-badge" style="background:{{ nhnl_badge.bg }};color:{{ nhnl_badge.color }}">
          {{ nhnl_badge.emoji }} NH/NL-Ratio: <strong>{{ "%.0f"|format(nhnl_badge.ratio) }}%</strong>
          &nbsp;<span style="font-weight:normal;opacity:.8">({{ nhnl_badge.nh }} Hochs / {{ nhnl_badge.nl }} Tiefs)</span>
        </div>
      </div>
      {% endif %}
    </div>
    {% endif %}

    <h2>1) Marktbreite – Vergleich</h2>
    <table>
        <tr>
            <th class="left"></th>
            {% for col in breadth_snap.columns %}
              <th class="left">{{ col }}</th>
            {% endfor %}
            <th class="left">Trend (13W)</th>
        </tr>
        {% for row in breadth_snap.index %}
        <tr>
            <td class="left">{{ row }}</td>
            {% for col in breadth_snap.columns %}
              {% set val = breadth_snap.loc[row, col] %}
              {% set ref = breadth_snap.loc[row, "Woche −1"] %}
              {% set is_high_good = "Tiefs" in row %}
              {% if col == "Aktuelle Woche" and ref is not none and val is not none %}
                {% if (val > ref and not is_high_good) or (val < ref and is_high_good) %}
                  <td style="background-color:{{ COLOR_POSITIVE }};color:{{ COLOR_POS_TEXT }};font-weight:bold">{{ '%.2f%%' % val if '%' in row else val|int }}</td>
                {% elif (val < ref and not is_high_good) or (val > ref and is_high_good) %}
                  <td style="background-color:{{ COLOR_NEGATIVE }};color:{{ COLOR_NEG_TEXT }};font-weight:bold">{{ '%.2f%%' % val if '%' in row else val|int }}</td>
                {% else %}
                  <td>{{ '%.2f%%' % val if '%' in row else val|int }}</td>
                {% endif %}
              {% else %}
                <td>{{ '%.2f%%' % val if '%' in row else val|int }}</td>
              {% endif %}
            {% endfor %}
            <td>{{ breadth_sparklines.get(row, "") | safe }}</td>
        </tr>
        {% endfor %}
    </table>
    
    {% if sector_rows %}
    <h2>1b) Sektor-Performance (Wochenbasis)</h2>
    <div style="overflow-x:auto">{{ sector_bar_svg | safe }}</div>
    {% endif %}

    {% if sector_heatmap and sector_heatmap.rows %}
    <h2>1c) Sektor-Rotation ({{ sector_heatmap.dates|length }} Wochen)</h2>
    <p style="color:var(--text-secondary);font-size:.9em;margin:-.4em 0 1em;">
      Wochenrendite je Sektor-ETF, oben = aktuell stärkster Sektor. Zeigt, wie sich Führerschaft über Zeit verschiebt, nicht nur den Momentanzustand.
    </p>
    <div style="overflow-x:auto">
    <table>
      <tr>
        <th class="left">Sektor</th>
        {% for d in sector_heatmap.dates %}
          <th style="text-align:center">{{ d }}</th>
        {% endfor %}
      </tr>
      {% for row in sector_heatmap.rows %}
      <tr>
        <td class="left">{{ row.name }} <span style="color:var(--text-muted);font-size:.85em">{{ row.ticker }}</span></td>
        {% for cell in row.cells %}
          <td style="text-align:center;background-color:{{ cell.bg }};font-weight:600">{{ cell.label }}</td>
        {% endfor %}
      </tr>
      {% endfor %}
    </table>
    </div>
    {% endif %}

    <h2>2) Trend & Momentum (Weekly)</h2>
    <table>
        <tr>
            <th class="left">Metrik</th>
            {% for col in idx.columns %}
                <th class="left">{{ col }}</th>
            {% endfor %}
        </tr>
        {% for row in idx.index %}
        <tr>
            <td class="left">{{ row }}</td>
            {% for col in idx.columns %}
                {% set val = idx.loc[row, col] %}
                {% if row in ["Δ WoW", "Δ RSI", "Δ MACD", "vs 10W MA"] %}
                    <td style="background-color:{{ COLOR_POSITIVE if val > 0 else COLOR_NEGATIVE if val < 0 else 'transparent' }};color:{{ COLOR_POS_TEXT if val > 0 else COLOR_NEG_TEXT if val < 0 else 'inherit' }};font-weight:{{ 'bold' if val != 0 else 'normal' }}">{{ '%.2f%%' % val }}</td>
                {% elif row == 'RSI(14)' %}
                    <td>{{ '%.1f' % val }}</td>
                {% else %}
                    <td>{{ '%.2f' % val }}</td>
                {% endif %}
            {% endfor %}
        </tr>
        {% endfor %}
    </table>
    <h3>Divergenzanalyse</h3>
    <p>{{ divergences | safe }}</p>
        
    <h2>3) Risiko & Sentiment</h2>
    <table>
        <tr>
            <th class="left">Metrik</th>
            <th>Aktuell</th>
            <th>Vorwoche</th>
            <th>Δ</th>
        </tr>
        {% for row in risk.iterrows() %}
        {% set name = row[0] %}
        {% set vals = row[1] %}
        {% set farbe = vals['Δ_farbe'] if 'Δ_farbe' in vals else ('pos' if vals['Δ'] > 0 else 'neg' if vals['Δ'] < 0 else '') %}
        <tr>
            <td class="left">{{ name }}</td>
            <td>{{ '%.2f' % vals['Aktuell'] }}</td>
            <td>{{ '%.2f' % vals['Vorwoche'] }}</td>
            <td style="background-color:{{ COLOR_POSITIVE if farbe == 'pos' else COLOR_NEGATIVE if farbe == 'neg' else 'transparent' }};color:{{ COLOR_POS_TEXT if farbe == 'pos' else COLOR_NEG_TEXT if farbe == 'neg' else 'inherit' }};font-weight:{{ 'bold' if farbe in ('pos','neg') else 'normal' }}">
                {{ '%.2f' % vals['Δ'] }}
            </td>
        </tr>
        {% endfor %}
    </table>

    <h2>4) Fazit</h2>
    <p>{{ summary }}</p>

    {% if recent_trades %}
    <h2>5) 📋 Portfolio Trades – letzte Woche</h2>
    <table>
      <tr>
        <th class="left">Symbol</th>
        <th class="left">Unternehmen</th>
        <th class="left">Muster</th>
        <th>Entry</th>
        <th>Exit</th>
        <th>Entry $</th>
        <th>Exit $</th>
        <th>P&amp;L $</th>
        <th>P&amp;L %</th>
        <th class="left">Grund</th>
      </tr>
      {% for t in recent_trades %}
      {% set pl_pos = t.realized_pl > 0 %}
      <tr>
        <td class="left"><strong style="color:#003d99">{{ t.symbol }}</strong></td>
        <td class="left" style="font-size:0.85em;color:#555">{{ t.company or '–' }}</td>
        <td class="left" style="font-size:0.85em">{{ t.pattern or '–' }}</td>
        <td>{{ t.entry_date }}</td>
        <td>{{ t.exit_date }}</td>
        <td>${{ "%.2f"|format(t.entry_price) }}</td>
        <td>${{ "%.2f"|format(t.exit_price) }}</td>
        <td style="background-color:{{ COLOR_POSITIVE if pl_pos else COLOR_NEGATIVE }};color:{{ COLOR_POS_TEXT if pl_pos else COLOR_NEG_TEXT }};font-weight:bold">
          {{ '+' if pl_pos else '' }}${{ "{:,.0f}".format(t.realized_pl) }}
        </td>
        <td style="background-color:{{ COLOR_POSITIVE if pl_pos else COLOR_NEGATIVE }};color:{{ COLOR_POS_TEXT if pl_pos else COLOR_NEG_TEXT }};font-weight:bold">
          {{ '+' if pl_pos else '' }}{{ "%.1f"|format(t.realized_plpc) }}%
        </td>
        <td class="left" style="font-size:0.85em">{{ t.exit_reason_label }}</td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}

    {% if alpaca_portfolio %}
    <h2>6) 💼 Alpaca Portfolio</h2>

    {# ── Summary Banner ── #}
    {% set pl = alpaca_portfolio.unrealized_pl %}
    {% set pl_pct = (pl / (alpaca_portfolio.equity - pl) * 100) if (alpaca_portfolio.equity - pl) != 0 else 0 %}
    <table class="portfolio-summary">
      <tr>
        <td>
          <div class="summary-label">Cash</div>
          <div class="summary-value">${{ "{:,.0f}".format(alpaca_portfolio.cash) }}</div>
        </td>
        <td>
          <div class="summary-label">Equity</div>
          <div class="summary-value">${{ "{:,.0f}".format(alpaca_portfolio.equity) }}</div>
        </td>
        <td>
          <div class="summary-label">Unrealized P&amp;L</div>
          <div class="summary-value {{ 'pos' if pl >= 0 else 'neg' }}">
            {{ '+' if pl >= 0 else '' }}${{ "{:,.0f}".format(pl) }}
            &nbsp;({{ '+' if pl_pct >= 0 else '' }}{{ "%.1f"|format(pl_pct) }}%)
          </div>
        </td>
        <td>
          <div class="summary-label">Positionen</div>
          <div class="summary-value">{{ alpaca_portfolio.positions | length }}</div>
        </td>
      </tr>
    </table>

    {# ── Position Cards (2-spaltig, auf Mobile 1-spaltig) ── #}
    {% if alpaca_portfolio.positions %}
    {% set ns = namespace(col=0) %}
    <table class="leader-grid">
    {% for pos in alpaca_portfolio.positions | sort(attribute='market_value', reverse=True) %}
      {% if ns.col == 0 %}<tr>{% endif %}
      <td class="leader-cell">
        <div class="leader-card">
          <div style="font-size:1.05em;font-weight:bold;margin-bottom:2px">{{ pos.symbol }}</div>
          <div style="color:#888;font-size:0.82em;margin-bottom:6px">
            {{ "{:.0f}".format(pos.qty) }} Stk &nbsp;·&nbsp; Ø ${{ "{:,.2f}".format(pos.avg_entry_price) }}
          </div>
          <div style="margin-bottom:4px">
            Aktuell: <strong>${{ "{:,.2f}".format(pos.current_price) }}</strong>
          </div>
          <div class="{{ 'pos' if pos.unrealized_pl >= 0 else 'neg' }}" style="font-size:1.05em;font-weight:bold;margin-bottom:4px">
            {{ '+' if pos.unrealized_pl >= 0 else '' }}${{ "{:,.0f}".format(pos.unrealized_pl) }}
            &nbsp;&nbsp;
            {{ '+' if pos.unrealized_plpc >= 0 else '' }}{{ "%.1f"|format(pos.unrealized_plpc) }}%
            {{ '▲' if pos.unrealized_pl >= 0 else '▼' }}
          </div>
          <div style="color:#888;font-size:0.82em">Marktwert: ${{ "{:,.0f}".format(pos.market_value) }}</div>
        </div>
      </td>
      {% set ns.col = ns.col + 1 %}
      {% if ns.col == 2 %}</tr>{% set ns.col = 0 %}{% endif %}
    {% endfor %}
    {% if ns.col == 1 %}<td class="leader-cell"></td></tr>{% endif %}
    </table>
    {% else %}
    <p style="color:#888">Keine offenen Positionen.</p>
    {% endif %}

    {% else %}
    <h2>6) 💼 Alpaca Portfolio</h2>
    <p style="color:#cc2222;font-weight:600">
      ⚠️ Alpaca nicht erreichbar — Portfolio-Daten konnten nicht geladen werden.
      Journal-Sync wurde übersprungen; offene Positionen wurden nicht aktualisiert.
    </p>
    {% endif %}

    <h2>7) 📈 Kaufsignale (Blueprint-Regelwerk)</h2>

    {% if not signals %}
    {% if pages_url %}
    <p style="margin-bottom:0.6em">
      <a href="{{ pages_url }}" target="_blank"
         style="display:inline-block;padding:6px 18px;background:#003d99;color:white;
                text-decoration:none;border-radius:6px;font-weight:bold;font-size:0.88em">
        Vollständigen Report ansehen →
      </a>
    </p>
    {% endif %}
    <p style="color:#888">
        Keine Kaufsignale diese Woche —
        {% if not market_bullish %}
        <strong>Marktfilter aktiv</strong>: S&amp;P 500 10W EMA &lt; 20W EMA
        {% if sp500_breadth_pct is not none and sp500_breadth_pct < min_breadth_pct %}
        und Marktbreite {{ "%.1f"|format(sp500_breadth_pct) }}% &lt; {{ min_breadth_pct }}%.
        {% else %}.{% endif %}
        {% else %}
        Kriterien (Score ≥ 6/8 + Vol-Breakout + RS ≥ 70 + Industry Top 50) nicht erfüllt.
        {% endif %}
    </p>

    {% if not pages_url and not all_leaders.empty %}
    {# ── CLOUDFLARE PAGES: Fallback-Tabelle aller Screener-Kandidaten ── #}
    <p style="font-size:0.9em;color:#555;margin-bottom:0.6em">
        Screener-Kandidaten (Score ≥ 6/8, Top 20 nach Score/RS) — sortierbar, kein Handelssignal diese Woche:
    </p>
    <div style="overflow-x:auto">
    <table>
      <tr>
        <th class="left sortable" onclick="sortTable(this)">Ticker</th>
        <th class="left sortable" onclick="sortTable(this)">Unternehmen</th>
        <th class="left sortable" onclick="sortTable(this)">Industry</th>
        <th class="sortable" onclick="sortTable(this)">Score</th>
        <th class="sortable" onclick="sortTable(this)">Close</th>
        <th class="sortable" onclick="sortTable(this)" style="color:#1565c0">RS</th>
        <th class="sortable" onclick="sortTable(this)">ΔRS 4W</th>
        <th class="sortable" onclick="sortTable(this)" style="color:#1565c0;width:1px;white-space:normal;padding-left:0.4em;padding-right:0.4em">Dist 52W H %</th>
        <th class="sortable" onclick="sortTable(this)" style="color:#1565c0;width:1px;white-space:normal;padding-left:0.4em;padding-right:0.4em">Ind. Rank</th>
        <th class="sortable" onclick="sortTable(this)">ATR %</th>
        <th class="sortable" onclick="sortTable(this)" style="width:1px;white-space:normal;padding-left:0.4em;padding-right:0.4em">Vol-Score</th>
        <th class="sortable" onclick="sortTable(this)">MarketCap<br>(Mio $)</th>
        <th class="left sortable" onclick="sortTable(this)" style="color:#c62828">Scheitert an</th>
      </tr>
      {% for idx, row in all_leaders.iterrows() %}
      {% set rs_val = row.get("RS (O'Neil)", none) %}
      {% set dist_val = row.get("Dist to 52W High (%)", none) %}
      {% set ind_val = row.get("Industry Ranking", none) %}
      {% set drs_val = row["ΔRS 4W"] if "ΔRS 4W" in all_leaders.columns else none %}
      {% set pat = row.get("VCP", false) %}
      {% set lp  = row.get("Launchpad", false) %}
      <tr>
        <td class="left">
          <a href="https://stockanalysis.com/stocks/{{ idx }}" target="_blank"
             style="color:#003d99;font-weight:bold;text-decoration:none">{{ idx }}</a>
          {% if pat or lp %}
          <div style="font-size:0.68em;font-weight:bold;letter-spacing:.03em;margin-top:1px">
            {% if pat %}<span style="color:#b8860b">VCP</span>{% endif %}
            {% if pat and lp %}&nbsp;{% endif %}
            {% if lp %}<span style="color:#9e9e9e">LP</span>{% endif %}
          </div>
          {% endif %}
        </td>
        <td class="left" style="font-size:0.85em;color:#555">{{ row.get("Company", "–") }}</td>
        <td class="left" style="font-size:0.85em;color:#555">{{ row.get("Industry", "–") }}</td>
        <td style="text-align:center">{{ row.get("score", "–") }}</td>
        <td style="text-align:right">{{ row.get("Close", "–") }}</td>
        <td style="text-align:center;background:{% if rs_val != none and rs_val|float >= 70 %}var(--success-bg){% else %}transparent{% endif %}">
          {{ rs_meter(rs_val|float if rs_val != none else none) }}
        </td>
        <td style="text-align:center;color:{% if drs_val is not none and drs_val > 0 %}#2e7d32{% elif drs_val is not none and drs_val < 0 %}#c62828{% else %}#555{% endif %}">
          {% if drs_val is not none %}{% if drs_val > 0 %}+{% endif %}{{ "%.0f"|format(drs_val) }}{% else %}–{% endif %}
        </td>
        <td style="text-align:center;white-space:nowrap;padding-left:0.4em;padding-right:0.4em;background:{% if dist_val != none and dist_val|float <= 25 %}#e8f5e9{% else %}transparent{% endif %}">
          {{ dist_val if dist_val != none else "–" }}
        </td>
        <td style="text-align:center;white-space:nowrap;padding-left:0.4em;padding-right:0.4em;background:{% if ind_val != none and ind_val|float <= 50 %}#e8f5e9{% else %}transparent{% endif %}">
          {{ ind_val|float|round|int if ind_val != none else "–" }}
        </td>
        <td style="text-align:center">{{ row.get("ATR / Price (%)", "–") }}</td>
        {% set vol_bo = row.get("Vol-Breakout", false) %}
        {% set vol_sc = row.get("Volume Score", none) %}
        <td style="text-align:center;white-space:nowrap;padding-left:0.4em;padding-right:0.4em;background:{% if vol_bo %}#e8f5e9{% else %}transparent{% endif %}">
          {% if vol_sc is not none and vol_sc != "–" %}{{ "%.2f"|format(vol_sc|float) }}{% else %}–{% endif %}
        </td>
        <td style="text-align:right">{{ row.get("MarketCap (Mio USD)", "–") }}</td>
        <td class="left" style="font-size:0.82em;{% if row.get('_filter_fails','') == '✅' %}color:#2e7d32;font-weight:bold{% else %}color:#c62828{% endif %}">
          {{ row.get("_filter_fails", "–") }}
        </td>
      </tr>
      {% endfor %}
    </table>
    </div>
    {% endif %}

    {% else %}
    <p style="margin-bottom:0.8em">
        <strong>Marktfilter:</strong> S&amp;P 500 10W EMA &gt; 20W EMA ✅
        {% if sp500_breadth_pct is not none %}
        &nbsp;|&nbsp;<strong>Marktbreite:</strong> {{ "%.1f"|format(sp500_breadth_pct) }}% über 200d
        {% if sp500_breadth_pct >= min_breadth_pct %}✅{% else %}⚠️{% endif %}
        {% endif %}
        &nbsp;|&nbsp;
        <strong>Position:</strong> {{ (signals[0].position_size_pct * 100) | round(1) }}% des Kapitals
        ({{ "{:,.0f}".format(signals[0].position_value) }} €/$) &nbsp;|&nbsp;
        <strong>Kelly-Fraction:</strong> 1/3 &nbsp;|&nbsp;
        <strong>Signale gesamt:</strong> {{ signals | length }}
        {% if alpaca_cash is not none %}
        &nbsp;|&nbsp;<strong>Alpaca Cash:</strong> ${{ "{:,.0f}".format(alpaca_cash) }}
        {% if alpaca_positions %}
        &nbsp;|&nbsp;<strong>Offen ({{ alpaca_positions | length }}):</strong> {{ alpaca_positions | join(", ") }}
        {% endif %}
        {% endif %}
    </p>

    {% if pages_url %}
    {# ── KOMPAKTE EMAIL-VERSION: nur Top-Picks als Cards ── #}
    {% set top_picks = signals | selectattr("is_top_pick") | list %}
    {% set ns = namespace(col=0) %}
    <table class="leader-grid">
    {% for s in top_picks %}
      {% if ns.col == 0 %}<tr>{% endif %}
      <td class="leader-cell">
        <div class="leader-card" style="background:{% if s.is_top_pick %}#fffdf5{% else %}#fff{% endif %}">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">
            <span>
              <span style="background:#f5a623;color:white;padding:2px 7px;border-radius:10px;font-size:0.82em;font-weight:bold">🏆 {{ s.rank }}</span>
              &nbsp;<strong style="font-size:1.05em;color:#003d99">{{ s.ticker }}</strong>
            </span>
            <strong style="font-size:1.05em">${{ '%.2f' % s.entry_price }}</strong>
          </div>
          <div style="font-size:0.87em;color:#333;margin-bottom:2px">{{ s.company }}</div>
          <div style="font-size:0.78em;color:#aaa;margin-bottom:8px">{{ s.industry }}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:5px">
            <span style="background:
              {%- if '+' in s.pattern %}#fffde7
              {%- elif s.pattern == 'VCP' %}#e8f5e9
              {%- else %}#e3f2fd{% endif %};
              padding:2px 7px;border-radius:4px;font-size:0.82em;font-weight:bold">{{ s.pattern }}</span>
            {%- if s.is_reentry %}
            <span title="Wiedereinstieg: Titel war ausgestoppt und hat den alten Pivot zurückerobert"
                  style="background:#ede7f6;color:#4527a0;padding:2px 7px;border-radius:4px;
                         font-size:0.82em;font-weight:bold">↻ Versuch {{ s.reentry_attempt }}</span>
            {%- endif %}
            <span style="font-size:0.87em">RS&nbsp;<strong>{{ '%.0f' % s.rs_score if s.rs_score is not none else '–' }}</strong></span>
            <span style="font-size:0.87em;color:
              {%- if s.rs_delta_4w is not none and s.rs_delta_4w > 0 %}#2e7d32
              {%- elif s.rs_delta_4w is not none and s.rs_delta_4w < 0 %}#c62828
              {%- else %}#555{% endif %}">
              ΔRS&nbsp;<strong>{% if s.rs_delta_4w is not none %}{% if s.rs_delta_4w > 0 %}+{% endif %}{{ '%.0f' % s.rs_delta_4w }}{% else %}–{% endif %}</strong>
            </span>
          </div>
          <div style="font-size:0.82em;color:#555">
            Stop:&nbsp;<strong style="color:#e65100">{{ '%.2f' % s.stop_loss }}</strong>
            &nbsp;({{ (s.stop_loss_pct * 100) | round(1) }}%)
            &nbsp;&nbsp;Risiko:&nbsp;{{ ((s.risk_on_equity_pct * 100) | round(2)) }}%
          </div>
        </div>
      </td>
      {% set ns.col = ns.col + 1 %}
      {% if ns.col == 2 %}</tr>{% set ns.col = 0 %}{% endif %}
    {% endfor %}
    {% if ns.col == 1 %}<td class="leader-cell"></td></tr>{% endif %}
    </table>

    <div style="text-align:center;margin-top:16px">
      <a href="{{ pages_url }}" target="_blank"
         style="display:inline-block;padding:10px 24px;background:#003d99;color:white;
                text-decoration:none;border-radius:6px;font-weight:bold;font-size:0.95em">
        Alle {{ signals | length }} Signale ansehen →
      </a>
    </div>

    {% else %}
    {# ── VOLLSTÄNDIGE VERSION FÜR GITHUB PAGES ── #}
    <table>
      <tr>
        <th class="sortable" onclick="sortTable(this)">Rang</th>
        <th class="left sortable" onclick="sortTable(this)">Ticker</th>
        <th class="left sortable" onclick="sortTable(this)">Unternehmen</th>
        <th class="left sortable" onclick="sortTable(this)">Sektor / Branche</th>
        <th class="left sortable" onclick="sortTable(this)">Muster</th>
        <th class="sortable" onclick="sortTable(this)">Entry</th>
        <th class="sortable" onclick="sortTable(this)">Buy-Stop</th>
        <th class="sortable" onclick="sortTable(this)" title="Order verwerfen wenn Montag-Open über diesem Preis">Max. Gap</th>
        <th class="sortable" onclick="sortTable(this)">Stop-Loss</th>
        <th class="sortable" onclick="sortTable(this)">Stop %</th>
        <th class="sortable" onclick="sortTable(this)">BO-Level</th>
        <th class="sortable" onclick="sortTable(this)">Dist 52W H</th>
        <th class="sortable" onclick="sortTable(this)">RS</th>
        <th class="sortable" onclick="sortTable(this)">ΔRS 4W</th>
        <th class="sortable" onclick="sortTable(this)">Industry<br>Rank</th>
        <th class="sortable" onclick="sortTable(this)">ROE %</th>
        <th class="sortable" onclick="sortTable(this)">Op.Margin</th>
        <th class="sortable" onclick="sortTable(this)">EPS Growth Q</th>
        <th class="sortable" onclick="sortTable(this)">Rev. Growth</th>
        <th class="sortable" onclick="sortTable(this)" title="Wochenvolumen / Ø20T-Volumen">Vol Score</th>
        <th class="sortable" onclick="sortTable(this)">Position</th>
        <th class="sortable" onclick="sortTable(this)">Risiko / Equity</th>
      </tr>
      {% for s in signals %}
      {% set stop_pct_display  = (s.stop_loss_pct * 100)      | round(1) %}
      {% set risk_pct_display  = (s.risk_on_equity_pct * 100) | round(2) %}
      {% set risk_high         = s.risk_on_equity_pct > 0.018 %}
      {% set row_bg = "#fffbea" if s.is_top_pick else "transparent" %}
      <tr style="background-color:{{ row_bg }}">
        <td style="text-align:center;font-weight:bold">
          {% if s.is_top_pick %}
            <span style="background:#f5a623;color:white;padding:2px 7px;border-radius:10px;font-size:0.85em">
              🏆 {{ s.rank }}
            </span>
          {% else %}
            <span style="color:#aaa">{{ s.rank }}</span>
            <span title="Außerhalb des wöchentlichen Neukauf-Limits — es wurde kein Auftrag angelegt"
                  style="display:block;margin-top:2px;background:#eee;color:#666;padding:1px 5px;
                         border-radius:8px;font-size:0.65em;font-weight:normal;white-space:nowrap">
              kein Auftrag
            </span>
          {% endif %}
        </td>
        <td class="left">
          <a href="{{ s.sa_link }}" target="_blank"
             style="font-weight:bold;color:{% if s.is_top_pick %}#b35900{% else %}#003d99{% endif %}">
            {{ s.ticker }}
          </a>
        </td>
        <td class="left">{{ s.company }}</td>
        <td class="left" style="font-size:0.85em;color:#555">{{ s.sector }}<br>{{ s.industry }}</td>
        <td class="left" style="font-weight:bold;background-color:
          {%- if '+' in s.pattern %}#fffde7
          {%- elif s.pattern == 'VCP' %}#e8f5e9
          {%- else %}#e3f2fd{% endif %}">{{ s.pattern }}{% if s.is_reentry %}
          <span title="Wiedereinstieg nach Ausstoppung — alter Pivot zurückerobert"
                style="color:#4527a0">↻{{ s.reentry_attempt }}</span>{% endif %}</td>
        <td><strong>{{ '%.2f' % s.entry_price }}</strong></td>
        <td style="background-color:#e8f4fd;font-weight:bold">{{ '%.2f' % s.buy_stop }}</td>
        <td style="background-color:#fdecea;font-weight:bold" title="Order verwerfen wenn Montag-Open über diesem Preis">{{ '%.2f' % s.max_gap_price }}</td>
        <td style="background-color:#fff3e0">{{ '%.2f' % s.stop_loss }}</td>
        <td style="background-color:#fff3e0">{{ stop_pct_display }}%</td>
        <td>{{ '%.2f' % s.breakout_level if s.breakout_level else '–' }}</td>
        <td style="background-color:
          {%- if s.dist_52w_high_pct is not none and s.dist_52w_high_pct <= 25 %}#d4edda
          {%- elif s.dist_52w_high_pct is not none %}#f8d7da
          {%- else %}transparent{% endif %}">
          {{ '%.1f' % s.dist_52w_high_pct if s.dist_52w_high_pct is not none else '–' }}%</td>
        <td style="background-color:
          {%- if s.rs_score is not none and s.rs_score >= 70 %}var(--success-bg)
          {%- elif s.rs_score is not none %}var(--danger-bg)
          {%- else %}transparent{% endif %}">
          {{ rs_meter(s.rs_score) }}</td>
        <td style="background-color:
          {%- if s.rs_delta_4w and s.rs_delta_4w > 0 %}{{ COLOR_POSITIVE }}
          {%- elif s.rs_delta_4w and s.rs_delta_4w < 0 %}{{ COLOR_NEGATIVE }}
          {%- else %}transparent{% endif %}">
          {% if s.rs_delta_4w is not none %}{% if s.rs_delta_4w > 0 %}+{% endif %}{{ '%.0f' % s.rs_delta_4w }}{% else %}–{% endif %}
        </td>
        <td style="text-align:center;background-color:
          {%- if s.industry_ranking is not none and s.industry_ranking <= 50 %}#d4edda
          {%- elif s.industry_ranking is not none %}#f8d7da
          {%- else %}transparent{% endif %}">
          {{ s.industry_ranking if s.industry_ranking is not none else '–' }}</td>
        <td>{{ '%.1f' % s.roe if s.roe is not none else '–' }}%</td>
        <td>{{ '%.1f' % s.op_margin if s.op_margin is not none else '–' }}%</td>
        <td style="background-color:
          {%- if s.eps_growth_last_q is not none and s.eps_growth_last_q >= 20 %}#d4edda
          {%- elif s.eps_growth_last_q is not none and s.eps_growth_last_q >= 0 %}transparent
          {%- elif s.eps_growth_last_q is not none %}#f8d7da
          {%- else %}transparent{% endif %}">
          {{ '%.0f' % s.eps_growth_last_q if s.eps_growth_last_q is not none else '–' }}%</td>
        <td>{{ '%.1f' % s.revenue_growth if s.revenue_growth is not none else '–' }}%</td>
        <td style="background-color:
          {%- if s.vol_score is not none and s.vol_score >= 1.3 %}#d4edda
          {%- elif s.vol_score is not none %}transparent
          {%- else %}transparent{% endif %}">
          {{ '%.2f' % s.vol_score if s.vol_score is not none else '–' }}</td>
        <td>{{ (s.position_size_pct * 100) | round(1) }}%</td>
        <td style="{% if risk_high %}background-color:#ffcccc;font-weight:bold{% endif %}">
          {{ risk_pct_display }}%
        </td>
      </tr>
      {# ── Minervini Scorecard ── #}
      {% set crit = signal_criteria.get(s.ticker, {}) %}
      {% if crit %}
      <tr style="background-color:{{ row_bg }}">
        <td colspan="22" style="border-top:none;padding:3px 8px 7px 8px;text-align:left">
          {% for name, val in crit.items() %}
          <span style="display:inline-block;margin:2px 3px 2px 0;padding:1px 7px;border-radius:3px;
                       font-size:0.76em;font-weight:bold;white-space:nowrap;
                       background:{{ '#d4edda' if val else '#f8d7da' }};
                       color:{{ '#155724' if val else '#721c24' }}">
            {{ '✅' if val else '❌' }}&nbsp;{{ name }}
          </span>
          {% endfor %}
          {# ATR Hard-Filter #}
          {% if s.atr_pct is not none %}
          {% set atr_ok = s.atr_pct < 8 %}
          <span style="display:inline-block;margin:2px 3px 2px 0;padding:1px 7px;border-radius:3px;
                       font-size:0.76em;font-weight:bold;white-space:nowrap;
                       background:{{ '#d4edda' if atr_ok else '#f8d7da' }};
                       color:{{ '#155724' if atr_ok else '#721c24' }}">
            {{ '✅' if atr_ok else '❌' }}&nbsp;ATR {{ '%.1f' % s.atr_pct }}% &lt;8%
          </span>
          {% endif %}
        </td>
      </tr>
      {% endif %}
      {% endfor %}
    </table>
    <p style="font-size:0.82em;color:#777;margin-top:0.3em">
      Ranking-Score = RS(35%) + ΔRS 4W(20%) + Muster(20%) + Tightness(15%) + Industry(10%).
      🏆 = Top-{{ signals | selectattr("is_top_pick") | list | length }} Kaufkandidaten.
      {% if max_new_per_week is not none %}
      {% set _portfolio_remaining = (portfolio_max_positions - (alpaca_positions|length)) if portfolio_max_positions is not none else none %}
      Begrenzt durch das <strong>wöchentliche Neukauf-Limit von {{ max_new_per_week }}</strong>
      {%- if _portfolio_remaining is not none %} (nicht durch freie Portfolio-Plätze —
      davon wären aktuell {{ _portfolio_remaining }} frei){% endif %}.
      Niedriger gerankte Signale ohne 🏆 sind deshalb keine schlechteren Kandidaten,
      sondern nur diese Woche außerhalb des Kaufbudgets — sie bleiben gültige Signale.
      {% endif %}
      🔵 Buy-Stop = Einstiegsorder (max(Entry, Pivot) +0.1%) &nbsp;|&nbsp;
      🔴 Max. Gap = Order verwerfen wenn Montag-Open über diesem Preis (Pivot +5%) &nbsp;|&nbsp;
      🟢/🔴 Scorecard = Minervini-Kriterien &nbsp;|&nbsp; Rot hinterlegt = Risiko/Equity &gt; 1.8%.
    </p>

    {% if tv_watchlist %}
    <div style="background:#f7f8fc;border:1px solid #dde2f0;border-radius:6px;padding:0.8em 1em;margin-bottom:1em">
      <p style="font-size:0.9em;color:#555;margin:0 0 0.5em 0">
        📋 TradingView-Watchlist dieser Woche — kopieren und in TradingView unter
        <strong>Watchlist → + → Liste importieren</strong> einfügen:
      </p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="tv_watchlist_signals" type="text" readonly value="{{ tv_watchlist }}"
               onclick="this.select()"
               style="flex:1;min-width:220px;padding:6px 10px;border:1px solid #ccc;border-radius:4px;
                      font-family:monospace;font-size:0.85em;background:#fff">
        <button type="button"
                onclick="navigator.clipboard.writeText(document.getElementById('tv_watchlist_signals').value)"
                style="padding:6px 14px;background:#003d99;color:white;border:none;border-radius:4px;
                       cursor:pointer;font-size:0.85em">
          Kopieren
        </button>
      </div>
    </div>
    {% endif %}
    {% endif %}

    {% endif %}

    <h2>8) Marktführer nach Minervini (Score 8/8, RS ≥ 85)</h2>

    {% if leaders.empty %}
    <p>Keine Aktien erfüllen alle 8 Minervini-Kriterien mit RS ≥ 85.</p>
    {% else %}
    {% set _leader_count = leaders | length %}
    <p style="font-size:0.9em;color:#555;margin-top:-0.6em;margin-bottom:0.8em">
      <strong>{{ _leader_count }}</strong> Titel erfüllen aktuell alle 8 Minervini-Kriterien mit RS ≥ 85 —
      ein Indikator für die Marktbreite auf der Long-Seite.
    </p>

    {% set ns = namespace(col=0) %}
    <table class="leader-grid">
    {% for idx, row in leaders.head(20).iterrows() %}
      {% if ns.col == 0 %}<tr>{% endif %}
      <td class="leader-cell">
{{ leader_card(idx, row) }}
      </td>
      {% set ns.col = ns.col + 1 %}
      {% if ns.col == 2 %}
        </tr>
        {% set ns.col = 0 %}
      {% endif %}
    {% endfor %}
    {% if ns.col == 1 %}<td class="leader-cell"></td></tr>{% endif %}
    </table>

    {% if _leader_count > 20 %}
    <div id="leaders-extra" style="display:none">
      {% set ns2 = namespace(col=0) %}
      <table class="leader-grid">
      {% for idx, row in leaders.iloc[20:].iterrows() %}
        {% if ns2.col == 0 %}<tr>{% endif %}
        <td class="leader-cell">
{{ leader_card(idx, row) }}
        </td>
        {% set ns2.col = ns2.col + 1 %}
        {% if ns2.col == 2 %}
          </tr>
          {% set ns2.col = 0 %}
        {% endif %}
      {% endfor %}
      {% if ns2.col == 1 %}<td class="leader-cell"></td></tr>{% endif %}
      </table>
    </div>
    <div style="text-align:center;margin:0.6em 0 1.2em">
      <button type="button" id="leaders-toggle-btn"
              onclick="var el=document.getElementById('leaders-extra');
                       var show=el.style.display==='none';
                       el.style.display=show?'block':'none';
                       this.textContent=show?'Weniger anzeigen':'Weitere {{ _leader_count - 20 }} Leader anzeigen';"
              style="padding:8px 20px;background:#f7f8fc;border:1px solid #dde2f0;color:#003d99;
                     border-radius:6px;cursor:pointer;font-size:0.88em;font-weight:bold">
        Weitere {{ _leader_count - 20 }} Leader anzeigen
      </button>
    </div>
    {% endif %}

    {% if tv_watchlist_leaders %}
    <div style="background:#f7f8fc;border:1px solid #dde2f0;border-radius:6px;padding:0.8em 1em;margin-bottom:1em">
      <p style="font-size:0.9em;color:#555;margin:0 0 0.5em 0">
        📋 TradingView-Watchlist Marktführer — kopieren und in TradingView unter
        <strong>Watchlist → + → Liste importieren</strong> einfügen:
      </p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="tv_watchlist_leaders" type="text" readonly value="{{ tv_watchlist_leaders }}"
               onclick="this.select()"
               style="flex:1;min-width:220px;padding:6px 10px;border:1px solid #ccc;border-radius:4px;
                      font-family:monospace;font-size:0.85em;background:#fff">
        <button type="button"
                onclick="navigator.clipboard.writeText(document.getElementById('tv_watchlist_leaders').value)"
                style="padding:6px 14px;background:#003d99;color:white;border:none;border-radius:4px;
                       cursor:pointer;font-size:0.85em">
          Kopieren
        </button>
      </div>
    </div>
    {% endif %}

    {% endif %}

    <h2>9) 🏢 Kaufsignale im Detail</h2>
    <p style="font-size:0.85em;color:#777;margin-top:-0.6em;margin-bottom:0.8em">
      Zeigt alle Kaufsignale dieser Woche, nicht nur die mit Auftrag. 🏆-Titel wurden tatsächlich geordert;
      Titel ohne 🏆 erfüllen ebenfalls alle Kriterien, liegen aber außerhalb des wöchentlichen
      Neukauf-Limits — für sie wurde <strong>kein Auftrag angelegt</strong>.
    </p>
    {% if not signals %}
    <p style="color:#888">Keine Kaufsignale diese Woche — kein Steckbrief zu zeigen.</p>
    {% elif not profile_display %}
    <p style="color:#888">Unternehmensdaten konnten diese Woche nicht geladen werden.</p>
    {% else %}
    {% for s in signals %}
    {% set pd_ = profile_display.get(s.ticker) %}
    {% if pd_ %}
    <div class="leader-card" style="max-width:820px;margin-bottom:1.4em">
      <div style="font-size:1.15em;font-weight:bold;margin-bottom:2px">
        <a href="{{ s.sa_link }}" target="_blank" style="color:#003d99;text-decoration:none">{{ s.ticker }}</a>
        &nbsp;—&nbsp;{{ s.company }}
        {% if s.is_top_pick %}
        <span style="background:#f5a623;color:white;padding:2px 7px;border-radius:10px;font-size:0.6em;vertical-align:middle;margin-left:6px">🏆 Rang {{ s.rank }} · Auftrag angelegt</span>
        {% else %}
        <span title="Kaufsignal erfüllt, aber diese Woche außerhalb des Neukauf-Limits — es wurde kein Auftrag angelegt"
              style="background:#eee;color:#666;padding:2px 7px;border-radius:10px;font-size:0.6em;vertical-align:middle;margin-left:6px">Rang {{ s.rank }} · kein Auftrag angelegt</span>
        {% endif %}
      </div>
      <div style="color:#888;font-size:0.85em;margin-bottom:10px">
        {{ pd_.kopfzeile }}{% if pd_.kopfzeile %} · {% endif %}RS {{ '%.0f' % s.rs_score if s.rs_score is not none else '–' }}
        · Muster {{ s.pattern }}
      </div>
      {% if not s.is_top_pick %}
      <p style="background:#fff8e1;border-left:3px solid #f5a623;padding:.5em .8em;margin:.2em 0 .8em;font-size:0.85em;color:#555">
        ⚠️ Erfüllt alle Kaufkriterien, wurde aber <strong>nicht gekauft</strong> — Rang {{ s.rank }} liegt außerhalb
        des wöchentlichen Neukauf-Limits (siehe Abschnitt 7). Entry/Buy-Stop unten sind die Werte, zu denen
        <em>eingestiegen worden wäre</em>, kein aktiver Auftrag.
      </p>
      {% endif %}
      {{ tv_signal_charts.get(s.ticker, '') | safe }}
      {% if pd_.beschreibung %}
      <p style="margin:.4em 0">{{ pd_.beschreibung }}</p>
      <p style="color:#aaa;font-size:0.8em;margin:-.3em 0 .9em">
        Unternehmensbeschreibung im Original (englisch), gekürzt.</p>
      {% endif %}
      {% for label, chart in [
          ("Umsatz je Quartal", pd_.umsatz_q_chart),
          ("Umsatz je Jahr", pd_.umsatz_j_chart),
          ("Gewinn je Aktie (Quartal)", pd_.eps_q_chart)] %}
      {% if chart %}
      <div style="margin-bottom:.9em">
        <div class="left" style="font-weight:600;color:#003d99;font-size:.88em;margin-bottom:.2em">{{ label }}</div>
        {{ chart | safe }}
      </div>
      {% endif %}
      {% endfor %}
      {% if pd_.begruendung %}
      <div style="font-weight:600;color:#003d99;margin-top:.6em">
        {{ "Warum im Depot" if s.is_top_pick else "Warum ein Kaufsignal" }}
      </div>
      <ul style="margin:.4em 0 0;padding-left:1.2em">
        {% for g in pd_.begruendung %}<li style="margin-bottom:.25em">{{ g }}</li>{% endfor %}
      </ul>
      {% endif %}
    </div>
    {% endif %}
    {% endfor %}
    {% endif %}

    <h2>10) 📐 Muster zum Ansehen</h2>
    <p style="font-size:0.88em;color:#555;margin-top:-1em;margin-bottom:0.8em">
      Titel mit erkanntem VCP oder Launchpad — unabhängig davon, ob daraus ein Kaufsignal wurde.
      Bewusst nicht auf Kaufsignale eingeschränkt: gefragt ist das Muster, nicht der Ausbruch.
    </p>
    {% if not muster %}
    <p style="color:#888">Diese Woche kein VCP und kein Launchpad im Universum erkannt.</p>
    {% else %}
    <div style="overflow-x:auto">
    <table>
      <tr>
        <th class="left sortable" onclick="sortTable(this)">Ticker</th>
        <th class="left sortable" onclick="sortTable(this)">Unternehmen</th>
        <th class="left sortable" onclick="sortTable(this)">Muster</th>
        <th class="left sortable" onclick="sortTable(this)">Detail</th>
        <th class="sortable" onclick="sortTable(this)">Kurs</th>
        <th class="sortable" onclick="sortTable(this)">Pivot</th>
        <th class="sortable" onclick="sortTable(this)">Abstand Pivot</th>
        <th class="sortable" onclick="sortTable(this)" style="color:#1565c0">RS</th>
        <th class="sortable" onclick="sortTable(this)" style="color:#1565c0">Dist 52W H %</th>
        <th class="sortable" onclick="sortTable(this)">SA</th>
      </tr>
      {% for m in muster %}
      <tr>
        <td class="left"><strong style="color:#003d99">{{ m.ticker }}</strong></td>
        <td class="left" style="font-size:0.85em;color:#555">{{ m.company }}</td>
        <td class="left" style="font-weight:bold">{{ m.muster }}</td>
        <td class="left" style="font-size:0.85em;color:#555">{{ m.detail }}</td>
        <td>{{ '%.2f' % m.close if m.close is not none else '–' }}</td>
        <td>{{ '%.2f' % m.pivot if m.pivot is not none else '–' }}</td>
        <td style="color:{% if m.pivot_abstand is not none and m.pivot_abstand > 0 %}{{ COLOR_POS_TEXT }}{% elif m.pivot_abstand is not none and m.pivot_abstand < 0 %}{{ COLOR_NEG_TEXT }}{% else %}inherit{% endif %}">
          {% if m.pivot_abstand is not none %}{% if m.pivot_abstand > 0 %}+{% endif %}{{ '%.1f' % m.pivot_abstand }}%{% else %}–{% endif %}
        </td>
        <td style="background-color:{% if m.rs is not none and m.rs >= 70 %}#d4edda{% else %}transparent{% endif %}">
          {{ '%.0f' % m.rs if m.rs is not none else '–' }}</td>
        <td style="background-color:{% if m.dist_52w is not none and m.dist_52w <= 25 %}#d4edda{% else %}transparent{% endif %}">
          {{ '%.1f' % m.dist_52w if m.dist_52w is not none else '–' }}</td>
        <td>{% if m.link %}<a href="{{ m.link }}" target="_blank" class="btn-sa">SA</a>{% endif %}</td>
      </tr>
      <tr>
        <td colspan="10" style="border-top:none;padding:0 8px 14px 8px;text-align:left">
          {{ tv_muster_charts.get(m.ticker, '') | safe }}
        </td>
      </tr>
      {% endfor %}
    </table>
    </div>

    {% if tv_watchlist_muster %}
    <div style="background:#f7f8fc;border:1px solid #dde2f0;border-radius:6px;padding:0.8em 1em;margin-bottom:1em">
      <p style="font-size:0.9em;color:#555;margin:0 0 0.5em 0">
        📋 TradingView-Watchlist Muster — kopieren und in TradingView unter
        <strong>Watchlist → + → Liste importieren</strong> einfügen:
      </p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input id="tv_watchlist_muster" type="text" readonly value="{{ tv_watchlist_muster }}"
               onclick="this.select()"
               style="flex:1;min-width:220px;padding:6px 10px;border:1px solid #ccc;border-radius:4px;
                      font-family:monospace;font-size:0.85em;background:#fff">
        <button type="button"
                onclick="navigator.clipboard.writeText(document.getElementById('tv_watchlist_muster').value)"
                style="padding:6px 14px;background:#003d99;color:white;border:none;border-radius:4px;
                       cursor:pointer;font-size:0.85em">
          Kopieren
        </button>
      </div>
    </div>
    {% endif %}

    {% endif %}

  </div><!-- .page -->
</body>
</html>
"""

def _extract_close_series(df: pd.DataFrame) -> pd.Series:
    """
    Robustly extract a one-dimensional Close series from various yfinance formats.
    Handles:
    - normal DataFrames with "Close"
    - MultiIndex DataFrames (('Close', 'TICKER'), etc.)
    - Series inputs
    Returns a cleaned numeric Series or an empty Series.
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)

    # Case 1: Already a Series
    if isinstance(df, pd.Series):
        return pd.to_numeric(df, errors="coerce").dropna()

    # Case 2: Normal DataFrame with "Close"
    if "Close" in df.columns:
        s = df["Close"]
        if isinstance(s, pd.DataFrame):   # can happen for MultiIndex
            s = s.iloc[:, 0]
        return pd.to_numeric(s, errors="coerce").dropna()

    # Case 3: MultiIndex columns: ('Close', TKR)
    if hasattr(df.columns, "levels"):
        close_cols = [c for c in df.columns if isinstance(c, tuple) and c[0] == "Close"]
        if close_cols:
            s = df[close_cols[0]]
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            return pd.to_numeric(s, errors="coerce").dropna()

    # Fall-through: Nothing usable
    return pd.Series(dtype=float)


def build_risk_rows(idx_data: dict) -> list[tuple]:
    """
    Builds Risk & Sentiment rows:
    - VIX
    - TNX (10-year yield)
    - UUP (Dollar index)
    Uses robust close extraction so it works for MultiIndex DF.
    Returns list of tuples: (Label, Now, Prev, Delta %)
    """
    rows = []   # ← FIXED: must be defined at the top
    risk_keys = [
        ("VIX", "VIX"),
        ("TNX", "10Y Interest Rate"),
        ("UUP", "UUP"),
        ]
    
    for key, label in risk_keys:
        df = idx_data.get(key)
        if df is None or len(df) == 0:
            continue

        close = _extract_close_series(df)
        if close.empty:
            continue

        now = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else now
        delta = (now - prev) / prev * 100 if prev != 0 else 0.0

        rows.append((label, now, prev, delta))
        
    return rows

def compute_ampel(breadth_snap: pd.DataFrame, idx: pd.DataFrame) -> dict:
    """Berechnet die Marktampel anhand von 6 Kriterien."""
    spy_col = "S&P 500 (SPY)"
    qqq_col = "Nasdaq 100 (QQQ)"

    def _v(df, row, col, default=0.0):
        try:
            return float(df.loc[row, col])
        except Exception:
            return default

    nh = _v(breadth_snap, "Neue 52W\u2011Hochs (Anzahl)", "Aktuelle Woche", 0)
    nl = _v(breadth_snap, "Neue 52W\u2011Tiefs (Anzahl)", "Aktuelle Woche", 0)
    spy_10w  = _v(idx, "vs 10W MA", spy_col)
    qqq_10w  = _v(idx, "vs 10W MA", qqq_col)
    breadth_ = _v(breadth_snap, "% \u00fcber 10\u2011Wochen\u2011EMA", "Aktuelle Woche")
    winners  = _v(breadth_snap, "1W-Kursgewinner (%)", "Aktuelle Woche")
    spy_macd = _v(idx, "\u0394 MACD", spy_col)

    criteria = [
        {"name": "SPY über 10W MA",              "met": spy_10w > 0,    "value": f"{spy_10w:+.2f}%"},
        {"name": "QQQ über 10W MA",              "met": qqq_10w > 0,    "value": f"{qqq_10w:+.2f}%"},
        {"name": "Marktbreite >55% über 10W EMA","met": breadth_ > 55,  "value": f"{breadth_:.1f}%"},
        {"name": "Wochenkursgewinner >55%",       "met": winners  > 55,  "value": f"{winners:.1f}%"},
        {"name": "New Highs > New Lows",          "met": nh > nl,        "value": f"NH={int(nh)}, NL={int(nl)}"},
        {"name": "SPY MACD-Momentum positiv",     "met": spy_macd > 0,   "value": f"{spy_macd:+.4f}"},
    ]

    score = sum(1 for c in criteria if c["met"])
    if score >= 5:
        label, color, bg, emoji = "Bullish",  "#155724", "#d4edda", "🟢"
    elif score >= 3:
        label, color, bg, emoji = "Neutral",  "#856404", "#fff3cd", "🟡"
    else:
        label, color, bg, emoji = "Defensiv", "#721c24", "#f8d7da", "🔴"

    return {"score": score, "label": label, "color": color, "bg": bg,
            "emoji": emoji, "criteria": criteria}


def compute_nhnl_badge(breadth_snap: pd.DataFrame) -> dict:
    """NH/NL-Ratio als eigenständiges Badge, analog zur Marktampel.

    Schwellen wie bei O'Neil/IBD gebräuchlich: ≥70% klar mehr neue Hochs als
    Tiefs (bullish), ≤30% Umkehrung (defensiv), dazwischen neutral.
    """
    def _v(df, row, col, default=0.0):
        try:
            return float(df.loc[row, col])
        except Exception:
            return default

    nh = _v(breadth_snap, "Neue 52W‑Hochs (Anzahl)", "Aktuelle Woche", 0)
    nl = _v(breadth_snap, "Neue 52W‑Tiefs (Anzahl)", "Aktuelle Woche", 0)
    ratio = _v(breadth_snap, "NH/(NH+NL) (%)", "Aktuelle Woche", 0)

    if ratio >= 70:
        label, color, bg, emoji = "Bullish",  "#155724", "#d4edda", "🟢"
    elif ratio <= 30:
        label, color, bg, emoji = "Defensiv", "#721c24", "#f8d7da", "🔴"
    else:
        label, color, bg, emoji = "Neutral",  "#856404", "#fff3cd", "🟡"

    return {"ratio": ratio, "nh": int(nh), "nl": int(nl),
            "label": label, "color": color, "bg": bg, "emoji": emoji}


def build_tv_watchlist_string(tickers: list) -> str:
    """Komma-getrennte Tickerliste fuer TradingView-Import.

    TradingView bietet keine oeffentliche API zum Anlegen von Watchlists
    (nur inoffizielle, session-cookie-basierte Endpunkte). Stattdessen laesst
    sich eine komma-getrennte Symbolliste ueber "Watchlist → + → Liste
    importieren" einfuegen — dieselbe rohe Tickerform, die auch das
    Advanced-Chart-Widget schon verwendet (siehe `_tv_advanced_chart`).

    Nimmt entweder rohe Ticker-Strings oder Objekte/Dicts mit `.ticker`
    bzw. `["ticker"]` entgegen, damit Signals, Leaders-Zeilen und
    Muster-Eintraege denselben Aufruf nutzen koennen.
    """
    def _ticker(t):
        if isinstance(t, str):
            return t
        if isinstance(t, dict):
            return t.get("ticker")
        return getattr(t, "ticker", None)

    return ",".join(dict.fromkeys(tk for t in tickers if (tk := _ticker(t))))


def build_sector_rows(idx_data: dict) -> list:
    """Wöchentliche Performance aller Sektor-ETFs, absteigend sortiert."""
    rows = []
    for sym, name in SECTOR_ETFS.items():
        df = idx_data.get(sym)
        if df is None:
            continue
        close = _extract_close_series(df)
        if len(close) < 2:
            continue
        prev = float(close.iloc[-2])
        curr = float(close.iloc[-1])
        chg  = (curr - prev) / prev * 100 if prev != 0 else 0.0
        rows.append({"ticker": sym, "name": name, "chg": chg})
    rows.sort(key=lambda r: r["chg"], reverse=True)
    return rows


def build_sector_heatmap(idx_data: dict, weeks: int = 13) -> dict:
    """Sektor-Wochenrenditen der letzten `weeks` Wochen als Rotations-Heatmap.

    Ergänzt 1b) (aktuelle Woche als Balken) um die Zeitdimension: zeigt, wie
    sich die Sektor-Führerschaft über mehrere Wochen verschiebt, statt nur
    den Momentanzustand. Nutzt dieselbe Rohquelle (idx_data mit wöchentlichen
    Sektor-ETF-Kursen) wie build_sector_rows, damit beide Ansichten nicht
    auseinanderlaufen können.
    """
    series_by_sym = {}
    dates = None
    for sym, name in SECTOR_ETFS.items():
        df = idx_data.get(sym)
        if df is None:
            continue
        close = _extract_close_series(df)
        if len(close) < 2:
            continue
        rets = (close.pct_change() * 100).dropna().tail(weeks)
        if rets.empty:
            continue
        series_by_sym[sym] = (name, rets)
        if dates is None or len(rets.index) > len(dates):
            dates = rets.index

    if not series_by_sym or dates is None:
        return {"dates": [], "rows": []}

    max_abs = 0.0
    for _, rets in series_by_sym.values():
        vals = rets.reindex(dates).dropna()
        if not vals.empty:
            max_abs = max(max_abs, float(vals.abs().max()))
    max_abs = max_abs or 1.0

    rows = []
    for sym, (name, rets) in series_by_sym.items():
        vals = rets.reindex(dates)
        cells = []
        for d in dates:
            v = vals.get(d)
            if v is None or pd.isna(v):
                cells.append({"bg": "transparent", "label": "–"})
            else:
                v = float(v)
                alpha = 0.10 + 0.70 * min(abs(v) / max_abs, 1.0)
                color = "28,124,77" if v > 0 else ("179,38,30" if v < 0 else "0,0,0")
                bg = f"rgba({color},{alpha:.2f})" if v != 0 else "transparent"
                cells.append({"bg": bg, "label": f"{v:+.1f}%"})
        last = vals.dropna()
        rows.append({
            "ticker": sym, "name": name, "cells": cells,
            "last": float(last.iloc[-1]) if not last.empty else None,
        })

    rows.sort(key=lambda r: r["last"] if r["last"] is not None else -999, reverse=True)
    return {"dates": [d.strftime("%d.%m.") for d in dates], "rows": rows}


def build_sector_bar_svg(sector_rows: list, width: int = 620, row_height: int = 26) -> str:
    """Sektor-Performance als horizontaler Balkenchart, Null in der Mitte.

    Inline-SVG statt <canvas>/Bild, damit dieselbe Grafik unveraendert im
    Web-Report UND im Boersenbrief funktioniert — siehe mail_report.equity_svg
    fuer dieselbe Begruendung.
    """
    if not sector_rows:
        return ""
    n = len(sector_rows)
    label_w, value_w = 150, 60
    pad_top, pad_bottom = 8, 8
    chart_w  = width - label_w - value_w
    half_w   = chart_w / 2.0
    height   = pad_top + pad_bottom + n * row_height
    zero_x   = label_w + half_w

    max_abs = max((abs(s["chg"]) for s in sector_rows), default=0) or 1.0
    scale   = (half_w * 0.9) / max_abs

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" height="{height}" style="max-width:{width}px;font-family:Arial;">',
        f'<line x1="{zero_x:.1f}" y1="{pad_top}" x2="{zero_x:.1f}" y2="{height - pad_bottom}" '
        f'stroke="#c9d2e8" stroke-width="1"/>',
    ]
    for i, s in enumerate(sector_rows):
        y_top  = pad_top + i * row_height
        y_mid  = y_top + row_height / 2.0
        bar_h  = row_height * 0.55
        val    = s["chg"]
        color  = COLOR_POS_TEXT if val > 0 else (COLOR_NEG_TEXT if val < 0 else "#888")
        bar_len = abs(val) * scale
        bar_x   = zero_x if val >= 0 else zero_x - bar_len
        parts.append(
            f'<text x="{label_w - 8}" y="{y_mid + 4:.1f}" font-size="12" fill="#333" '
            f'text-anchor="end">{s["name"]} '
            f'<tspan fill="#999" font-size="10">{s["ticker"]}</tspan></text>'
        )
        parts.append(
            f'<rect x="{bar_x:.1f}" y="{y_top + (row_height - bar_h) / 2:.1f}" '
            f'width="{bar_len:.1f}" height="{bar_h:.1f}" fill="{color}" rx="2"/>'
        )
        label_x = bar_x + bar_len + 5 if val >= 0 else bar_x - 5
        anchor  = "start" if val >= 0 else "end"
        parts.append(
            f'<text x="{label_x:.1f}" y="{y_mid + 4:.1f}" font-size="12" fill="{color}" '
            f'font-weight="bold" text-anchor="{anchor}">'
            f'{"+" if val > 0 else ""}{val:.2f}%</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def build_sparkline_svg(values: list, width: int = 90, height: int = 24) -> str:
    """13-Wochen-Trendverlauf einer Breadth-Metrik als Inline-SVG-Sparkline.

    Grün/Rot zeigt nur die Richtung (letzter Wert vs. erster Wert der Reihe),
    nicht ob der Wert für die Metrik günstig ist — das übernimmt weiterhin
    die Zellenfarbe in der Tabelle (siehe is_high_good im Template).
    """
    vals = [float(v) for v in values if v is not None and not (isinstance(v, float) and pd.isna(v))]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pad = 3
    n = len(vals)
    step = (width - 2 * pad) / (n - 1)
    color = COLOR_POS_TEXT if vals[-1] > vals[0] else (COLOR_NEG_TEXT if vals[-1] < vals[0] else "#999")

    pts = []
    for i, v in enumerate(vals):
        x = pad + i * step
        y = height - pad - (v - lo) / span * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    last_x, last_y = pts[-1].split(",")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" style="vertical-align:middle;">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="2" fill="{color}"/>'
        f'</svg>'
    )


def build_breadth_sparklines(weekly_data: dict, breadth_snap: pd.DataFrame, weeks: int = 13) -> dict:
    """Sparkline-SVG je Breadth-Zeile über die letzten `weeks` Wochen.

    Nutzt dieselbe Snapshot-Funktion wie die Vergleichstabelle, nur mit
    Offsets 0..weeks-1 (statt nur 0/1/4) — damit keine zweite Berechnung der
    Breadth-Logik entsteht, die später auseinanderlaufen könnte.
    """
    trend = compute_breadth_snapshots(weekly_data, offsets=list(range(weeks - 1, -1, -1)))
    if trend.empty:
        return {}

    try:
        nh_t = trend.loc["Neue 52W‑Hochs (Anzahl)"].astype(float)
        nl_t = trend.loc["Neue 52W‑Tiefs (Anzahl)"].astype(float)
        tot_t = nh_t + nl_t
        trend.loc["NH/(NH+NL) (%)"] = nh_t.where(tot_t == 0, other=nh_t / tot_t.where(tot_t > 0, 1) * 100)
    except Exception:
        pass

    return {
        row: build_sparkline_svg(trend.loc[row].tolist())
        for row in breadth_snap.index if row in trend.index
    }


def compute_filter_fails(row, *, sector_excluded: set, min_rs: float,
                         max_rank: float, max_atr: float, min_price: float,
                         min_cap: float, min_rev_growth: float,
                         min_eps_growth: float, html: bool = True) -> str:
    """Welche Kaufkriterien hat dieser Titel NICHT erfuellt?

    Auf Modulebene, weil zwei Verwender dieselbe Antwort brauchen: der
    Web-Report und der Boersenbrief (mail_report). Als Closure innerhalb von
    `build_html_report` waere sie fuer die Mail nicht erreichbar gewesen — und
    eine zweite Umsetzung waere genau die Art Dopplung, die spaeter auseinander
    laeuft.

    `html=False` liefert dieselben Texte ohne `&nbsp;`/`&lt;` — fuer Kontexte,
    die reinen Text brauchen.
    """
    lt, gt, sp = ("&lt;", "&gt;", "&nbsp;") if html else ("<", ">", " ")

    fails = []
    if row.name in sector_excluded:
        fails.append("Sektor-Limit")

    def _n(col):
        return pd.to_numeric(row.get(col, None), errors="coerce")

    if not bool(row.get("MACD > Signal (W)", False)):
        fails.append("MACD fällt")
    if not bool(row.get("Vol-Breakout", False)):
        fails.append("Vol-BO fehlt")
    rs = _n("RS (O'Neil)")
    if pd.isna(rs) or rs < min_rs:
        fails.append(f"RS{sp}{lt}{sp}{min_rs:.0f}")
    rank = _n("Industry Ranking")
    if not pd.isna(rank) and rank > max_rank:
        fails.append(f"Rank{sp}{gt}{sp}{max_rank:.0f}")
    atr = _n("ATR / Price (%)")
    if not pd.isna(atr) and atr > max_atr:
        fails.append(f"ATR{sp}{gt}{sp}{max_atr:.0f}%")
    price = _n("Close")
    if pd.isna(price):
        fails.append("Kurs fehlt")
    elif price < min_price:
        fails.append(f"Kurs{sp}{lt}{sp}${min_price:.0f}")
    cap = _n("MarketCap (Mio USD)")
    if not pd.isna(cap) and cap < min_cap:
        fails.append(f"MCap{sp}{lt}{sp}{min_cap:.0f}M")
    if min_rev_growth > 0:
        rev = _n("Revenue Wachstum TTM YoY (%)")
        if pd.isna(rev):
            fails.append(f"Rev{sp}fehlt")
        elif rev < min_rev_growth:
            fails.append(f"Rev{sp}{lt}{sp}{min_rev_growth:.0f}%")
    if min_eps_growth > 0:
        eps = _n("EPS Wachstum letztes Q YoY (%)")
        if pd.isna(eps):
            fails.append(f"EPS-Q{sp}fehlt")
        elif eps < min_eps_growth:
            fails.append(f"EPS-Q{sp}{lt}{sp}{min_eps_growth:.0f}%")
    return " · ".join(fails) if fails else "✅"


def filter_rules_for_fails() -> dict:
    """Die Schwellen fuer `compute_filter_fails` aus rules.json."""
    try:
        r = json.loads((Path(__file__).parent / "rules.json").read_text(encoding="utf-8"))
    except Exception:
        r = {}
    f = r.get("filters", {})
    return {
        "min_rs":         float(f.get("min_rs_score",          70.0)),
        "max_rank":       float(f.get("max_industry_rank",     50.0)),
        "max_atr":        float(f.get("max_atr_pct",            8.0)),
        "min_price":      float(f.get("min_price",              5.0)),
        "min_cap":        float(f.get("min_market_cap_mio",   300.0)),
        "min_rev_growth": float(f.get("min_rev_growth",         0.0)),
        "min_eps_growth": float(f.get("min_eps_growth_last_q",  0.0)),
    }


def save_leaders_diagnostic(leaders: pd.DataFrame, *, sector_excluded: set,
                            path) -> Path:
    """Rohe Schwellenwert-Kennzahlen aller Score>=6-Leaders als JSON persistieren.

    Anlass (2026-08-09): AVT und MTRN scheiterten im Report vom Samstag
    beide an "EPS-Q < 20%", bestanden aber im Sonntagslauf trotz identischem
    Kurs/Score/RS -- die Ursache (Yahoo liefert die EPS-Q-Kennzahl
    inkonsistent zwischen Laeufen) liess sich nur durch manuellen Vergleich
    zweier HTML-Reports finden. Diese Datei macht dieselbe Frage kuenftig zu
    einem einfachen JSON-Diff zwischen zwei Tagen.
    """
    path = Path(path)
    schwellen = filter_rules_for_fails()
    rows: dict = {}

    def _num(v):
        try:
            return None if pd.isna(v) else float(v)
        except Exception:
            return None

    if leaders is not None and not leaders.empty and "score" in leaders.columns:
        score_num = pd.to_numeric(leaders["score"], errors="coerce")
        subset = leaders[score_num >= 6]
        for ticker, row in subset.iterrows():
            fails = compute_filter_fails(
                row, sector_excluded=sector_excluded, html=False, **schwellen,
            )
            rows[str(ticker)] = {
                "score":              _num(row.get("score")),
                "rs":                 _num(row.get("RS (O'Neil)")),
                "eps_growth_last_q":  _num(row.get("EPS Wachstum letztes Q YoY (%)")),
                "revenue_growth":     _num(row.get("Revenue Wachstum TTM YoY (%)")),
                "roe":                _num(row.get("ROE (%)")),
                "industry_ranking":   _num(row.get("Industry Ranking")),
                "atr_pct":            _num(row.get("ATR / Price (%)")),
                "close":              _num(row.get("Close")),
                "market_cap_mio":     _num(row.get("MarketCap (Mio USD)")),
                "vol_breakout":       bool(row.get("Vol-Breakout", False)),
                "macd_above_signal":  bool(row.get("MACD > Signal (W)", False)),
                "fails":              fails,
            }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True),
                     encoding="utf-8")
    return path


def _tv_chart_id(prefix: str, ticker: str) -> str:
    """DOM-taugliche, eindeutige Widget-ID je Ticker und Sektion."""
    return f"{prefix}_{re.sub(r'[^A-Za-z0-9]', '_', str(ticker))}"


def _tv_advanced_chart(ticker: str, elem_id: str, height: int = 400) -> str:
    """TradingView Advanced-Chart-Widget: Wochenkerzen mit 10/30/40-Wochen-Linien.

    Oeffentliches Embed-Widget (kein Login, kein API-Key, kein Pro-Abo noetig
    — Premium lizenziert nur die Darstellung im TradingView-eigenen Chart,
    nicht den Embed, siehe [[project_kursquelle_alternativen]]). Die drei
    Moving Averages sind bewusst 10/30/40 Wochen: dasselbe Set, das das
    VCP/Launchpad-Regelwerk selbst benutzt (siehe signal_generator.py), damit
    der Chart zeigt, wonach der Screener tatsaechlich sucht.

    Nur fuer den Web-Report. Die Mail bindet bewusst keine externen Skripte
    oder Iframes ein — die meisten Mailclients blockieren sie ohnehin (siehe
    mail_report.py Modul-Docstring).
    """
    options = {
        "autosize": False,
        "width": "100%",
        "height": height,
        "symbol": ticker,
        "interval": "W",
        "timezone": "Etc/UTC",
        "theme": "light",
        "style": "1",
        "locale": "de_DE",
        "toolbar_bg": "#f1f3f6",
        "enable_publishing": False,
        "hide_side_toolbar": True,
        "allow_symbol_change": False,
        "studies": [
            {"id": "MASimple@tv-basicstudies", "inputs": {"length": 10}},
            {"id": "MASimple@tv-basicstudies", "inputs": {"length": 30}},
            {"id": "MASimple@tv-basicstudies", "inputs": {"length": 40}},
        ],
        "container_id": elem_id,
    }
    return (
        f'<div class="tradingview-widget-container" style="margin:.6em 0 1em;">'
        f'<div id="{elem_id}"></div>'
        f'<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>'
        f'<script type="text/javascript">new TradingView.widget({json.dumps(options)});</script>'
        f'</div>'
    )


def _format_geld_kompakt(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "–"
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


_BAR_FARBE = "#0052cc"   # dieselbe Akzentfarbe wie die SA-Buttons im Report


def _bar_chart_svg(reihe: list, als_betrag: bool, jahres_reihe: bool = False,
                   hoehe: int = 168) -> str:
    """Balkendiagramm fuer eine Kennzahlreihe (Umsatz oder Gewinn je Aktie).

    Ersetzt die fruehere Tabelle: bei 4-5 Perioden zeigt die Balkenhoehe den
    Trend auf einen Blick, waehrend die Tabelle ihn erst beim Vergleichen der
    Zahlen ergab. Nullbasis ist Pflicht (kein abgeschnittener Balken) — sonst
    uebertreibt die Balkenhoehe das Wachstum. Ein einzelner Balken je Periode
    ist eine Serie, deshalb kein Farbwechsel und keine Legende noetig; der
    Titel darueber (im Template) sagt, was geplottet ist.
    """
    reihe = [r for r in (reihe or []) if r.get("wert") is not None]
    if not reihe:
        return ""

    n = len(reihe)
    breite_bar = 62
    breite = n * breite_bar
    pad_l, pad_t, pad_b = 4, 38, 34
    plot_h = hoehe - pad_t - pad_b

    werte = [float(r["wert"]) for r in reihe]
    lo = min(0.0, min(werte))
    hi = max(0.0, max(werte))
    if hi - lo < 1e-9:
        hi = lo + 1.0
    baseline_y = pad_t + (hi - 0.0) / (hi - lo) * plot_h

    def _y(v: float) -> float:
        return pad_t + (hi - v) / (hi - lo) * plot_h

    bar_w, rund = 22, 4   # Spezifikation: Balken max. 24px dick, 4px Eckenradius

    teile = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {breite} {hoehe}" '
        f'width="100%" height="{hoehe}" style="max-width:{breite}px;font-family:Arial,sans-serif;">',
        f'<line x1="0" y1="{baseline_y:.1f}" x2="{breite}" y2="{baseline_y:.1f}" '
        f'stroke="#c3c2b7" stroke-width="1"/>',
    ]

    for i, r in enumerate(reihe):
        wert = float(r["wert"])
        cx = pad_l + i * breite_bar + breite_bar / 2
        periode = str(r.get("periode", ""))
        # Jahresreihe auf das Jahr kuerzen ("2025-12" -> "2025"); Quartale
        # behalten "YYYY-MM", auch das Q4-Quartal, das ebenfalls auf "-12"
        # endet — an der Endung allein laesst sich das nicht unterscheiden.
        label_periode = periode[:4] if jahres_reihe else periode
        teile.append(
            f'<text x="{cx:.1f}" y="{hoehe - 6}" text-anchor="middle" font-size="9.5" '
            f'fill="#52514e">{label_periode}</text>'
        )

        y_wert  = _y(wert)
        top     = min(y_wert, baseline_y)
        bottom  = max(y_wert, baseline_y)
        positiv = wert >= 0
        x0 = cx - bar_w / 2

        wert_label = _format_geld_kompakt(wert) if als_betrag else f"{wert:.2f}"
        yoy = r.get("yoy")
        yoy_label = f"{'+' if yoy > 0 else ''}{yoy:.0f}%" if yoy is not None else None
        hover = f"{periode}: {wert_label}" + (f" ({yoy_label} ggü. Vorjahr)" if yoy_label else "")

        if positiv:
            pfad = (
                f'M{x0:.1f},{bottom:.1f} L{x0:.1f},{top + rund:.1f} '
                f'Q{x0:.1f},{top:.1f} {x0 + rund:.1f},{top:.1f} '
                f'L{x0 + bar_w - rund:.1f},{top:.1f} '
                f'Q{x0 + bar_w:.1f},{top:.1f} {x0 + bar_w:.1f},{top + rund:.1f} '
                f'L{x0 + bar_w:.1f},{bottom:.1f} Z'
            )
        else:
            pfad = (
                f'M{x0:.1f},{top:.1f} L{x0 + bar_w:.1f},{top:.1f} '
                f'L{x0 + bar_w:.1f},{bottom - rund:.1f} '
                f'Q{x0 + bar_w:.1f},{bottom:.1f} {x0 + bar_w - rund:.1f},{bottom:.1f} '
                f'L{x0 + rund:.1f},{bottom:.1f} '
                f'Q{x0:.1f},{bottom:.1f} {x0:.1f},{bottom - rund:.1f} Z'
            )
        teile.append(f'<path d="{pfad}" fill="{_BAR_FARBE}"><title>{hover}</title></path>')

        label_y = (top - 6) if positiv else (bottom + 13)
        teile.append(
            f'<text x="{cx:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="10.5" '
            f'font-weight="bold" fill="#0b0b0b">{wert_label}</text>'
        )
        if yoy_label is not None:
            yoy_farbe = COLOR_POS_TEXT if yoy > 0 else (COLOR_NEG_TEXT if yoy < 0 else "#898781")
            yoy_y = (top - 19) if positiv else (bottom + 25)
            teile.append(
                f'<text x="{cx:.1f}" y="{yoy_y:.1f}" text-anchor="middle" font-size="9" '
                f'fill="{yoy_farbe}">{yoy_label}</text>'
            )

    teile.append("</svg>")
    return "".join(teile)


def _price_history_svg(perioden: list, close: list, volumen: Optional[list] = None,
                       marker_index: Optional[int] = None, marker_label: str = "",
                       breite: int = 560, hoehe: int = 210) -> str:
    """Kursverlauf (Linie) + Volumen (Balken) als eigenstaendiges SVG, mit optionalem
    Marker (z.B. Kaufzeitpunkt).

    Anders als `_tv_advanced_chart`: TradingViews Embed-Widget zeigt immer den
    aktuellen Live-Chart und laesst sich nicht auf ein festes historisches
    Fenster verankern. Fuer einen Zustand, der dauerhaft eingefroren werden
    soll (z.B. "wie sah der Titel unmittelbar vor dem Kauf aus"), reicht das
    nicht — deshalb dieses SVG, das exakt das Fenster zeigt, das man ihm
    mitgibt und beliebig oft identisch neu rendert.

    `close`/`volumen` sind parallele Listen, keine DataFrame-Abhaengigkeit in
    der Signatur — der Aufrufer extrahiert aus yfinance, diese Funktion baut
    nur noch Geometrie.
    """
    n = len(close)
    if n < 2:
        return ""

    hat_volumen = bool(volumen) and len(volumen) == n
    pad_l, pad_r, pad_t, pad_b = 4, 46, 20, 26
    vol_h = 40 if hat_volumen else 0
    gap   = 14 if hat_volumen else 0
    preis_h = hoehe - pad_t - pad_b - vol_h - gap

    plot_w = breite - pad_l - pad_r
    dx = plot_w / (n - 1)

    lo, hi = min(close), max(close)
    if hi - lo < 1e-9:
        hi = lo + 1.0

    def _py(v: float) -> float:
        return pad_t + (hi - v) / (hi - lo) * preis_h

    xs = [pad_l + i * dx for i in range(n)]
    ys = [_py(v) for v in close]

    teile = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {breite} {hoehe}" '
        f'width="100%" height="{hoehe}" style="max-width:{breite}px;font-family:Arial,sans-serif;">',
    ]

    # Flaeche unter der Linie (Wash, 10% Deckkraft)
    area = f'M{xs[0]:.1f},{pad_t + preis_h:.1f} '
    area += " ".join(f'L{x:.1f},{y:.1f}' for x, y in zip(xs, ys))
    area += f' L{xs[-1]:.1f},{pad_t + preis_h:.1f} Z'
    teile.append(f'<path d="{area}" fill="{_BAR_FARBE}" opacity="0.10"/>')

    # Linie (2px, runde Verbindungen)
    linie = " ".join(f'{"M" if i == 0 else "L"}{x:.1f},{y:.1f}' for i, (x, y) in enumerate(zip(xs, ys)))
    teile.append(f'<path d="{linie}" fill="none" stroke="{_BAR_FARBE}" stroke-width="2" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')

    # Marker (z.B. Kaufzeitpunkt): gestrichelte Vertikale + Endpunkt-Dot
    if marker_index is not None and 0 <= marker_index < n:
        mx, my = xs[marker_index], ys[marker_index]
        teile.append(f'<line x1="{mx:.1f}" y1="{pad_t:.1f}" x2="{mx:.1f}" y2="{pad_t + preis_h:.1f}" '
                     f'stroke="#e34948" stroke-width="1" stroke-dasharray="3,3"/>')
        teile.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="4.5" fill="#e34948" '
                     f'stroke="#fcfcfb" stroke-width="2"/>')
        if marker_label:
            anchor = "start" if marker_index < n * 0.7 else "end"
            lx = mx + (6 if anchor == "start" else -6)
            teile.append(f'<text x="{lx:.1f}" y="{pad_t - 6:.1f}" text-anchor="{anchor}" '
                         f'font-size="10" font-weight="bold" fill="#e34948">{marker_label}</text>')

    # Endlabel: letzter Kurs (Linie -> Wert am Ende, per Spezifikation)
    teile.append(f'<text x="{xs[-1] + 8:.1f}" y="{ys[-1] + 3:.1f}" font-size="10.5" '
                 f'font-weight="bold" fill="#0b0b0b">{close[-1]:.2f}</text>')

    # x-Achse: sparsam beschriften — nur erster, letzter und Marker-Punkt
    label_idx = {0, n - 1}
    if marker_index is not None:
        label_idx.add(marker_index)
    for i in sorted(label_idx):
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        teile.append(f'<text x="{xs[i]:.1f}" y="{hoehe - pad_b + 16:.1f}" text-anchor="{anchor}" '
                     f'font-size="9" fill="#898781">{perioden[i]}</text>')

    # Volumen-Panel: eigene Skala, eigenes Panel — kein zweiter Y-Achsenmassstab
    # im selben Plot (dual-axis waere hier ein Anti-Pattern).
    if hat_volumen:
        vol_top = pad_t + preis_h + gap
        vmax = max(volumen) or 1.0
        bar_w = max(1.5, dx * 0.55)
        for i, (x, v) in enumerate(zip(xs, volumen)):
            bh = (v / vmax) * vol_h
            farbe = "#e34948" if i == marker_index else "#c3c2b7"
            teile.append(f'<rect x="{x - bar_w / 2:.1f}" y="{vol_top + vol_h - bh:.1f}" '
                         f'width="{bar_w:.1f}" height="{max(bh, 1):.1f}" fill="{farbe}"/>')

    teile.append("</svg>")
    return "".join(teile)


def _format_profile_for_report(profile: dict) -> dict:
    """Unternehmensportraets fuer den Web-Report vorformatieren.

    `profile` kommt roh aus `company_profile.fetch_profiles` (Zahlen, keine
    Strings). Umsatz und Gewinn je Aktie werden hier zu fertigen SVG-Balken-
    diagrammen — Jinja baut keine Geometrie, das gehoert nach Python.
    """
    out = {}
    for ticker, p in (profile or {}).items():
        if not p:
            continue
        kopfzeile = " · ".join(str(x) for x in [
            p.get("industrie"), p.get("land"),
            (f"{p['mitarbeiter']:,} Mitarbeitende".replace(",", ".")
             if p.get("mitarbeiter") else None),
        ] if x)
        out[ticker] = {
            "beschreibung": p.get("beschreibung", ""),
            "kopfzeile":    kopfzeile,
            "umsatz_q_chart": _bar_chart_svg(p.get("umsatz_q", []), True),
            "umsatz_j_chart": _bar_chart_svg(p.get("umsatz_j", []), True, jahres_reihe=True),
            "eps_q_chart":    _bar_chart_svg(p.get("eps_q", []), False),
            "begruendung":  p.get("_begruendung", []),
        }
    return out


def build_html_report(breadth, idx, risk, summary, report_date, weekly_data, leaders,
                      signals=None, pages_url=None,
                      alpaca_cash=None, alpaca_positions=None, alpaca_portfolio=None,
                      sector_excluded=None,
                      sp500_breadth_pct=None, min_breadth_pct=40,
                      test_mode=False, sector_rows=None, sector_heatmap=None,
                      profile=None, muster=None,
                      max_new_per_week=None, portfolio_max_positions=None):
    """Build the weekly HTML email.

    Parameters
    ----------
    signals : list[TradeSignal] | None
        Buy signals produced by signal_generator.generate_signals().
        Shown in Section 5 of the email; pass None or [] for "no signals".
    alpaca_cash : float | None
        Available buying power from Alpaca paper account.
    alpaca_positions : list[str] | None
        Tickers of currently held positions.
    profile : dict | None
        Unternehmensportraets je Ticker (aus `company_profile.fetch_profiles`,
        angereichert um `_begruendung`) — Quelle fuer Abschnitt 9.
    muster : list | None
        Titel mit erkanntem VCP/Launchpad (aus `mail_report.muster_liste`) —
        Quelle fuer Abschnitt 10.
    """
    signals = signals or []
    profile_display = _format_profile_for_report(profile or {})
    muster = muster or []

    # TradingView-Charts je Ticker — getrennte IDs je Sektion, falls derselbe
    # Titel sowohl Kaufsignal als auch Musterzeile ist.
    tv_signal_charts = {
        s.ticker: _tv_advanced_chart(s.ticker, _tv_chart_id("tv_sig", s.ticker))
        for s in signals
    }
    tv_muster_charts = {
        m["ticker"]: _tv_advanced_chart(m["ticker"], _tv_chart_id("tv_mus", m["ticker"]), height=360)
        for m in muster
    }

    # ── Recent closed trades (last 7 days) ───────────────────────────────────
    _EXIT_LABELS = {
        "stop_hit":      "Stop Loss",
        "manual":        "Manuell",
        "manual_market": "Manuell",
    }
    recent_trades = []
    try:
        _tf = Path(__file__).parent / "docs" / "data" / "trades.json"
        if _tf.exists():
            _all = json.loads(_tf.read_text(encoding="utf-8")).get("closed", [])
            _cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
            for t in _all:
                if t.get("exit_date", "") >= _cutoff:
                    reason = t.get("exit_reason", "")
                    label  = _EXIT_LABELS.get(reason, reason or "–")
                    # Differentiate initial vs. raised stop
                    if reason == "stop_hit":
                        init_stop = t.get("initial_stop")
                        exit_px   = t.get("exit_price", 0)
                        if init_stop and abs(exit_px - init_stop) / init_stop > 0.03:
                            label = "Stop nachgezogen"
                        else:
                            label = "Initialer Stop Loss"
                    recent_trades.append(SimpleNamespace(**{**t, "exit_reason_label": label}))
            recent_trades.sort(key=lambda x: x.exit_date, reverse=True)
    except Exception:
        pass

    # Derive market_bullish from the signal list:
    # if the market filter was active, generate_signals returns an empty list.
    # We surface this to the template so it can show the right "why no signals" text.
    market_bullish = True   # assume bullish; generator already filtered if bearish

    # Build Minervini criteria lookup for the Cloudflare Pages scorecard row
    _MINERVINI_CRITERIA = [
        "SMA10W steigend", "SMA30W steigend", "SMA40W steigend",
        "MA-Ordnung 10>30>40", "52W Range OK", "RS-Trend ↑",
        "Vol-Breakout", "Close > Vorwoche",
    ]
    signal_criteria: dict = {}
    for sig in signals:
        if sig.ticker in leaders.index:
            row = leaders.loc[sig.ticker]
            signal_criteria[sig.ticker] = {
                c: bool(row[c]) for c in _MINERVINI_CRITERIA if c in leaders.columns
            }

    # 1) Divergenzen & Breadth-Snapshots
    divergences  = build_divergence_text(idx)
    breadth_snap = compute_breadth_snapshots(weekly_data, offsets=[0, 1, 4])

    # NH/NL-Ratio als zusätzliche Zeile
    try:
        nh_row = breadth_snap.loc["Neue 52W\u2011Hochs (Anzahl)"].astype(float)
        nl_row = breadth_snap.loc["Neue 52W\u2011Tiefs (Anzahl)"].astype(float)
        total  = nh_row + nl_row
        ratio  = nh_row.where(total == 0, other=nh_row / total.where(total > 0, 1) * 100)
        breadth_snap.loc["NH/(NH+NL) (%)"] = ratio
    except Exception:
        pass

    # Marktampel berechnen
    ampel = compute_ampel(breadth_snap, idx)
    nhnl_badge = compute_nhnl_badge(breadth_snap)

    # Sparklines für die Breadth-Tabelle (13-Wochen-Trend je Zeile)
    breadth_sparklines = build_breadth_sparklines(weekly_data, breadth_snap)

    # 2) Leaders: Screener-Kandidaten Score ≥ 6, Top 20 nach Score/RS; echte Leader nur Score 8/8 + RS ≥ 85
    all_leaders_html = leaders.copy()
    if "score" in all_leaders_html.columns:
        all_leaders_html["score_num"] = pd.to_numeric(all_leaders_html["score"], errors="coerce")
        all_leaders_html = all_leaders_html[all_leaders_html["score_num"] >= 6]
        _sort_cols = ["score_num"]
        _sort_asc  = [False]
        if "RS (O'Neil)" in all_leaders_html.columns:
            all_leaders_html["_rs_num"] = pd.to_numeric(all_leaders_html["RS (O'Neil)"], errors="coerce")
            _sort_cols.append("_rs_num")
            _sort_asc.append(False)
        all_leaders_html = (
            all_leaders_html
            .sort_values(_sort_cols, ascending=_sort_asc)
            .head(20)
            .drop(columns=[c for c in ("score_num", "_rs_num") if c in all_leaders_html.columns])
        )
    leaders_html = leaders.copy()
    if "score" in leaders_html.columns:
        leaders_html["score_num"] = pd.to_numeric(leaders_html["score"], errors="coerce")
        leaders_html = leaders_html[leaders_html["score_num"] == 8]
        if "RS (O'Neil)" in leaders_html.columns:
            leaders_html["_rs_num"] = pd.to_numeric(leaders_html["RS (O'Neil)"], errors="coerce")
            leaders_html = leaders_html[leaders_html["_rs_num"] >= 85]
            # Score ist bei allen Zeilen 8/8 (konstant) — ohne expliziten
            # Sort haengt die Reihenfolge sonst am zufaelligen Universums-Scan.
            leaders_html = leaders_html.sort_values("_rs_num", ascending=False)
        leaders_html = leaders_html.drop(
            columns=[c for c in ("score_num", "_rs_num") if c in leaders_html.columns]
        )

    # 3) SA-Spalte in HTML-Buttons umwandeln
    def _sa_button(url: str) -> str:
        if not isinstance(url, str) or not url:
            return ""
        return (
            f'<a href="{url}" target="_blank" '
            f'style="display:inline-block;padding:4px 8px;'
            f'background-color:#007bff;color:white;'
            f'text-decoration:none;border-radius:4px;'
            f'font-size:12px;">SA</a>'
        )
    if "SA" in leaders_html.columns:
        leaders_html["SA"] = leaders_html["SA"].apply(_sa_button)

    # 4a) ΔRS 4W sicher auf numerisch konvertieren (verhindert str/int-Vergleich im Template)
    if 'ΔRS 4W' in leaders_html.columns:
        leaders_html['ΔRS 4W'] = pd.to_numeric(leaders_html['ΔRS 4W'], errors='coerce')
    if 'ΔRS 4W' in all_leaders_html.columns:
        all_leaders_html['ΔRS 4W'] = pd.to_numeric(all_leaders_html['ΔRS 4W'], errors='coerce')

    # 4b-pre) Filter-Analyse: welche Kaufkriterien hat jeder Leader nicht erfüllt?
    try:
        _rules = json.loads((Path(__file__).parent / "rules.json").read_text(encoding="utf-8"))
    except Exception:
        _rules = {}
    _min_rs         = float(_rules.get("filters", {}).get("min_rs_score",           70.0))
    _max_rank       = float(_rules.get("filters", {}).get("max_industry_rank",      50.0))
    _max_atr        = float(_rules.get("filters", {}).get("max_atr_pct",             8.0))
    _min_price      = float(_rules.get("filters", {}).get("min_price",               5.0))
    _min_cap        = float(_rules.get("filters", {}).get("min_market_cap_mio",    300.0))
    _min_rev_growth = float(_rules.get("filters", {}).get("min_rev_growth",          0.0))
    _min_eps_growth = float(_rules.get("filters", {}).get("min_eps_growth_last_q",   0.0))

    _sector_excluded: set = sector_excluded or set()

    def _compute_fails(row) -> str:
        return compute_filter_fails(
            row, sector_excluded=_sector_excluded, min_rs=_min_rs,
            max_rank=_max_rank, max_atr=_max_atr, min_price=_min_price,
            min_cap=_min_cap, min_rev_growth=_min_rev_growth,
            min_eps_growth=_min_eps_growth,
        )

    all_leaders_html["_filter_fails"] = all_leaders_html.apply(_compute_fails, axis=1)
    leaders_html["_filter_fails"]     = leaders_html.apply(_compute_fails, axis=1)

    # 4b) Vorformatierte Spalten für Card-Layout (NaN-sicher)
    def _card_fmt(val, fmt, sign=False):
        try:
            if pd.isna(val):
                return '–'
            f = float(val)
            s = fmt % f
            return ('+' if (sign and f > 0) else '') + s
        except Exception:
            return '–'

    for _src, _fmt, _sign, _dst in [
        ("RS (O'Neil)",                  '%.0f', False, '_card_rs'),
        ('ΔRS 4W',                       '%.0f', True,  '_card_drs'),
        ('Close',                        '%.2f', False, '_card_close'),
        ('Revenue Wachstum TTM YoY (%)', '%.0f', True,  '_card_rev'),
        ('EPS Wachstum FWD/TTM (%)',     '%.0f', True,  '_card_eps'),
        ('Dist to 52W High (%)',         '%.1f', False, '_card_dist'),
    ]:
        leaders_html[_dst] = (
            leaders_html[_src].apply(lambda v, f=_fmt, s=_sign: _card_fmt(v, f, s))
            if _src in leaders_html.columns else '–'
        )

    # 5) Template rendern
    tmpl = Template(HTML_TMPL)
    html = tmpl.render(
        breadth          = breadth,
        breadth_snap     = breadth_snap,
        idx              = idx,
        risk             = risk,
        summary          = summary,
        report_date      = report_date,
        leaders          = leaders_html,
        all_leaders      = all_leaders_html,
        signals            = signals,
        market_bullish     = market_bullish,
        sp500_breadth_pct  = sp500_breadth_pct,
        min_breadth_pct    = min_breadth_pct,
        COLOR_POSITIVE     = COLOR_POSITIVE,
        COLOR_NEGATIVE     = COLOR_NEGATIVE,
        COLOR_POS_TEXT     = COLOR_POS_TEXT,
        COLOR_NEG_TEXT     = COLOR_NEG_TEXT,
        divergences        = divergences,
        pages_url          = pages_url,
        alpaca_cash        = alpaca_cash,
        alpaca_positions   = alpaca_positions or [],
        alpaca_portfolio   = alpaca_portfolio,
        signal_criteria    = signal_criteria,
        test_mode          = test_mode,
        ampel              = ampel,
        nhnl_badge         = nhnl_badge,
        breadth_sparklines = breadth_sparklines,
        sector_rows        = sector_rows or [],
        sector_heatmap     = sector_heatmap or {"dates": [], "rows": []},
        sector_bar_svg     = build_sector_bar_svg(sector_rows or []),
        tv_watchlist       = build_tv_watchlist_string(signals or []),
        tv_watchlist_leaders = build_tv_watchlist_string(list(leaders_html.index) if leaders_html is not None else []),
        tv_watchlist_muster  = build_tv_watchlist_string(muster or []),
        recent_trades      = recent_trades,
        profile_display    = profile_display,
        muster             = muster,
        tv_signal_charts   = tv_signal_charts,
        tv_muster_charts   = tv_muster_charts,
        max_new_per_week        = max_new_per_week,
        portfolio_max_positions = portfolio_max_positions,
    )
    return html

def build_index_rows(idx_data: Dict[str, pd.DataFrame]) -> List[Tuple[str, dict]]:
    rows = []
    mapping = {"SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq 100 (QQQ)", "IWM": "Russell 2000 (IWM)"}
    for sym, label in mapping.items():
        df = idx_data.get(sym, pd.DataFrame())
        if df is None or df.empty or "Close" not in df:
            continue
        close = df["Close"]
        close = close.squeeze().dropna()
        if len(close) < 30:
            continue
        rsi_now = rsi(close).iloc[-1]
        rsi_prev = rsi(close).iloc[-2] if len(close) >= 16 else rsi_now
        m, s, h = macd(close)
        macd_line = m.iloc[-1]
        signal_line = s.iloc[-1]
        delta_macd = (m - s).diff().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        above_10w = (close.iloc[-1] - ma10) / ma10 if pd.notna(ma10) and ma10 != 0 else 0.0
        row = {
            "Close": float(close.iloc[-1]),
            "Δ WoW": float(close.pct_change().iloc[-1]) * 100,
            "RSI(14)": float(rsi_now) if pd.notna(rsi_now) else float("nan"),
            "Δ RSI": float(rsi_now - rsi_prev) if pd.notna(rsi_now) and pd.notna(rsi_prev) else float("nan"),
            "MACD": float(macd_line),
            "Signal": float(signal_line),
            "Δ MACD": float(delta_macd),
            "vs 10W MA": float(above_10w) * 100,
        }
        rows.append((label, row))
    return rows

def heuristic_verdict(breadth: pd.DataFrame, idx_rows: List[Tuple[str, dict]]) -> str:
    if breadth.empty:
        return "Daten unvollständig."
    b = breadth.iloc[0]
    strong_breadth = (b['%>50w'] > 55) and (b['advancers_wow_%'] > 55)
    weak_breadth = (b['%>50w'] < 45) or (b['advancers_wow_%'] < 45)
    spy_rsi = [r for n, r in idx_rows if n.startswith('S&P')][0]['RSI(14)']
    qqq_rsi = [r for n, r in idx_rows if n.startswith('Nasdaq')][0]['RSI(14)']
    if strong_breadth and spy_rsi > 50 and qqq_rsi > 50:
        return "Akkumulationsmodus: Übergewichtung zulässig, selektiv zukaufen."
    if weak_breadth and (spy_rsi < 50 or qqq_rsi < 50):
        return "Distribution/Schutzmodus: Risiko reduzieren, Stops nachziehen, Neuzukäufe selektiv."
    return "Neutral: Selektiv vorgehen, auf Bestätigungen warten."

def build_index_page(reports_dir, base_url: str, ampel=None) -> str:
    """Erzeugt das Dashboard (docs/index.html) mit Mini-KPIs, Nav-Karten und Report-Archiv."""
    from pathlib import Path
    import datetime

    reports_dir  = Path(reports_dir)
    report_files = sorted(reports_dir.glob("????-??-??.html"), reverse=True)
    latest_rpt   = f"reports/{report_files[0].name}" if report_files else "reports/"

    # ── KPI data from portfolio_performance ──────────────────────────────────────
    try:
        import portfolio_performance as pp
        _trades = pp._load_trades()
        _all    = _trades.get("closed", []) + _trades.get("open", [])
        _dates  = [t["entry_date"] for t in _all if t.get("entry_date")]
        _trim   = min(_dates) if _dates else None
        _tm = pp._trade_metrics(_trades.get("closed", []), _trades.get("open", []))
        _em = pp._equity_metrics(pp._load_equity_history(), trim_from=_trim)

        def _fm(v, plus=False):
            if v is None: return "–"
            s = ("+" if v > 0 else "") if plus else ""
            return f"{s}{v:,.0f} $"

        eq_str   = _fm(_em["current_equity"])
        upl      = _tm["unrealized_pl"]
        upl_str  = _fm(upl, plus=True)
        upl_col  = "#1a8a1a" if upl and upl > 0 else "#cc2222" if upl and upl < 0 else "#333"
        cagr     = _em["cagr"]
        cagr_str = (f"+{cagr:.1f}%" if cagr > 0 else f"{cagr:.1f}%") if cagr is not None else "–"
        cagr_col = "#1a8a1a" if cagr and cagr > 0 else "#cc2222" if cagr and cagr < 0 else "#333"
    except Exception:
        eq_str = upl_str = cagr_str = "–"
        upl_col = cagr_col = "#333"

    # ── Ampel KPI ────────────────────────────────────────────────────────────────
    if ampel:
        ampel_text  = f"{ampel['emoji']} {ampel['label']}"
        ampel_style = f"color:{ampel['color']};background:{ampel['bg']};border-radius:6px;padding:.1em .5em"
        ampel_sub   = f"Score {ampel['score']}/6"
    else:
        ampel_text  = "–"
        ampel_style = "color:#333"
        ampel_sub   = "Nicht berechnet"

    # ── Archive list ─────────────────────────────────────────────────────────────
    _WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    _MO = ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
           "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

    def _human(stem):
        try:
            d = datetime.date.fromisoformat(stem)
            return f"{_WD[d.weekday()]}, {d.day:02d}. {_MO[d.month]} {d.year}"
        except Exception:
            return stem

    latest_stem  = report_files[0].stem if report_files else "–"
    latest_human = _human(latest_stem)

    archive_items = ""
    for f in report_files[1:]:
        archive_items += f"""
      <li class="report-item">
        <div>
          <a href="reports/{f.name}">{f.stem}</a>
          <div class="ri-meta">{_human(f.stem)}</div>
        </div>
      </li>"""

    older_count = max(0, len(report_files) - 1)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Weekly Screener – Dashboard</title>
  <style>
    :root {{
        --bg: #eef1f7; --surface: #ffffff; --surface-2: #f6f8fc;
        --border: #dde3ef; --border-strong: #c3cde0;
        --text: #10192b; --text-secondary: #4d5b73; --text-muted: #8593ab;
        --accent: #1f4fa3; --accent-soft: #e8eefa; --accent-text: #163c80;
        --font-sans: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        --font-mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", Consolas, monospace;
    }}
    @media (prefers-color-scheme: dark) {{
        :root {{
            --bg: #0a0f1a; --surface: #121a2b; --surface-2: #0f1626;
            --border: #223049; --border-strong: #2e4066;
            --text: #e8edf7; --text-secondary: #a3b1c9; --text-muted: #6b7a97;
            --accent: #6c9bef; --accent-soft: #16233e; --accent-text: #9dbdf5;
        }}
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: var(--font-sans); margin: 0; background: var(--bg); color: var(--text); }}
    .g-nav   {{ background: var(--surface); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 0 1.5em;
                flex-wrap: wrap; position: sticky; top: 0; z-index: 100; }}
    .g-brand {{ font-family: var(--font-mono); font-weight: 600; color: var(--text-muted); text-decoration: none;
                padding: .72em 1.1em .72em 0; margin-right: .5em; border-right: 1px solid var(--border);
                white-space: nowrap; font-size: .78em; letter-spacing: .05em; text-transform: uppercase; }}
    .g-nav a {{ color: var(--text-secondary); text-decoration: none; padding: .72em .85em;
                font-size: .84em; white-space: nowrap; }}
    .g-nav a:hover  {{ color: var(--accent); background: var(--accent-soft); }}
    .g-nav a.active {{ color: var(--accent-text); box-shadow: inset 0 -2px var(--accent); font-weight: 600; }}
    .page {{ max-width: 900px; margin: 0 auto; padding: 2em 1em 3em; }}
    .page-title {{ color: var(--text); font-size: 1.6em; margin: 0 0 .15em; font-weight: 600; letter-spacing: -.01em; }}
    .page-sub   {{ color: var(--text-muted); font-size: .88em; margin: 0 0 1.8em; }}
    .section-h  {{ color: var(--text-muted); font-size: .78em; text-transform: uppercase;
                   letter-spacing: .08em; margin: 0 0 .75em; font-weight: 600;
                   padding-bottom: .35em; border-bottom: 1px solid var(--border-strong); }}
    .kpi-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .85em; margin-bottom: 2.2em; }}
    .kpi-card {{ background: var(--surface); border-radius: 10px; padding: .9em 1.1em;
                 border: 1px solid var(--border); border-top: 2px solid var(--accent); }}
    .kc-label {{ font-size: .72em; text-transform: uppercase; letter-spacing: .06em; color: var(--text-muted); margin-bottom: .3em; }}
    .kc-val   {{ font-size: 1.3em; font-weight: 700; color: var(--text); font-family: var(--font-mono); }}
    .kc-sub   {{ font-size: .75em; color: var(--text-muted); margin-top: .2em; }}
    .nav-row  {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: .85em; margin-bottom: 2.2em; }}
    .nav-card {{ background: var(--surface); border-radius: 10px; padding: 1em 1.2em;
                 border: 1px solid var(--border); text-decoration: none; color: inherit;
                 border-left: 3px solid var(--accent); display: block;
                 transition: border-color .15s, transform .12s; }}
    .nav-card:hover {{ border-color: var(--accent); transform: translateY(-1px); }}
    .nc-title {{ font-weight: 600; color: var(--text); font-size: .92em; margin-bottom: .2em; }}
    .nc-desc  {{ font-size: .79em; color: var(--text-muted); }}
    .nav-card.nc-green  {{ border-left-color: #27ae60; }}
    .nav-card.nc-teal   {{ border-left-color: #16a085; }}
    .nav-card.nc-orange {{ border-left-color: #e67e22; }}
    .nav-card.nc-purple {{ border-left-color: #8e44ad; }}
    .report-list {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5em; }}
    .report-item {{ background: var(--surface); border-radius: 10px; padding: .8em 1.1em;
                    border: 1px solid var(--border); }}
    .report-item a {{ color: var(--accent); text-decoration: none; font-weight: 600; font-size: .95em; }}
    .report-item a:hover {{ text-decoration: underline; }}
    .ri-meta {{ font-size: .78em; color: var(--text-muted); margin-top: .15em; }}
    details summary {{ cursor: pointer; color: var(--accent); font-size: .88em; padding: .4em 0; }}
    details summary:hover {{ text-decoration: underline; }}
    @media (max-width: 680px) {{
      .kpi-row {{ grid-template-columns: 1fr 1fr; }}
      .nav-row {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 420px) {{
      .kpi-row {{ grid-template-columns: 1fr; }}
      .nav-row {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <nav class="g-nav">
    <a href="index.html" class="g-brand active">📈 Weekly Screener</a>
    <a href="{latest_rpt}">Aktueller Report</a>
    <a href="trades.html">Trade Journal</a>
    <a href="performance.html">Performance</a>
    <a href="zertifikate/index.html">Zertifikate</a>
    <a href="blueprint.html">Blueprint</a>
  </nav>
  <div class="page">
    <h1 class="page-title">📈 Weekly Screener</h1>
    <p class="page-sub">Growth Stock Portfolio — automatischer Wochenreport</p>

    <h2 class="section-h">Depot-Überblick</h2>
    <div class="kpi-row">
      <div class="kpi-card">
        <div class="kc-label">Marktampel</div>
        <div class="kc-val" style="{ampel_style}">{ampel_text}</div>
        <div class="kc-sub">{ampel_sub}</div>
      </div>
      <div class="kpi-card">
        <div class="kc-label">Depot-Equity</div>
        <div class="kc-val">{eq_str}</div>
        <div class="kc-sub">Aktuell</div>
      </div>
      <div class="kpi-card">
        <div class="kc-label">Unrealized P&amp;L</div>
        <div class="kc-val" style="color:{upl_col}">{upl_str}</div>
        <div class="kc-sub">Offene Positionen</div>
      </div>
      <div class="kpi-card">
        <div class="kc-label">CAGR (ann.)</div>
        <div class="kc-val" style="color:{cagr_col}">{cagr_str}</div>
        <div class="kc-sub">Annualisiert</div>
      </div>
    </div>

    <h2 class="section-h">Navigation</h2>
    <div class="nav-row">
      <a href="{latest_rpt}" class="nav-card">
        <div class="nc-title">📋 Aktueller Report</div>
        <div class="nc-desc">Wöchentliche Marktanalyse &amp; Signale</div>
      </a>
      <a href="trades.html" class="nav-card nc-green">
        <div class="nc-title">📒 Trade Journal</div>
        <div class="nc-desc">Offene &amp; abgeschlossene Positionen</div>
      </a>
      <a href="performance.html" class="nav-card nc-teal">
        <div class="nc-title">📊 Performance</div>
        <div class="nc-desc">Equity-Kurve, KPIs &amp; Analyse</div>
      </a>
      <a href="zertifikate/index.html" class="nav-card nc-orange">
        <div class="nc-title">🏷️ Zertifikate-Scanner</div>
        <div class="nc-desc">Low-Vol Momentum Screener</div>
      </a>
      <a href="blueprint.html" class="nav-card nc-purple">
        <div class="nc-title">📐 Blueprint</div>
        <div class="nc-desc">Regelwerk &amp; Handelssystem</div>
      </a>
    </div>

    <h2 class="section-h">Aktueller Report</h2>
    <ul class="report-list">
      <li class="report-item">
        <div>
          <a href="{latest_rpt}" style="color:#27ae60">{latest_stem}</a>
          <div class="ri-meta">{latest_human} — Aktuellster Report</div>
        </div>
      </li>
    </ul>
    <details style="margin-top:.8em">
      <summary>Ältere Reports anzeigen ({older_count} weitere)</summary>
      <ul class="report-list" style="margin-top:.6em">{archive_items}
      </ul>
    </details>
  </div>
</body>
</html>
"""


def build_divergence_text(idx: pd.DataFrame) -> str:
    messages = []
    for symbol in idx.columns:
        try:
            rsi = idx.loc["RSI(14)", symbol]
            delta_rsi = idx.loc["Δ RSI", symbol]
            delta_macd = idx.loc["Δ MACD", symbol]
            ret = idx.loc["Δ WoW", symbol]

            parts = []

            # Preis/RSI Divergenz
            if ret > 0 and delta_rsi < 0:
                parts.append("Kursanstieg bei fallendem RSI → möglicher Momentumverlust")
            elif ret < 0 and delta_rsi > 0:
                parts.append("Kursrückgang bei steigendem RSI → Druck lässt nach")

            # RSI vs MACD Divergenz
            if delta_rsi > 0 and delta_macd < 0:
                parts.append("RSI steigt, aber MACD fällt → kurzfristige Stärke, mittelfristig schwach")
            elif delta_rsi < 0 and delta_macd > 0:
                parts.append("RSI fällt, aber MACD steigt → mögliches Rebound-Signal")

            if parts:
                messages.append(f"<b>{symbol}</b>: " + " / ".join(parts))
        except Exception:
            continue

    return "<br>".join(messages) if messages else "Keine auffälligen Divergenzen erkannt."
