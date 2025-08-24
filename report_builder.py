import datetime as dt
from typing import Dict
import pandas as pd
from jinja2 import Template

from indicators import rsi, macd, pct_above_ma
from typing import Dict, List, Tuple
from breadth import compute_breadth, compute_breadth_snapshots

HTML_TMPL = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
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
              {% if 'Anzahl' in row %}
                <td>{{ val|int }}</td>
              {% else %}
                <td>{{ '%.2f%%' % val }}</td>
              {% endif %}
            {% endfor %}
        </tr>
        {% endfor %}
    </table>

    <h2>2) Trend & Momentum (Weekly)</h2>
    <table>
        <tr>
            <th class="left">Index</th>
            {% for col in idx.columns %}
            <th>{{ col }}</th>
            {% endfor %}
        </tr>
        {% for idx_name, row in idx.iterrows() %}
        <tr>
            <td class="left">{{ idx_name }}</td>
            {% for col in idx.columns %}
                {% set val = row[col] %}
                {% if col.startswith("Δ") or col.startswith("vs") %}
                    <td class="{{ 'pos' if val > 0 else 'neg' if val < 0 else '' }}">{{ '%.2f' % val }}%</td>
                {% elif col == 'RSI(14)' %}
                    <td>{{ '%.1f' % val }}</td>
                {% else %}
                    <td>{{ '%.2f' % val }}</td>
                {% endif %}
            {% endfor %}
        </tr>
        {% endfor %}
    </table>

    <h2>3) Risiko &amp; Sentiment</h2>
    <table>
        <tr>
            <th class="left">Metrik</th>
            <th>Aktuell</th>
            <th>Vorwoche</th>
            <th>Δ</th>
        </tr>
        {% for metric, row in risk.iterrows() %}
        <tr>
            <td class="left">{{ metric }}</td>
            <td>{{ '%.2f' % row['Aktuell'] if row['Aktuell'] is not none else '-' }}</td>
            <td>{{ '%.2f' % row['Vorwoche'] if row['Vorwoche'] is not none else '-' }}</td>
            <td class="{% if row['Δ'] > 0 %}pos{% elif row['Δ'] < 0 %}neg{% endif %}">
                {{ '%.2f' % row['Δ'] if row['Δ'] is not none else '-' }}
            </td>
        </tr>
        {% endfor %}
    </table>

    <h2>4) Fazit</h2>
    <p>{{ summary }}</p>
</body>
</html>
"""


def build_risk_rows(idx_data: Dict[str, pd.DataFrame]):
    risk_keys = ["VIX", "CPC", "TNX", "UUP"]
    out = []
    for key in risk_keys:
        df = idx_data.get(key, pd.DataFrame())
        if df is None or df.empty or "Close" not in df:
            out.append((key, None, None))
            continue
        close = df["Close"].dropna()
        if len(close) == 0:
            out.append((key, None, None))
            continue
        now = float(close.iloc[-1]) if len(close) >= 1 else None
        prev = float(close.iloc[-2]) if len(close) >= 2 else None
        out.append((key, now, prev))
    return out
  

def build_index_rows(idx_data: Dict[str, pd.DataFrame]) -> List[Tuple[str, dict]]:
    rows = []
    mapping = {"SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq 100 (QQQ)", "IWM": "Russell 2000 (IWM)"}
    for sym, label in mapping.items():
        df = idx_data.get(sym, pd.DataFrame())
        if df is None or df.empty or "Close" not in df:
            continue
        close = df["Close"]
        close = close.squeeze().dropna()   # 1D erzwingen, NaN raus
        if len(close) < 30:                # zu wenig Historie → überspringen
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
            "close": float(close.iloc[-1]),
            "ret_wow": float(close.pct_change().iloc[-1]),
            "rsi": float(rsi_now) if pd.notna(rsi_now) else float("nan"),
            "delta_rsi": float(rsi_now - rsi_prev) if pd.notna(rsi_now) and pd.notna(rsi_prev) else float("nan"),
            "macd": float(macd_line),
            "signal": float(signal_line),
            "delta_macd": float(delta_macd),
            "above_10w": float(above_10w),
        }
        rows.append((label, row))
    return rows


def heuristic_verdict(breadth: pd.DataFrame, idx_rows) -> str:
    """Einfache Heuristik für Ampel-Signal (später leicht austauschbar)."""
    if breadth.empty:
        return "Daten unvollständig."
    b = breadth.iloc[0]
    strong_breadth = (b['%>50w'] > 55) and (b['advancers_wow_%'] > 55)
    weak_breadth = (b['%>50w'] < 45) or (b['advancers_wow_%'] < 45)

    # Momentum-Check: RSI der SPY/QQQ
    spy_rsi = [r for n, r in idx_rows if n.startswith('S&P')][0]['rsi']
    qqq_rsi = [r for n, r in idx_rows if n.startswith('Nasdaq')][0]['rsi']

    if strong_breadth and spy_rsi > 50 and qqq_rsi > 50:
        return "Akkumulationsmodus: Übergewichtung zulässig, selektiv zukaufen."
    if weak_breadth and (spy_rsi < 50 or qqq_rsi < 50):
        return "Distribution/Schutzmodus: Risiko reduzieren, Stops nachziehen, Neuzukäufe selektiv."
    return "Neutral: Selektiv vorgehen, auf Bestätigungen warten."

def build_html_report(
    breadth: pd.DataFrame,
    breadth_snap: pd.DataFrame,
    idx: pd.DataFrame,
    risk: pd.DataFrame,
    summary: str,
    report_date: str
) -> str:
    """Erzeugt HTML-Report als String auf Basis vorbereiteter DataFrames."""

    if breadth.empty or idx.empty or risk.empty:
        raise ValueError("Eingabedaten für Report sind unvollständig oder leer.")

    tmpl = Template(HTML_TMPL)
    html = tmpl.render(
        breadth=breadth,
        breadth_snap=breadth_snap,
        idx=idx,
        risk=risk,
        summary=summary,
        report_date=report_date
    )
    return html
