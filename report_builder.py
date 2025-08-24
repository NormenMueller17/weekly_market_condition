import datetime as dt
from typing import Dict, List, Tuple
import pandas as pd
from jinja2 import Template

from indicators import rsi, macd, pct_above_ma
from breadth import compute_breadth_snapshots

# Farben für Zellhintergründe
POS_BG = "#99FF33"  # grün
NEG_BG = "#FF7C80"  # rot

HTML_TMPL = """
<!DOCTYPE html>
<html>
<head>
    <meta charset=\"UTF-8\">
    <style>
        body    { font-family: Arial, sans-serif; margin: 2em; }
        table   { border-collapse: collapse; margin-bottom: 2em; }
        th, td  { border: 1px solid #ccc; padding: 0.4em 0.8em; text-align: right; }
        th.left, td.left { text-align: left; }
        .pos    { color: green; }
        .neg    { color: red; }
    </style>
</head>
<body>
    <h1>Weekly US Market Report</h1>
    <p><strong>Report-Woche:</strong> {{ report_date }}</p>

    <h2>1) Marktbreite</h2>
    <table>
        <tr>
            {% for col in breadth.columns %}
            <th>{{ col }}</th>
            {% endfor %}
        </tr>
        <tr>
            {% for col in breadth.columns %}
            <td>{{ '%.2f' % breadth[col] if col != 'universe_size' else '%d' % breadth[col] }}</td>
            {% endfor %}
        </tr>
    </table>

    <h2>1b) Marktbreite – Vergleich</h2>
    <table>
        <tr>
            <th class=\"left\"></th>
            {% for col in breadth_snap.columns %}
              <th class=\"left\">{{ col }}</th>
            {% endfor %}
        </tr>
        {% for row in breadth_snap.index %}
        <tr>
            <td class=\"left\">{{ row }}</td>
            {% for col in breadth_snap.columns %}
              {% set val = breadth_snap.loc[row, col] %}
              {% set is_current = col == 'Aktuelle Woche' %}
              {% set val_w1 = breadth_snap.loc[row, 'Woche −1'] %}
              {% set is_52w_low = 'Tief' in row %}
              {% set highlight = '' %}
              {% if is_current and val is not none and val_w1 is not none %}
                {% if is_52w_low %}
                  {% if val < val_w1 %}
                    {% set highlight = 'background-color:' ~ POS_BG %}
                  {% elif val > val_w1 %}
                    {% set highlight = 'background-color:' ~ NEG_BG %}
                  {% endif %}
                {% else %}
                  {% if val > val_w1 %}
                    {% set highlight = 'background-color:' ~ POS_BG %}
                  {% elif val < val_w1 %}
                    {% set highlight = 'background-color:' ~ NEG_BG %}
                  {% endif %}
                {% endif %}
              {% endif %}
              {% if 'Anzahl' in row %}
                <td style=\"{{ highlight }}\">{{ val|int }}</td>
              {% else %}
                <td style=\"{{ highlight }}\">{{ '%.2f%%' % val }}</td>
              {% endif %}
            {% endfor %}
        </tr>
        {% endfor %}
    </table>

    <h2>2) Trend & Momentum (Weekly)</h2>
    <table>
        <tr>
            <th class=\"left\">Index</th>
            {% for col in idx.columns %}
            <th>{{ col }}</th>
            {% endfor %}
        </tr>
        {% for idx_name, row in idx.iterrows() %}
        <tr>
            <td class=\"left\">{{ idx_name }}</td>
            {% for col in idx.columns %}
                {% set val = row[col] %}
                {% if col.startswith(\"\u0394\") or col.startswith(\"vs\") %}
                    <td class=\"{{ 'pos' if val > 0 else 'neg' if val < 0 else '' }}\">{{ '%.2f' % val }}%</td>
                {% elif col == 'RSI(14)' %}
                    <td>{{ '%.1f' % val }}</td>
                {% else %}
                    <td>{{ '%.2f' % val }}</td>
                {% endif %}
            {% endfor %}
        </tr>
        {% endfor %}
    </table>

    <h2>3) Risiko & Sentiment</h2>
    <table>
        <tr>
            <th class=\"left\">Metrik</th>
            <th>Aktuell</th>
            <th>Vorwoche</th>
            <th>\u0394</th>
        </tr>
        {% for row in risk.iterrows() %}
        {% set name = row[0] %}
        {% set vals = row[1] %}
        <tr>
            <td class=\"left\">{{ name }}</td>
            <td>{{ '%.2f' % vals['Aktuell'] }}</td>
            <td>{{ '%.2f' % vals['Vorwoche'] }}</td>
            <td class=\"{{ 'pos' if vals['\u0394'] > 0 else 'neg' if vals['\u0394'] < 0 else '' }}\">{{ '%.2f' % vals['\u0394'] }}</td>
        </tr>
        {% endfor %}
    </table>

    <h2>4) Fazit</h2>
    <p>{{ summary }}</p>
</body>
</html>
"""

def build_html_report(breadth, idx, risk, summary, report_date, weekly_data):
    breadth_snap = compute_breadth_snapshots(weekly_data, offsets=[0, 1, 4])
    tmpl = Template(HTML_TMPL)
    html = tmpl.render(
        breadth=breadth,
        breadth_snap=breadth_snap,
        idx=idx,
        risk=risk,
        summary=summary,
        report_date=report_date,
        POS_BG=POS_BG,
        NEG_BG=NEG_BG
    )
    return html
