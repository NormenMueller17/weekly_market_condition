import datetime as dt
rsi_now = rsi(close).iloc[-1]
rsi_prev = rsi(close).iloc[-2]
m, s, h = macd(close)
row = {
'close': float(close.iloc[-1]),
'ret_wow': float(close.pct_change().iloc[-1]),
'rsi': float(rsi_now),
'delta_rsi': float(rsi_now - rsi_prev),
'macd': float(m.iloc[-1]),
'signal': float(s.iloc[-1]),
'delta_macd': float((m - s).diff().iloc[-1]),
'above_10w': float((close.iloc[-1] - close.rolling(10).mean().iloc[-1]) / close.rolling(10).mean().iloc[-1]),
}
rows.append((label, row))
return rows




def build_risk_rows(idx_data: Dict[str, pd.DataFrame]):
risk_keys = ["VIX", "CPC", "TNX", "UUP"]
out = []
for key in risk_keys:
df = idx_data[key].dropna()
close = df['Close']
now = float(close.iloc[-1]) if len(close) else None
prev = float(close.iloc[-2]) if len(close) > 1 else None
out.append((key, now, prev))
return out




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
