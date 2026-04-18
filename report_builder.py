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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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

    {% if alpaca_portfolio %}
    <h2>5) 💼 Alpaca Portfolio</h2>

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

    <h2>6) 📈 Kaufsignale (Blueprint-Regelwerk)</h2>

    {% if not signals %}
    <p style="color:#888">
        Keine Kaufsignale diese Woche —
        {% if not market_bullish %}
        <strong>Marktfilter aktiv</strong>: S&amp;P 500 10W EMA &lt; 20W EMA.
        {% else %}
        Kriterien (Score ≥ 6/8 + Vol-Breakout + RS ≥ 70 + Industry Top 50) nicht erfüllt.
        {% endif %}
    </p>
    {% else %}
    <p style="margin-bottom:0.8em">
        <strong>Marktfilter:</strong> S&amp;P 500 10W EMA &gt; 20W EMA ✅ &nbsp;|&nbsp;
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
        <th>Rang</th>
        <th class="left">Ticker</th>
        <th class="left">Unternehmen</th>
        <th class="left">Sektor / Branche</th>
        <th class="left">Muster</th>
        <th>Entry</th>
        <th>Stop-Loss</th>
        <th>Stop %</th>
        <th>BO-Level</th>
        <th>Dist 52W H</th>
        <th>RS</th>
        <th>ΔRS 4W</th>
        <th>Industry<br>Rank</th>
        <th>ROE %</th>
        <th>Op.Margin</th>
        <th>Rev. Growth</th>
        <th>Position</th>
        <th>Risiko / Equity</th>
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
        <td style="background-color:#fff3e0">{{ '%.2f' % s.stop_loss }}</td>
        <td style="background-color:#fff3e0">{{ stop_pct_display }}%</td>
        <td>{{ '%.2f' % s.breakout_level if s.breakout_level else '–' }}</td>
        <td>{{ '%.1f' % s.dist_52w_high_pct if s.dist_52w_high_pct is not none else '–' }}%</td>
        <td><strong>{{ '%.0f' % s.rs_score if s.rs_score is not none else '–' }}</strong></td>
        <td style="background-color:
          {%- if s.rs_delta_4w and s.rs_delta_4w > 0 %}{{ COLOR_POSITIVE }}
          {%- elif s.rs_delta_4w and s.rs_delta_4w < 0 %}{{ COLOR_NEGATIVE }}
          {%- else %}transparent{% endif %}">
          {% if s.rs_delta_4w is not none %}{% if s.rs_delta_4w > 0 %}+{% endif %}{{ '%.0f' % s.rs_delta_4w }}{% else %}–{% endif %}
        </td>
        <td style="text-align:center">{{ s.industry_ranking if s.industry_ranking is not none else '–' }}</td>
        <td>{{ '%.1f' % s.roe if s.roe is not none else '–' }}%</td>
        <td>{{ '%.1f' % s.op_margin if s.op_margin is not none else '–' }}%</td>
        <td>{{ '%.1f' % s.revenue_growth if s.revenue_growth is not none else '–' }}%</td>
        <td>{{ (s.position_size_pct * 100) | round(1) }}%</td>
        <td style="{% if risk_high %}background-color:#ffcccc;font-weight:bold{% endif %}">
          {{ risk_pct_display }}%
        </td>
      </tr>
      {% endfor %}
    </table>
    <p style="font-size:0.82em;color:#777;margin-top:0.3em">
      Ranking-Score = RS(35%) + ΔRS 4W(20%) + Muster(20%) + Tightness(15%) + Industry(10%).
      🏆 = Top-{{ signals | selectattr("is_top_pick") | list | length }} Kaufkandidaten.
      Rot = Risiko/Equity &gt; 1.8%.
    </p>
    {% endif %}

    {% endif %}

    <h2>7) Marktführer nach Minervini (Score 8/8)</h2>

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
          <div style="font-size:0.82em;color:#555;display:flex;gap:8px;flex-wrap:wrap">
            {% if row["VCP"] or row["Launchpad"] %}
            <span style="background:#fff8e1;padding:1px 6px;border-radius:3px">
              📐&nbsp;{% if row["VCP"] and row["Launchpad"] %}VCP+Launchpad{% elif row["VCP"] %}VCP{% else %}Launchpad{% endif %}
            </span>
            {% endif %}
            {% if row["_card_dist"] != '–' %}
            <span>Dist 52W&nbsp;H:&nbsp;{{ row["_card_dist"] }}%</span>
            {% endif %}
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

