import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Tuple
import pandas as pd
from jinja2 import Template

from indicators import rsi, macd, pct_above_ma
from breadth import compute_breadth_snapshots_with_advancers as compute_breadth_snapshots


COLOR_POSITIVE  = "#d4edda"   # soft green background
COLOR_NEGATIVE  = "#f8d7da"   # soft red background
COLOR_POS_TEXT  = "#155724"   # dark green text
COLOR_NEG_TEXT  = "#721c24"   # dark red text

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
        *, *::before, *::after { box-sizing: border-box; }
        body    { font-family: Arial, sans-serif; margin: 0; }
        .g-nav   { background: #003d99; display: flex; align-items: center; padding: 0 1.5em;
                   box-shadow: 0 2px 6px rgba(0,0,0,.22); flex-wrap: wrap; }
        .g-brand { font-weight: bold; color: #fff; text-decoration: none; padding: .72em 1.1em .72em 0;
                   margin-right: .5em; border-right: 1px solid rgba(255,255,255,.25);
                   white-space: nowrap; font-size: .95em; }
        .g-nav a { color: rgba(255,255,255,.82); text-decoration: none; padding: .72em .85em;
                   font-size: .84em; white-space: nowrap; }
        .g-nav a:hover  { color: #fff; background: rgba(255,255,255,.12); }
        .g-nav a.active { color: #fff; box-shadow: inset 0 -3px rgba(255,255,255,.8); font-weight: 600; }
        .page   { max-width: 1200px; margin: 0 auto; padding: 2em 1em 3em; }
        table   { border-collapse: collapse; margin-bottom: 2em; font-size: 0.93em; }
        th, td  { border: 1px solid #d0d5e8; padding: 0.45em 0.9em; text-align: right; }
        th      { background: #eef2fa; color: #003d99; font-weight: 600; white-space: nowrap; }
        th.left, td.left { text-align: left; }
        tr:hover td { background-color: #f6f8ff; }
        tr:hover td[style] { filter: brightness(0.97); }
        .pos    { color: #155724; }
        .neg    { color: #721c24; }
        .btn-sa {
            display: inline-block;
            padding: 2px 6px;
            background-color: #0052cc;
            color: #ffffff;
            font-size: 0.8em;
            border-radius: 4px;
            text-decoration: none;
        }
        .btn-sa:hover { background-color: #003d99; }
        /* Leader cards */
        .leader-grid { width: 100%; border-collapse: collapse; }
        .leader-cell { width: 50%; padding: 5px; vertical-align: top; }
        .leader-card {
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 12px 14px;
            background: #fff;
        }
        /* Portfolio cards */
        .portfolio-summary {
            background: #f5f7fa;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
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
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .portfolio-summary .summary-value {
            font-size: 1.1em;
            font-weight: bold;
        }
        @media only screen and (max-width: 600px) {
            body { margin: 0.8em; }
            .leader-cell { display: block !important; width: 100% !important; box-sizing: border-box; }
            .portfolio-summary td { display: block !important; width: 100% !important; text-align: left; padding: 2px 0; }
        }
        th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
        th.sortable:hover { background: #dde6f5; }
        th.sortable::after { content: ' ⇅'; font-size: 0.75em; color: #aaa; }
        th.sortable.asc::after  { content: ' ▲'; color: #333; }
        th.sortable.desc::after { content: ' ▼'; color: #333; }
        /* Marktampel */
        .ampel-wrap { position: relative; display: inline-block; margin-bottom: 1.5em; }
        .ampel-badge {
            display: inline-flex; align-items: center; gap: 8px;
            padding: 8px 18px; border-radius: 22px; font-weight: bold;
            font-size: 1.05em; cursor: default; border: 1px solid rgba(0,0,0,0.1);
            user-select: none;
        }
        .ampel-tooltip {
            display: none; position: absolute; left: 0; top: 115%;
            background: #fff; border: 1px solid #d0d5e8; border-radius: 8px;
            padding: 10px 16px; min-width: 300px; z-index: 100;
            box-shadow: 0 4px 14px rgba(0,0,0,0.13); font-size: 0.88em;
            white-space: nowrap;
        }
        .ampel-wrap:hover .ampel-tooltip { display: block; }
        .ampel-tooltip table { border: none; margin: 4px 0 0 0; font-size: 1em; }
        .ampel-tooltip td { border: none; padding: 3px 8px; }
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

    {% if ampel %}
    <div class="ampel-wrap">
      <div class="ampel-badge" style="background:{{ ampel.bg }};color:{{ ampel.color }}">
        {{ ampel.emoji }} Marktampel: <strong>{{ ampel.label }}</strong> &nbsp;({{ ampel.score }}/6)
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

    <h2>1) Marktbreite – Vergleich</h2>
    <table>
        <tr>
            <th class="left"></th>
            {% for col in breadth_snap.columns %}
              <th class="left">{{ col }}</th>
            {% endfor %}
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
        </tr>
        {% endfor %}
    </table>
    
    {% if sector_rows %}
    <h2>1b) Sektor-Performance (Wochenbasis)</h2>
    <table>
      <tr>
        <th class="left">Sektor</th>
        <th class="left" style="color:#888">ETF</th>
        <th>Performance Woche</th>
      </tr>
      {% for s in sector_rows %}
      <tr>
        <td class="left">{{ s.name }}</td>
        <td class="left" style="font-size:0.85em;color:#888">{{ s.ticker }}</td>
        <td style="background-color:{{ COLOR_POSITIVE if s.chg > 0 else COLOR_NEGATIVE if s.chg < 0 else 'transparent' }};color:{{ COLOR_POS_TEXT if s.chg > 0 else COLOR_NEG_TEXT if s.chg < 0 else 'inherit' }};font-weight:{{ 'bold' if s.chg != 0 else 'normal' }}">
          {{ '+' if s.chg > 0 else '' }}{{ '%.2f'|format(s.chg) }}%
        </td>
      </tr>
      {% endfor %}
    </table>
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
        Screener-Kandidaten (Score ≥ 6/8) — sortierbar, kein Handelssignal diese Woche:
    </p>
    <div style="overflow-x:auto">
    <table>
      <tr>
        <th class="left sortable" onclick="sortTable(this)">Ticker</th>
        <th class="left sortable" onclick="sortTable(this)">Unternehmen</th>
        <th class="left sortable" onclick="sortTable(this)">Industry</th>
        <th class="sortable" onclick="sortTable(this)">Score</th>
        <th class="left sortable" onclick="sortTable(this)">Muster</th>
        <th class="sortable" onclick="sortTable(this)">Close</th>
        <th class="sortable" onclick="sortTable(this)" style="color:#1565c0">RS</th>
        <th class="sortable" onclick="sortTable(this)">ΔRS 4W</th>
        <th class="sortable" onclick="sortTable(this)" style="color:#1565c0">Dist 52W H %</th>
        <th class="sortable" onclick="sortTable(this)" style="color:#1565c0">Ind. Rank</th>
        <th class="sortable" onclick="sortTable(this)">ATR %</th>
        <th class="sortable" onclick="sortTable(this)">Vol-Score</th>
        <th class="sortable" onclick="sortTable(this)">MarketCap<br>(Mio $)</th>
        <th class="sortable" onclick="sortTable(this)">SA</th>
        <th class="left sortable" onclick="sortTable(this)" style="color:#c62828">Scheitert an</th>
      </tr>
      {% for idx, row in all_leaders.iterrows() %}
      {% set rs_val = row.get("RS (O'Neil)", none) %}
      {% set dist_val = row.get("Dist to 52W High (%)", none) %}
      {% set ind_val = row.get("Industry Ranking", none) %}
      {% set drs_val = row["ΔRS 4W"] if "ΔRS 4W" in all_leaders.columns else none %}
      <tr>
        <td class="left"><strong style="color:#003d99">{{ idx }}</strong></td>
        <td class="left" style="font-size:0.85em;color:#555">{{ row.get("Company", "–") }}</td>
        <td class="left" style="font-size:0.85em;color:#555">{{ row.get("Industry", "–") }}</td>
        <td style="text-align:center">{{ row.get("score", "–") }}</td>
        <td class="left">
          {% set pat = row.get("VCP", false) %}
          {% set lp  = row.get("Launchpad", false) %}
          {% if pat %}<span style="background:#e8f5e9;padding:1px 5px;border-radius:3px;font-size:0.82em">VCP</span>{% endif %}
          {% if lp %}<span style="background:#fffde7;padding:1px 5px;border-radius:3px;font-size:0.82em">LP</span>{% endif %}
          {% if not pat and not lp %}<span style="color:#aaa;font-size:0.82em">–</span>{% endif %}
        </td>
        <td style="text-align:right">{{ row.get("Close", "–") }}</td>
        <td style="text-align:center;background:{% if rs_val != none and rs_val|float >= 70 %}#e8f5e9{% else %}transparent{% endif %}">
          {{ rs_val if rs_val != none else "–" }}
        </td>
        <td style="text-align:center;color:{% if drs_val is not none and drs_val > 0 %}#2e7d32{% elif drs_val is not none and drs_val < 0 %}#c62828{% else %}#555{% endif %}">
          {% if drs_val is not none %}{% if drs_val > 0 %}+{% endif %}{{ "%.0f"|format(drs_val) }}{% else %}–{% endif %}
        </td>
        <td style="text-align:center;background:{% if dist_val != none and dist_val|float <= 25 %}#e8f5e9{% else %}transparent{% endif %}">
          {{ dist_val if dist_val != none else "–" }}
        </td>
        <td style="text-align:center;background:{% if ind_val != none and ind_val|float <= 50 %}#e8f5e9{% else %}transparent{% endif %}">
          {{ ind_val if ind_val != none else "–" }}
        </td>
        <td style="text-align:center">{{ row.get("ATR / Price (%)", "–") }}</td>
        {% set vol_bo = row.get("Vol-Breakout", false) %}
        {% set vol_sc = row.get("Volume Score", none) %}
        <td style="text-align:center;background:{% if vol_bo %}#e8f5e9{% else %}transparent{% endif %}">
          {% if vol_sc is not none and vol_sc != "–" %}{{ "%.2f"|format(vol_sc|float) }}{% else %}–{% endif %}
        </td>
        <td style="text-align:right">{{ row.get("MarketCap (Mio USD)", "–") }}</td>
        <td style="text-align:center">{{ row.get("SA", "") }}</td>
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
          {%- else %}#e3f2fd{% endif %}">{{ s.pattern }}</td>
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
          {%- if s.rs_score is not none and s.rs_score >= 70 %}#d4edda
          {%- elif s.rs_score is not none %}#f8d7da
          {%- else %}transparent{% endif %}">
          <strong>{{ '%.0f' % s.rs_score if s.rs_score is not none else '–' }}</strong></td>
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
      🔵 Buy-Stop = Einstiegsorder (max(Entry, Pivot) +0.1%) &nbsp;|&nbsp;
      🔴 Max. Gap = Order verwerfen wenn Montag-Open über diesem Preis (Pivot +5%) &nbsp;|&nbsp;
      🟢/🔴 Scorecard = Minervini-Kriterien &nbsp;|&nbsp; Rot hinterlegt = Risiko/Equity &gt; 1.8%.
    </p>
    {% endif %}

    {% endif %}

    <h2>8) Marktführer nach Minervini (Score 8/8)</h2>

    {% if leaders.empty %}
    <p>Keine Aktien erfüllen alle 8 Minervini-Kriterien.</p>
    {% else %}

    {% set ns = namespace(col=0) %}
    <table class="leader-grid">
    {% for idx, row in leaders.iterrows() %}
      {% if ns.col == 0 %}<tr>{% endif %}
      <td class="leader-cell">
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
            <span style="font-size:0.87em">RS&nbsp;<strong>{{ row["_card_rs"] }}</strong></span>
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
      </td>
      {% set ns.col = ns.col + 1 %}
      {% if ns.col == 2 %}
        </tr>
        {% set ns.col = 0 %}
      {% endif %}
    {% endfor %}
    {% if ns.col == 1 %}<td class="leader-cell"></td></tr>{% endif %}
    </table>

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


def build_html_report(breadth, idx, risk, summary, report_date, weekly_data, leaders,
                      signals=None, pages_url=None,
                      alpaca_cash=None, alpaca_positions=None, alpaca_portfolio=None,
                      sector_excluded=None,
                      sp500_breadth_pct=None, min_breadth_pct=40,
                      test_mode=False, sector_rows=None):
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
    """
    signals = signals or []

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

    # 2) Leaders: Screener-Kandidaten Score ≥ 6; für Email nur Score 8/8
    all_leaders_html = leaders.copy()
    if "score" in all_leaders_html.columns:
        all_leaders_html["score_num"] = pd.to_numeric(all_leaders_html["score"], errors="coerce")
        all_leaders_html = all_leaders_html[all_leaders_html["score_num"] >= 6].drop(columns=["score_num"])
    leaders_html = leaders.copy()
    if "score" in leaders_html.columns:
        leaders_html["score_num"] = pd.to_numeric(leaders_html["score"], errors="coerce")
        leaders_html = leaders_html[leaders_html["score_num"] == 8].drop(columns=["score_num"])

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
    if "SA" in all_leaders_html.columns:
        all_leaders_html["SA"] = all_leaders_html["SA"].apply(_sa_button)

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
        fails = []
        if row.name in _sector_excluded:
            fails.append("Sektor-Limit")
        def _n(col): return pd.to_numeric(row.get(col, None), errors='coerce')
        if not bool(row.get("MACD > Signal (W)", False)):
            fails.append("MACD fällt")
        if not bool(row.get("Vol-Breakout", False)):
            fails.append("Vol-BO fehlt")
        rs = _n("RS (O'Neil)")
        if pd.isna(rs) or rs < _min_rs:
            fails.append(f"RS&nbsp;&lt;&nbsp;{_min_rs:.0f}")
        rank = _n("Industry Ranking")
        if not pd.isna(rank) and rank > _max_rank:
            fails.append(f"Rank&nbsp;&gt;&nbsp;{_max_rank:.0f}")
        atr = _n("ATR / Price (%)")
        if not pd.isna(atr) and atr > _max_atr:
            fails.append(f"ATR&nbsp;&gt;&nbsp;{_max_atr:.0f}%")
        price = _n("Close")
        if pd.isna(price):
            fails.append("Kurs fehlt")
        elif price < _min_price:
            fails.append(f"Kurs&nbsp;&lt;&nbsp;${_min_price:.0f}")
        cap = _n("MarketCap (Mio USD)")
        if not pd.isna(cap) and cap < _min_cap:
            fails.append(f"MCap&nbsp;&lt;&nbsp;{_min_cap:.0f}M")
        if _min_rev_growth > 0:
            rev = _n("Revenue Wachstum TTM YoY (%)")
            if pd.isna(rev):
                fails.append("Rev&nbsp;fehlt")
            elif rev < _min_rev_growth:
                fails.append(f"Rev&nbsp;&lt;&nbsp;{_min_rev_growth:.0f}%")
        if _min_eps_growth > 0:
            eps = _n("EPS Wachstum letztes Q YoY (%)")
            if pd.isna(eps):
                fails.append("EPS-Q&nbsp;fehlt")
            elif eps < _min_eps_growth:
                fails.append(f"EPS-Q&nbsp;&lt;&nbsp;{_min_eps_growth:.0f}%")
        return " · ".join(fails) if fails else "✅"

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
        sector_rows        = sector_rows or [],
        recent_trades      = recent_trades,
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
    .page {{ max-width: 900px; margin: 0 auto; padding: 2em 1em 3em; }}
    .page-title {{ color: #003d99; font-size: 1.6em; margin: 0 0 .15em; }}
    .page-sub   {{ color: #888; font-size: .88em; margin: 0 0 1.8em; }}
    .section-h  {{ color: #003d99; font-size: .78em; text-transform: uppercase;
                   letter-spacing: .08em; margin: 0 0 .75em;
                   padding-bottom: .35em; border-bottom: 2px solid #003d99; }}
    .kpi-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .85em; margin-bottom: 2.2em; }}
    .kpi-card {{ background: #fff; border-radius: 8px; padding: .9em 1.1em;
                 box-shadow: 0 1px 4px rgba(0,0,0,.07); border-top: 3px solid #003d99; }}
    .kc-label {{ font-size: .72em; text-transform: uppercase; letter-spacing: .06em; color: #888; margin-bottom: .3em; }}
    .kc-val   {{ font-size: 1.3em; font-weight: bold; color: #003d99; }}
    .kc-sub   {{ font-size: .75em; color: #aaa; margin-top: .2em; }}
    .nav-row  {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: .85em; margin-bottom: 2.2em; }}
    .nav-card {{ background: #fff; border-radius: 8px; padding: 1em 1.2em;
                 box-shadow: 0 1px 4px rgba(0,0,0,.08); text-decoration: none; color: inherit;
                 border-left: 4px solid #003d99; display: block;
                 transition: box-shadow .15s, transform .12s; }}
    .nav-card:hover {{ box-shadow: 0 4px 14px rgba(0,0,0,.13); transform: translateY(-1px); }}
    .nc-title {{ font-weight: bold; color: #003d99; font-size: .92em; margin-bottom: .2em; }}
    .nc-desc  {{ font-size: .79em; color: #999; }}
    .nav-card.nc-green  {{ border-left-color: #27ae60; }}
    .nav-card.nc-green  .nc-title {{ color: #27ae60; }}
    .nav-card.nc-teal   {{ border-left-color: #16a085; }}
    .nav-card.nc-teal   .nc-title {{ color: #16a085; }}
    .nav-card.nc-orange {{ border-left-color: #e67e22; }}
    .nav-card.nc-orange .nc-title {{ color: #e67e22; }}
    .nav-card.nc-purple {{ border-left-color: #8e44ad; }}
    .nav-card.nc-purple .nc-title {{ color: #8e44ad; }}
    .report-list {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .5em; }}
    .report-item {{ background: #fff; border-radius: 8px; padding: .8em 1.1em;
                    box-shadow: 0 1px 4px rgba(0,0,0,.07); }}
    .report-item a {{ color: #003d99; text-decoration: none; font-weight: 600; font-size: .95em; }}
    .report-item a:hover {{ text-decoration: underline; }}
    .ri-meta {{ font-size: .78em; color: #aaa; margin-top: .15em; }}
    details summary {{ cursor: pointer; color: #003d99; font-size: .88em; padding: .4em 0; }}
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
