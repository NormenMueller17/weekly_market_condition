import datetime as dt
from typing import Dict
import pandas as pd
from jinja2 import Template

from indicators import rsi, macd, pct_above_ma
from typing import Dict, List, Tuple

HTML_TMPL = Template(
"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{{ title }}</title>
  <style>
    body { font-family: Arial, sans-serif; }
    h1, h2 { margin-bottom: 0.2rem; }
    table { border-collapse: collapse; width: 100%; margin-bottom: 1rem; }
    th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: right; }
    th { background: #f2f2f2; }
    .left { text-align: left; }
    .green { color: #0a7b2d; }
    .red { color: #b00020; }
    .yellow { color: #b38f00; }
    .badge { padding: 2px 6px; border-radius: 6px; background: #eee; }
  </style>
</head>
<body>
  <h1>{{ title }}</h1>
  <p><small>Report-Woche: {{ week_ending }}</small></p>

  <h2>1) Marktbreite</h2>
  <table>
    <tr>
      {% for col in breadth.columns %}<th class="left">{{ col }}</th>{% endfor %}
    </tr>
    <tr>
      {% for col in breadth.columns %}<td>{{ '%.2f' % breadth.iloc[0][col] if breadth.iloc[0][col] is not none else '' }}</td>{% endfor %}
    </tr>
  </table>

  <h2>2) Trend & Momentum (Weekly)</h2>
  <table>
    <tr>
      <th class="left">Index</th><th>Close</th><th>Δ WoW</th><th>RSI(14)</th><th>Δ RSI</th><th>MACD</th><th>Signal</th><th>Δ MACD</th><th>vs 10W MA</th>
    </tr>
    {% for name, row in idx_rows %}
    <tr>
      <td class="left">{{ name }}</td>
      <td>{{ '%.2f' % row['close'] }}</td>
      <td class="{{ 'green' if row['ret_wow']>0 else 'red' if row['ret_wow']<0 else '' }}">{{ '%.2f%%' % (row['ret_wow']*100) }}</td>
      <td>{{ '%.1f' % row['rsi'] }}</td>
      <td class="{{ 'green' if row['delta_rsi']>0 else 'red' if row['delta_rsi']<0 else '' }}">{{ '%.1f' % row['delta_rsi'] }}</td>
      <td>{{ '%.2f' % row['macd'] }}</td>
      <td>{{ '%.2f' % row['signal'] }}</td>
      <td class="{{ 'green' if row['delta_macd']>0 else 'red' if row['delta_macd']<0 else '' }}">{{ '%.2f' % row['delta_macd'] }}</td>
      <td class="{{ 'green' if row['above_10w']>0 else 'red' }}">{{ '%.2f%%' % (row['above_10w']*100) }}</td>
    </tr>
    {% endfor %}
  </table>

  <h2>3) Risiko & Sentiment</h2>
  <table>
    <tr><th class="left">Metrik</th><th>Aktuell</th><th>Vorwoche</th><th>Δ</th></tr>
    {% for name, now, prev in risk_rows %}
      {% set delta = (now - prev) if (now is not none and prev is not none) else none %}
      <tr>
        <td class="left">{{ name }}</td>
        <td>{{ '%.2f' % now if now is not none else '' }}</td>
        <td>{{ '%.2f' % prev if prev is not none else '' }}</td>
        <td class="{{ 'red' if name in ['VIX','CPC','UUP'] and delta and delta>0 else 'green' if name in ['TNX'] and delta and delta<0 else 'green' if delta and delta<0 else 'red' if delta and delta>0 else '' }}">{{ '%.2f' % delta if delta is not none else '' }}</td>
      </tr>
    {% endfor %}
  </table>

  <h2>4) Fazit</h2>
  <p>{{ verdict_text }}</p>
</body>
</html>
"""
)


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


def build_html_report(breadth: pd.DataFrame, idx_data: Dict[str, pd.DataFrame]) -> str:
    idx_rows = build_index_rows(idx_data)
    risk_rows = build_risk_rows(idx_data)
    verdict_text = heuristic_verdict(breadth, idx_rows)
    html = HTML_TMPL.render(
        title="Weekly US Market Report",
        week_ending=dt.date.today().isoformat(),
        breadth=breadth,
        idx_rows=idx_rows,
        risk_rows=risk_rows,
        verdict_text=verdict_text,
    )
    return html