def build_html_report(breadth, idx, risk, summary, report_date, weekly_data, leaders,
                      signals=None, pages_url=None,
                      alpaca_cash=None, alpaca_positions=None, alpaca_portfolio=None):
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

    # Derive market_bullish from the signal list:
    # if the market filter was active, generate_signals returns an empty list.
    # We surface this to the template so it can show the right "why no signals" text.
    market_bullish = True   # assume bullish; generator already filtered if bearish

    # 1) Divergenzen & Breadth-Snapshots
    divergences  = build_divergence_text(idx)
    breadth_snap = compute_breadth_snapshots(weekly_data, offsets=[0, 1, 4])

    # 2) Leaders: nur Score 8/8 im Mail-Report
    leaders_html = leaders.copy()
    if "score" in leaders_html.columns:
        leaders_html["score_num"] = pd.to_numeric(leaders_html["score"], errors="coerce")
        leaders_html = leaders_html[leaders_html["score_num"] == 8].drop(columns=["score_num"])

    # 3) SA-Spalte in HTML-Buttons umwandeln
    if "SA" in leaders_html.columns:
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
        leaders_html["SA"] = leaders_html["SA"].apply(_sa_button)

    # 4a) ΔRS 4W sicher auf numerisch konvertieren (verhindert str/int-Vergleich im Template)
    if 'ΔRS 4W' in leaders_html.columns:
        leaders_html['ΔRS 4W'] = pd.to_numeric(leaders_html['ΔRS 4W'], errors='coerce')

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
        signals          = signals,
        market_bullish   = market_bullish,
        COLOR_POSITIVE   = COLOR_POSITIVE,
        COLOR_NEGATIVE   = COLOR_NEGATIVE,
        divergences      = divergences,
        pages_url        = pages_url,
        alpaca_cash      = alpaca_cash,
        alpaca_positions = alpaca_positions or [],
        alpaca_portfolio = alpaca_portfolio,
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

def build_index_page(reports_dir, base_url: str) -> str:
    """Erzeugt das Dashboard (docs/index.html) mit einer Liste aller Reports."""
    from pathlib import Path
    import datetime

    reports_dir = Path(reports_dir)
    report_files = sorted(reports_dir.glob("*.html"), reverse=True)

    rows_html = ""
    for f in report_files:
        date_str = f.stem          # z.B. "2026-04-19"
        try:
            d = datetime.date.fromisoformat(date_str)
            label = d.strftime("%d. %B %Y")
        except ValueError:
            label = date_str
        url = f"{base_url}/reports/{f.name}"
        rows_html += (
            f'<li style="margin-bottom:8px">'
            f'<a href="{url}" style="color:#003d99;font-weight:bold">{label}</a>'
            f'</li>\n'
        )

    latest_url = f"{base_url}/reports/{report_files[0].name}" if report_files else "#"

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Weekly Market Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 2em auto; padding: 0 1em; color: #333; }}
    h1   {{ color: #003d99; }}
    .btn {{ display:inline-block; padding:12px 28px; background:#003d99; color:white;
             text-decoration:none; border-radius:6px; font-weight:bold; font-size:1em; margin-bottom:2em; }}
    ul   {{ padding-left: 1.2em; }}
    li a {{ color: #003d99; }}
  </style>
</head>
<body>
  <h1>📈 Weekly Market Report</h1>
  <p>Automatisch generierter wöchentlicher Marktreport nach dem Financial Wisdom Blueprint.</p>
  <a href="{latest_url}" class="btn">Aktuellen Report öffnen →</a>
  <h2>Archiv</h2>
  <ul>
{rows_html}  </ul>
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
