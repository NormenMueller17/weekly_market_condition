"""
Portfolio Performance Dashboard — generates docs/performance.html.

Metrics from trades.json (always available):
  Win Rate, Profit Factor, Ø Win/Loss %, Largest Win/Loss,
  Ø Haltedauer, P&L by Sector, P&L by Pattern

Metrics from Alpaca portfolio history (fetched weekly):
  Equity Curve, Max Drawdown, CAGR, Current Equity
"""

import json
import datetime
import math
from pathlib import Path
from typing import Optional

TRADES_FILE  = Path("docs/data/trades.json")
EQUITY_FILE  = Path("docs/data/equity_history.json")
PERF_HTML    = Path("docs/performance.html")


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_trades() -> dict:
    if TRADES_FILE.exists():
        try:
            return json.loads(TRADES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"open": [], "closed": []}


def save_equity_history(portfolio_history: Optional[dict]) -> None:
    """Persist Alpaca portfolio history to docs/data/equity_history.json."""
    if not portfolio_history:
        return
    EQUITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    EQUITY_FILE.write_text(
        json.dumps({"updated": datetime.date.today().isoformat(), **portfolio_history},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_equity_history() -> Optional[dict]:
    if EQUITY_FILE.exists():
        try:
            return json.loads(EQUITY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


# ── Metric computation ────────────────────────────────────────────────────────

def _trade_metrics(closed: list, open_: list) -> dict:
    n = len(closed)
    wins   = [t for t in closed if (t.get("realized_plpc") or 0) > 0]
    losses = [t for t in closed if (t.get("realized_plpc") or 0) <= 0]

    win_pl  = sum(t.get("realized_pl", 0) for t in wins)
    loss_pl = abs(sum(t.get("realized_pl", 0) for t in losses))

    holding = []
    for t in closed:
        try:
            e = datetime.date.fromisoformat(t["entry_date"])
            x = datetime.date.fromisoformat(t["exit_date"])
            holding.append((x - e).days)
        except Exception:
            pass

    by_sector = {}
    for t in closed:
        key = t.get("sector") or "–"
        by_sector.setdefault(key, {"pl": 0.0, "count": 0})
        by_sector[key]["pl"]    += t.get("realized_pl", 0)
        by_sector[key]["count"] += 1

    by_pattern = {}
    for t in closed:
        key = t.get("pattern") or "–"
        by_pattern.setdefault(key, {"pl": 0.0, "count": 0})
        by_pattern[key]["pl"]    += t.get("realized_pl", 0)
        by_pattern[key]["count"] += 1

    return {
        "total_trades":      n,
        "wins":              len(wins),
        "losses":            len(losses),
        "win_rate":          len(wins) / n * 100 if n else None,
        "total_realized_pl": sum(t.get("realized_pl", 0) for t in closed),
        "profit_factor":     round(win_pl / loss_pl, 2) if loss_pl > 0 else None,
        "avg_win_pct":       sum(t.get("realized_plpc", 0) for t in wins)   / len(wins)   if wins   else None,
        "avg_loss_pct":      sum(t.get("realized_plpc", 0) for t in losses) / len(losses) if losses else None,
        "largest_win_pct":   max((t.get("realized_plpc", 0) for t in wins),   default=None),
        "largest_loss_pct":  min((t.get("realized_plpc", 0) for t in losses), default=None),
        "avg_holding_days":  round(sum(holding) / len(holding), 1) if holding else None,
        "unrealized_pl":     sum(t.get("unrealized_pl", 0) for t in open_),
        "open_count":        len(open_),
        "by_sector":         by_sector,
        "by_pattern":        by_pattern,
    }


def _equity_metrics(history: Optional[dict]) -> dict:
    empty = {"max_drawdown_pct": None, "cagr": None,
             "current_equity": None, "start_equity": None,
             "chart_labels": [], "chart_values": []}
    if not history:
        return empty

    ts  = history.get("timestamps", [])
    eq  = history.get("equity", [])
    pairs = [(t, e) for t, e in zip(ts, eq) if e is not None and e > 0]
    if not pairs:
        return empty

    labels = []
    values = []
    for t, e in pairs:
        try:
            d = datetime.datetime.utcfromtimestamp(int(t)).strftime("%Y-%m-%d")
        except Exception:
            d = str(t)
        labels.append(d)
        values.append(round(e, 2))

    # Max drawdown
    peak   = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd   = (peak - v) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # CAGR
    cagr = None
    if len(pairs) >= 2:
        try:
            t0    = datetime.datetime.utcfromtimestamp(int(pairs[0][0]))
            t1    = datetime.datetime.utcfromtimestamp(int(pairs[-1][0]))
            years = (t1 - t0).days / 365.25
            if years > 0.02 and values[0] > 0:  # at least ~1 week of data
                cagr = round(((values[-1] / values[0]) ** (1 / years) - 1) * 100, 1)
        except Exception:
            pass

    return {
        "max_drawdown_pct": round(max_dd, 2) if max_dd else None,
        "cagr":             cagr,
        "current_equity":   values[-1],
        "start_equity":     values[0],
        "chart_labels":     labels,
        "chart_values":     values,
    }


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt(v, suffix="", decimals=1, plus=False):
    if v is None:
        return "–"
    sign = "+" if plus and v > 0 else ""
    return f"{sign}{v:.{decimals}f}{suffix}"

def _fmt_money(v, plus=False):
    if v is None:
        return "–"
    sign = "+" if plus and v > 0 else ""
    return f"{sign}{v:,.0f} $"

def _color(v):
    if v is None:
        return ""
    return "color:#1a8a1a;font-weight:600" if v > 0 else "color:#cc2222;font-weight:600"


# ── Chart data helpers ────────────────────────────────────────────────────────

def _bar_chart_data(by_dict: dict) -> tuple[list, list, list]:
    """Return (labels, values, colors) sorted by P&L descending."""
    items = sorted(by_dict.items(), key=lambda x: x[1]["pl"], reverse=True)
    labels = [k for k, _ in items]
    values = [round(v["pl"], 2) for _, v in items]
    colors = ["rgba(26,138,26,.75)" if v >= 0 else "rgba(204,34,34,.75)" for v in values]
    return labels, values, colors


# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(tm: dict, em: dict) -> str:
    today = datetime.date.today().isoformat()

    # ── KPI card helper ───────────────────────────────────────────────────────
    def kpi(val, label, style=""):
        return (f'<div class="kpi"><div class="val" style="{style}">{val}</div>'
                f'<div class="lbl">{label}</div></div>')

    # ── KPI values ───────────────────────────────────────────────────────────
    wr_str   = _fmt(tm["win_rate"],  "%", 0)
    pf_str   = _fmt(tm["profit_factor"], decimals=2)
    rpl_str  = _fmt_money(tm["total_realized_pl"], plus=True)
    upl_str  = _fmt_money(tm["unrealized_pl"],     plus=True)
    aw_str   = _fmt(tm["avg_win_pct"],  "%", 1, plus=True)
    al_str   = _fmt(tm["avg_loss_pct"], "%", 1)
    lw_str   = _fmt(tm["largest_win_pct"],  "%", 1, plus=True)
    ll_str   = _fmt(tm["largest_loss_pct"], "%", 1)
    hd_str   = f"{tm['avg_holding_days']} Tage" if tm["avg_holding_days"] is not None else "–"
    dd_str   = _fmt(em["max_drawdown_pct"], "%", 1)
    cagr_str = _fmt(em["cagr"], "%", 1, plus=True)
    eq_str   = _fmt_money(em["current_equity"])

    rpl_color = _color(tm["total_realized_pl"])
    upl_color = _color(tm["unrealized_pl"])

    # ── Equity chart ──────────────────────────────────────────────────────────
    eq_labels = json.dumps(em["chart_labels"])
    eq_values = json.dumps(em["chart_values"])
    eq_min    = min(em["chart_values"]) * 0.98 if em["chart_values"] else 0
    eq_max    = max(em["chart_values"]) * 1.02 if em["chart_values"] else 100000
    eq_empty  = "true" if not em["chart_values"] else "false"

    # ── Sector chart ──────────────────────────────────────────────────────────
    sec_labels, sec_values, sec_colors = _bar_chart_data(tm["by_sector"])
    sec_labels_js = json.dumps(sec_labels)
    sec_values_js = json.dumps(sec_values)
    sec_colors_js = json.dumps(sec_colors)
    sec_empty  = "true" if not sec_labels else "false"

    # ── Pattern chart ─────────────────────────────────────────────────────────
    pat_labels, pat_values, pat_colors = _bar_chart_data(tm["by_pattern"])
    pat_labels_js = json.dumps(pat_labels)
    pat_values_js = json.dumps(pat_values)
    pat_colors_js = json.dumps(pat_colors)
    pat_empty  = "true" if not pat_labels else "false"

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Portfolio Performance</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body     {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 2em auto; padding: 0 1em; color: #333; }}
    h1       {{ color: #003d99; margin-bottom: .15em; }}
    h2       {{ color: #003d99; margin: 1.8em 0 .5em; border-bottom: 2px solid #003d99; padding-bottom: .2em; }}
    .meta    {{ color: #888; font-size: .85em; margin-bottom: 1.5em; }}
    .kpis    {{ display: flex; gap: 1em; flex-wrap: wrap; margin-bottom: 1.5em; }}
    .kpi     {{ background: #f5f7fa; border: 1px solid #dde; border-radius: 8px;
                padding: 10px 18px; min-width: 110px; text-align: center; flex: 1; }}
    .kpi .val {{ font-size: 1.35em; font-weight: bold; color: #003d99; }}
    .kpi .lbl {{ font-size: .78em; color: #888; margin-top: 3px; }}
    .charts2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5em; margin-bottom: 1.5em; }}
    .chart-box {{ background: #f9fafc; border: 1px solid #dde; border-radius: 8px; padding: 1em; }}
    .chart-box h3 {{ margin: 0 0 .6em; color: #003d99; font-size: .95em; }}
    .empty-note {{ color:#aaa; font-size:.85em; text-align:center; padding:2em 0; }}
    canvas   {{ max-height: 260px; }}
    .back    {{ display:inline-block; margin-bottom:1.5em; color:#003d99;
                text-decoration:none; font-size:.9em; }}
    .back:hover {{ text-decoration:underline; }}
    @media(max-width:700px) {{ .charts2 {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <a href="index.html" class="back">← Zurück zur Übersicht</a>
  <h1>Portfolio Performance</h1>
  <p class="meta">Stand: {today} &nbsp;|&nbsp; {tm['total_trades']} abgeschlossene Trades &nbsp;|&nbsp; {tm['open_count']} offene Positionen</p>

  <!-- KPI Row 1: Trade-Metriken -->
  <h2>Trade-Statistik</h2>
  <div class="kpis">
    {kpi(f"{tm['wins']}/{tm['total_trades']}", "Wins / Gesamt")}
    {kpi(wr_str, "Win Rate")}
    {kpi(pf_str, "Profit Factor")}
    {kpi(aw_str, "Ø Gewinner", "color:#1a8a1a")}
    {kpi(al_str, "Ø Verlierer", "color:#cc2222")}
    {kpi(lw_str, "Größter Gewinn", "color:#1a8a1a")}
    {kpi(ll_str, "Größter Verlust", "color:#cc2222")}
    {kpi(hd_str, "Ø Haltedauer")}
  </div>
  <div class="kpis">
    {kpi(rpl_str, "Realized P&amp;L", rpl_color)}
    {kpi(upl_str, "Unrealized P&amp;L (offen)", upl_color)}
    {kpi(eq_str,  "Aktuelles Depot-Equity")}
    {kpi(dd_str,  "Max. Drawdown", "color:#cc2222" if em["max_drawdown_pct"] else "")}
    {kpi(cagr_str,"CAGR (ann.)", "color:#1a8a1a" if em["cagr"] and em["cagr"] > 0 else "")}
  </div>

  <!-- Equity Chart -->
  <h2>Equity-Kurve</h2>
  <div class="chart-box" style="margin-bottom:1.5em">
    <canvas id="chartEquity"></canvas>
    <p class="empty-note" id="emptyEquity" style="display:none">Noch keine Daten verfügbar.</p>
  </div>

  <!-- P&L by Sector + Pattern -->
  <h2>P&amp;L-Analyse (abgeschlossene Trades)</h2>
  <div class="charts2">
    <div class="chart-box">
      <h3>P&amp;L nach Sektor</h3>
      <canvas id="chartSector"></canvas>
      <p class="empty-note" id="emptySector" style="display:none">Noch keine abgeschlossenen Trades.</p>
    </div>
    <div class="chart-box">
      <h3>P&amp;L nach Pattern</h3>
      <canvas id="chartPattern"></canvas>
      <p class="empty-note" id="emptyPattern" style="display:none">Noch keine abgeschlossenen Trades.</p>
    </div>
  </div>

  <script>
    // ── shared options ───────────────────────────────────────────────────────
    const fmtMoney = v => (v >= 0 ? '+' : '') + v.toLocaleString('de-DE', {{minimumFractionDigits:0, maximumFractionDigits:0}}) + ' $';

    // ── Equity curve ─────────────────────────────────────────────────────────
    const eqLabels = {eq_labels};
    const eqValues = {eq_values};
    if ({eq_empty}) {{
      document.getElementById('chartEquity').style.display = 'none';
      document.getElementById('emptyEquity').style.display = 'block';
    }} else {{
      new Chart(document.getElementById('chartEquity'), {{
        type: 'line',
        data: {{
          labels: eqLabels,
          datasets: [{{
            label: 'Depot-Equity',
            data: eqValues,
            borderColor: '#003d99',
            backgroundColor: 'rgba(0,61,153,.08)',
            fill: true,
            pointRadius: eqValues.length > 60 ? 0 : 3,
            tension: 0.3,
          }}]
        }},
        options: {{
          responsive: true,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{ callbacks: {{ label: ctx => fmtMoney(ctx.parsed.y) }} }},
          }},
          scales: {{
            x: {{ ticks: {{ maxTicksLimit: 10 }} }},
            y: {{
              min: {eq_min:.2f},
              max: {eq_max:.2f},
              ticks: {{ callback: v => '$' + (v/1000).toFixed(0) + 'k' }},
            }},
          }},
        }},
      }});
    }}

    // ── horizontal bar helper ─────────────────────────────────────────────────
    function makeBarChart(id, emptyId, labels, values, colors) {{
      if ({sec_empty} && id === 'chartSector' || {pat_empty} && id === 'chartPattern') {{
        document.getElementById(id).style.display    = 'none';
        document.getElementById(emptyId).style.display = 'block';
        return;
      }}
      new Chart(document.getElementById(id), {{
        type: 'bar',
        data: {{
          labels: labels,
          datasets: [{{ data: values, backgroundColor: colors, borderRadius: 4 }}]
        }},
        options: {{
          indexAxis: 'y',
          responsive: true,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{ callbacks: {{ label: ctx => fmtMoney(ctx.parsed.x) }} }},
          }},
          scales: {{
            x: {{ ticks: {{ callback: v => '$' + (v/1000).toFixed(0) + 'k' }} }},
          }},
        }},
      }});
    }}

    makeBarChart('chartSector',  'emptySector',  {sec_labels_js}, {sec_values_js}, {sec_colors_js});
    makeBarChart('chartPattern', 'emptyPattern', {pat_labels_js}, {pat_values_js}, {pat_colors_js});
  </script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def build_and_save(portfolio_history: Optional[dict] = None) -> Path:
    """Compute metrics, save equity history, write docs/performance.html."""
    if portfolio_history is not None:
        save_equity_history(portfolio_history)

    trades = _load_trades()
    eq_history = _load_equity_history()

    tm = _trade_metrics(trades.get("closed", []), trades.get("open", []))
    em = _equity_metrics(eq_history)

    PERF_HTML.parent.mkdir(parents=True, exist_ok=True)
    PERF_HTML.write_text(build_html(tm, em), encoding="utf-8")
    print(f"[PERF] Performance-Dashboard gespeichert → {PERF_HTML}")
    return PERF_HTML
