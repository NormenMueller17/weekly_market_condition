import datetime as dt
from typing import Dict, List, Tuple
import pandas as pd
from jinja2 import Template

from indicators import rsi, macd, pct_above_ma
from breadth import compute_breadth_snapshots_with_advancers as compute_breadth_snapshots


COLOR_POSITIVE = "#99ff33"  # RGB (153,255,51)
COLOR_NEGATIVE = "#ff7c80"  # RGB (255,124,128)

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
    </style>
</head>
<body>
    <h1>Weekly US Market Report</h1>
    <p><strong>Report-Woche:</strong> {{ report_date }}</p>

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
                  <td style="background-color: {{ COLOR_POSITIVE }}">{{ '%.2f%%' % val if '%' in row else val|int }}</td>
                {% elif (val < ref and not is_high_good) or (val > ref and is_high_good) %}
                  <td style="background-color: {{ COLOR_NEGATIVE }}">{{ '%.2f%%' % val if '%' in row else val|int }}</td>
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
                    <td style="background-color: {{ COLOR_POSITIVE if val > 0 else COLOR_NEGATIVE if val < 0 else 'transparent' }}">{{ '%.2f%%' % val }}</td>
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
            <td style="background-color: {{ COLOR_POSITIVE if farbe == 'pos' else COLOR_NEGATIVE if farbe == 'neg' else 'transparent' }}">
                {{ '%.2f' % vals['Δ'] }}
            </td>
        </tr>
        {% endfor %}
    </table>

    <h2>4) Fazit</h2>
    <p>{{ summary }}</p>

    <h2>5) Marktführer nach Minervini</h2>
    
    {% if leaders.empty %}
    <p>Keine Aktien erfüllen die Kriterien.</p>
    {% else %}
    
    <table>
      <tr>
        <th class="left">Ticker</th>
        <th class="left">Unternehmen</th>
        <th class="left">SA</th> 
        <th class="left">Branche</th>
        <th>Score</th>
        <th>RS</th>
        <th>EPS (Forward/TTM)</th>
        <th>EPS Wachstum<br>FWD/TTM (%)</th>
        <th>Revenue Wachstum<br>TTM YoY (%)</th>
        <th>Close</th>
        <th>Close Vorwoche</th>
        <th>Veränderung in %</th>        
        <th>52W High</th>
        <th>Dist<br>52W High (%)</th>
        <th>Ø-Vol<br>20W</th>
        <th>Vol Score</th>
      </tr>
    
      {% for idx, row in leaders.iterrows() %}
      {% set base_ticker = idx.split('.')[0] %}
      <tr>
        <td class="left">{{ idx }}</td>
        <td class="left">{{ row["Company"] }}</td>
        <td class="left">
          <a class="btn-sa"
             href="https://stockanalysis.com/stocks/{{ base_ticker }}"
             target="_blank">SA</a>
        </td>
        <td class="left">{{ row["Industry"] }}</td>
        <td>{{ row["score"] }}</td>
        <td>{{ row["RS (O'Neil)"] }}</td>
        <td>{{ row["EPS (Forward/TTM)"] }}</td>
        <td>{{ row["EPS Wachstum FWD/TTM (%)"] }}</td>
        <td>{{ row["Revenue Wachstum TTM YoY (%)"] }}</td>
        <td>{{ row["Close"] }}</td>
        <td>{{ row["Close Vorwoche"] }}</td>
        <td>{{ row["Veränderung in %"] }}</td>        
        <td>{{ row["52W High"] }}</td>
        <td>{{ row["Dist to 52W High (%)"] }}</td>
        <td>{{ row["Ø-Volume 20W"] }}</td>
        <td>{{ row["Volume Score"] }}</td>
      </tr>
      {% endfor %}
    
    </table>
    
    {% endif %}

    
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

def build_html_report(breadth, idx, risk, summary, report_date, weekly_data, leaders):
    divergences = build_divergence_text(idx)    
    breadth_snap = compute_breadth_snapshots(weekly_data, offsets=[0, 1, 4])
    tmpl = Template(HTML_TMPL)
    html = tmpl.render(
        breadth=breadth,
        breadth_snap=breadth_snap,
        idx=idx,
        risk=risk,
        summary=summary,
        report_date=report_date,
        leaders=leaders,
        COLOR_POSITIVE=COLOR_POSITIVE,
        COLOR_NEGATIVE=COLOR_NEGATIVE,
        divergences=divergences,
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
