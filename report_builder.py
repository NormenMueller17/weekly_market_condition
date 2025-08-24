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

def build_index_rows(idx_data: Dict[str, pd.DataFrame]) -> List[Tuple[str, dict]]:
    rows = []
    mapping = {
        "SPY": "S&P 500 (SPY)",
        "QQQ": "Nasdaq 100 (QQQ)",
        "IWM": "Russell 2000 (IWM)"
    }

    for sym, label in mapping.items():
        df = idx_data.get(sym, pd.DataFrame())
        if df is None or df.empty or "Close" not in df:
            continue

        #close = df["Close"].dropna()
        close = pd.Series(df["Close"]).dropna()
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
            "Δ WoW": float(close.pct_change().iloc[-1]),
            "RSI(14)": float(rsi_now) if pd.notna(rsi_now) else float("nan"),
            "Δ RSI": float(rsi_now - rsi_prev) if pd.notna(rsi_now) and pd.notna(rsi_prev) else float("nan"),
            "MACD": float(macd_line),
            "Signal": float(signal_line),
            "Δ MACD": float(delta_macd),
            "vs 10W MA": float(above_10w),
        }
        rows.append((label, row))
    return rows

def build_risk_rows(idx_data: Dict[str, pd.DataFrame]) -> List[Tuple[str, Dict[str, float]]]:
    """
    Erzeugt Risiko-Metriken (z. B. VIX, CPC) aus Indexdaten.
    Gibt eine Liste von Tupeln mit Metriknamen und dict mit 'Aktuell', 'Vorwoche', 'Δ' zurück.
    """
    RISK_KEYS = ["VIX", "CPC", "TNX", "UUP"]
    rows = []

    for key in RISK_KEYS:
        df = idx_data.get(key, pd.DataFrame())
        if df is None or df.empty or "Close" not in df:
            continue

        s = df["Close"].dropna()
        if len(s) < 2:
            continue

        now = s.iloc[-1]
        prev = s.iloc[-2]
        delta = now - prev

        rows.append((key, {
            "Aktuell": float(now),
            "Vorwoche": float(prev),
            "Δ": float(delta)
        }))

    return rows

def heuristic_verdict(breadth: pd.DataFrame, idx_rows: List[Tuple[str, dict]]) -> str:
    """
    Einfache Heuristik zur Einschätzung der Marktlage auf Basis von Breadth und Momentum.
    """
    if breadth.empty:
        return "Keine ausreichenden Daten verfügbar."

    b = breadth.iloc[0]
    strong_breadth = (b['%>50w'] > 55) and (b['advancers_wow_%'] > 55)
    weak_breadth = (b['%>50w'] < 45) or (b['advancers_wow_%'] < 45)

    # RSI als Momentum-Indikator für SPY & QQQ
    try:
        spy_rsi = [r for n, r in idx_rows if n.startswith("S&P")][0]["rsi"]
        qqq_rsi = [r for n, r in idx_rows if n.startswith("Nasdaq")][0]["rsi"]
    except Exception:
        return "Keine verlässliche RSI-Datenlage vorhanden."

    if strong_breadth and spy_rsi > 50 and qqq_rsi > 50:
        return "Akkumulationsmodus: Übergewichtung zulässig, selektiv zukaufen."
    if weak_breadth and (spy_rsi < 50 or qqq_rsi < 50):
        return "Distribution/Schutzmodus: Risiko reduzieren, Stops nachziehen, Neuzukäufe selektiv."
    return "Neutral: Selektiv vorgehen, auf Bestätigungen warten."
