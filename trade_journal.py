"""
Trade Journal — persistentes Tracking aller Positionen.

Datei: docs/data/trades.json
  open   – aktuell offene Positionen (Einstandspreis, Stop-Levels, unrealized P&L)
  closed – abgeschlossene Trades (Exit-Preis, Grund, realized P&L)

Wird bei jedem Wochenrun synchronisiert:
  1. Neue Positionen aus Alpaca Portfolio eintragen
  2. Geschlossene Positionen via Alpaca Order History erkennen
  3. Aktuelle Preise und unrealized P&L aus Portfolio aktualisieren
  4. Raised Stops aus exit_manager eintragen
  5. docs/trades.html neu generieren
"""

import json
import datetime
from pathlib import Path
from typing import Optional

TRADES_FILE    = Path("docs/data/trades.json")
TRADES_HTML    = Path("docs/trades.html")
SIGNALS_DIR    = Path("artifacts")
SIGNALS_META_DIR = Path("docs/data")


# ── JSON helpers ──────────────────────────────────────────────────────────────

def _empty() -> dict:
    return {"generated": "", "open": [], "closed": []}


def load() -> dict:
    if TRADES_FILE.exists():
        try:
            return json.loads(TRADES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _empty()


def save(data: dict) -> None:
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["generated"] = datetime.date.today().isoformat()
    TRADES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Signal-JSON lookup ────────────────────────────────────────────────────────

def _signal_files(weeks_back: int = 16) -> list:
    """Return signal JSON files from both docs/data/ (committed) and artifacts/ (local run),
    sorted newest-first. docs/data/ files are preferred as they survive across CI runs."""
    committed = sorted(SIGNALS_META_DIR.glob("signals_meta_*.json"), key=lambda f: f.stem, reverse=True)
    local     = sorted(SIGNALS_DIR.glob("signals_*.json"), key=lambda f: f.stem, reverse=True)
    # Merge: committed files take priority; deduplicate by date suffix
    seen_dates = set()
    merged = []
    for f in committed + local:
        date_part = f.stem.split("_")[-1]  # "2026-04-19"
        if date_part not in seen_dates:
            seen_dates.add(date_part)
            merged.append(f)
    merged.sort(key=lambda f: f.stem, reverse=True)
    return merged[:weeks_back]


def _find_initial_stop(symbol: str, weeks_back: int = 16) -> Optional[float]:
    """Search recent signals JSON files for the initial stop of *symbol*."""
    for f in _signal_files(weeks_back):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            for sig in payload.get("signals", []):
                if sig.get("ticker") == symbol:
                    return sig.get("stop_loss")
        except Exception:
            continue
    return None


def _find_signal_meta(symbol: str, weeks_back: int = 16) -> dict:
    """Return pattern, company, rs_score, market_regime from the most recent signal."""
    for f in _signal_files(weeks_back):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            for sig in payload.get("signals", []):
                if sig.get("ticker") == symbol:
                    return {
                        "pattern":       sig.get("pattern", "–"),
                        "company":       sig.get("company", ""),
                        "sector":        sig.get("sector", ""),
                        "rs_score":      sig.get("rs_score"),
                        "market_regime": sig.get("market_regime", "bullish"),
                        "signal_date":   payload.get("generated", ""),
                    }
        except Exception:
            continue
    return {"pattern": "–", "company": "", "sector": "", "rs_score": None,
            "market_regime": "bullish", "signal_date": ""}


# ── Alpaca order history helpers ──────────────────────────────────────────────

def _entry_date_from_orders(symbol: str, filled_buys: list[dict]) -> str:
    """Return ISO date of the most recent filled buy order for *symbol*."""
    matches = [o for o in filled_buys if o["symbol"] == symbol and o["filled_avg_price"] > 0]
    if not matches:
        return datetime.date.today().isoformat()
    matches.sort(key=lambda o: o["filled_at"], reverse=True)
    raw = matches[0]["filled_at"]
    try:
        return raw[:10]   # "YYYY-MM-DD"
    except Exception:
        return datetime.date.today().isoformat()


def _exit_info_from_orders(symbol: str, filled_sells: list[dict]) -> Optional[dict]:
    """Return exit details for a closed position, or None if not found."""
    matches = [o for o in filled_sells if o["symbol"] == symbol and o["filled_avg_price"] > 0]
    if not matches:
        return None
    matches.sort(key=lambda o: o["filled_at"], reverse=True)
    best = matches[0]
    order_type = best.get("order_type", "").lower()
    if "stop" in order_type:
        reason = "stop_hit"
    elif "market" in order_type:
        reason = "manual_market"
    else:
        reason = "manual"
    return {
        "exit_price": best["filled_avg_price"],
        "exit_date":  best["filled_at"][:10],
        "exit_reason": reason,
        "exit_order_id": best["order_id"],
    }


# ── Core sync ─────────────────────────────────────────────────────────────────

def sync(
    portfolio: Optional[dict],
    filled_buys:  list[dict],
    filled_sells: list[dict],
) -> dict:
    """Synchronise trades.json with current Alpaca state.

    Args:
      portfolio    : output of alpaca_client.get_portfolio()
      filled_buys  : output of alpaca_client.get_filled_orders("buy")
      filled_sells : output of alpaca_client.get_filled_orders("sell")

    Returns the updated trades dict.
    """
    data = load()

    if portfolio is None:
        return data

    open_symbols        = {p["symbol"] for p in portfolio["positions"]}
    journal_open_syms   = {t["symbol"] for t in data["open"]}

    # 1. New positions: in Alpaca but not yet in journal
    for pos in portfolio["positions"]:
        sym = pos["symbol"]
        if sym in journal_open_syms:
            continue
        meta         = _find_signal_meta(sym)
        initial_stop = _find_initial_stop(sym)
        entry_date   = _entry_date_from_orders(sym, filled_buys)
        data["open"].append({
            "symbol":           sym,
            "company":          meta["company"],
            "sector":           meta.get("sector", ""),
            "pattern":          meta["pattern"],
            "market_regime":    meta["market_regime"],
            "rs_score":         meta["rs_score"],
            "entry_date":       entry_date,
            "entry_price":      pos["avg_entry_price"],
            "qty":              pos["qty"],
            "initial_stop":     initial_stop,
            "current_stop":     initial_stop,
            "stop_raised_date": None,
            "current_price":    pos["current_price"],
            "unrealized_pl":    pos["unrealized_pl"],
            "unrealized_plpc":  pos["unrealized_plpc"],
            # Profit-Taking state (Minervini / O'Neill)
            "pt_original_qty":    pos["qty"],
            "pt_is_fast_mover":   False,
            "pt_hold_until":      None,
            "pt_breakeven_done":  False,
            "pt_partial_1_done":  False,
            "pt_partial_1_qty":   0,
            "pt_partial_1_date":  None,
            "pt_partial_2_done":  False,
            "pt_partial_2_qty":   0,
            "pt_partial_2_date":  None,
            "pt_trailing_stop":   None,
        })
        print(f"[JOURNAL] ➕ {sym} neu eingetragen (Entry {entry_date} @ {pos['avg_entry_price']})")

    # 2. Closed positions: in journal but no longer in Alpaca
    still_open = []
    for trade in data["open"]:
        sym = trade["symbol"]
        if sym in open_symbols:
            still_open.append(trade)
            continue
        exit_info = _exit_info_from_orders(sym, filled_sells)
        if exit_info is None:
            # Position not found in Alpaca and no sell order — keep in open with a warning
            print(f"[JOURNAL] ⚠️  {sym} nicht in Portfolio, kein Sell-Order gefunden — übersprungen")
            still_open.append(trade)
            continue
        entry  = trade.get("entry_price") or 0
        ep     = exit_info["exit_price"]
        qty    = trade.get("qty") or 0
        pl     = (ep - entry) * qty
        pl_pct = ((ep / entry) - 1) * 100 if entry else 0
        closed_trade = {
            **trade,
            "exit_date":     exit_info["exit_date"],
            "exit_price":    ep,
            "exit_reason":   exit_info["exit_reason"],
            "exit_order_id": exit_info["exit_order_id"],
            "realized_pl":   round(pl, 2),
            "realized_plpc": round(pl_pct, 2),
        }
        # Remove open-only fields
        for k in ("current_price", "unrealized_pl", "unrealized_plpc",
                  "stop_raised_date", "current_stop"):
            closed_trade.pop(k, None)
        data["closed"].append(closed_trade)
        print(f"[JOURNAL] ✅ {sym} geschlossen: {exit_info['exit_reason']} "
              f"@ {ep:.2f}  P&L {pl:+.0f} ({pl_pct:+.1f}%)")

    data["open"] = still_open

    # 3. Update current price, qty and unrealized P&L from Alpaca (source of truth)
    pos_map = {p["symbol"]: p for p in portfolio["positions"]}
    for trade in data["open"]:
        sym = trade["symbol"]
        if sym in pos_map:
            p = pos_map[sym]
            trade["current_price"]   = p["current_price"]
            trade["unrealized_pl"]   = round(p["unrealized_pl"], 2)
            trade["unrealized_plpc"] = round(p["unrealized_plpc"], 2)
            trade["qty"]             = p["qty"]

    # 4. Backfill initial_stop for existing trades where it is still missing
    for trade in data["open"]:
        if trade.get("initial_stop") is None:
            stop = _find_initial_stop(trade["symbol"])
            if stop is not None:
                trade["initial_stop"] = stop
                if trade.get("current_stop") is None:
                    trade["current_stop"] = stop
                print(f"[JOURNAL] 🔁 {trade['symbol']} initial_stop nachgetragen: {stop}")

    # 5. Backfill rs_score / pattern / company / sector for existing trades where missing
    for trade in data["open"]:
        needs_meta = (
            trade.get("rs_score") is None
            or trade.get("pattern") in (None, "–", "")
            or not trade.get("company")
            or not trade.get("sector")
        )
        if needs_meta:
            meta = _find_signal_meta(trade["symbol"])
            if meta["rs_score"] is not None and trade.get("rs_score") is None:
                trade["rs_score"] = meta["rs_score"]
                print(f"[JOURNAL] 🔁 {trade['symbol']} rs_score nachgetragen: {meta['rs_score']}")
            if meta["pattern"] not in ("–", "", None) and trade.get("pattern") in (None, "–", ""):
                trade["pattern"] = meta["pattern"]
            if meta.get("company") and not trade.get("company"):
                trade["company"] = meta["company"]
            if meta.get("sector") and not trade.get("sector"):
                trade["sector"] = meta["sector"]

    # Sort closed: newest exit first
    data["closed"].sort(key=lambda t: t.get("exit_date", ""), reverse=True)

    save(data)
    return data


def apply_profit_taking(data: dict, pt_results: list[dict]) -> dict:
    """Persist profit-taking actions from exit_manager into open trades."""
    sym_map = {r["symbol"]: r for r in pt_results}
    today   = datetime.date.today().isoformat()

    for trade in data["open"]:
        sym = trade["symbol"]
        if sym not in sym_map:
            continue
        r       = sym_map[sym]
        actions = r.get("actions_taken", [])

        if r.get("is_fast_mover") and not trade.get("pt_is_fast_mover"):
            trade["pt_is_fast_mover"] = True
            trade["pt_hold_until"]    = r.get("hold_until")
            print(f"[JOURNAL] 🚀 {sym}: Fast Mover — halten bis {r.get('hold_until')}")

        if "breakeven" in actions:
            trade["pt_breakeven_done"] = True
            trade["current_stop"]      = r["breakeven_stop"]
            trade["stop_raised_date"]  = today

        if "partial_1" in actions:
            qty = r["partial_sell_1_qty"]
            trade["pt_partial_1_done"] = True
            trade["pt_partial_1_qty"]  = qty
            trade["pt_partial_1_date"] = today
            # Qty wird beim nächsten sync() von Alpaca bestätigt

        if "partial_2" in actions:
            qty = r["partial_sell_2_qty"]
            trade["pt_partial_2_done"] = True
            trade["pt_partial_2_qty"]  = qty
            trade["pt_partial_2_date"] = today

        if "trailing" in actions:
            trade["pt_trailing_stop"]  = r["trailing_stop_level"]
            trade["current_stop"]      = r["trailing_stop_level"]
            trade["stop_raised_date"]  = today

    save(data)
    return data


def apply_raised_stops(data: dict, exit_results: list[dict]) -> dict:
    """Write raised stop levels from exit_manager into open trades."""
    sym_map = {r["symbol"]: r for r in exit_results if r.get("cross") and r.get("new_stop")}
    for trade in data["open"]:
        sym = trade["symbol"]
        if sym not in sym_map:
            continue
        new_stop = sym_map[sym]["new_stop"]
        if new_stop and (trade.get("current_stop") or 0) < new_stop:
            trade["current_stop"]     = new_stop
            trade["stop_raised_date"] = datetime.date.today().isoformat()
    save(data)
    return data


# ── Summary stats ─────────────────────────────────────────────────────────────

def _stats(data: dict) -> dict:
    closed = data.get("closed", [])
    if not closed:
        return {"count": 0, "wins": 0, "losses": 0, "win_rate": None,
                "total_pl": 0.0, "avg_win": None, "avg_loss": None}
    wins   = [t for t in closed if (t.get("realized_plpc") or 0) > 0]
    losses = [t for t in closed if (t.get("realized_plpc") or 0) <= 0]
    return {
        "count":    len(closed),
        "wins":     len(wins),
        "losses":   len(losses),
        "win_rate": len(wins) / len(closed) * 100,
        "total_pl": sum(t.get("realized_pl", 0) for t in closed),
        "avg_win":  sum(t.get("realized_plpc", 0) for t in wins)  / len(wins)  if wins   else None,
        "avg_loss": sum(t.get("realized_plpc", 0) for t in losses)/ len(losses) if losses else None,
    }


# ── HTML page builder ─────────────────────────────────────────────────────────

def _fmt_pct(v, decimals=1):
    if v is None:
        return "–"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.{decimals}f}%"

def _fmt_money(v):
    if v is None:
        return "–"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:,.0f}"

def _color(v):
    if v is None or v == 0:
        return ""
    return "color:#1a8a1a;font-weight:600" if v > 0 else "color:#cc2222;font-weight:600"

def _exit_reason_label(r):
    return {"stop_hit": "Stop Hit", "manual": "Manuell",
            "manual_market": "Manuell"}.get(r, r or "–")

def _pt_status(t: dict) -> str:
    parts = []
    if t.get("pt_is_fast_mover"):
        hold = t.get("pt_hold_until", "")
        parts.append(f"&#x1F680; Fast Mover (bis {hold})")
    if t.get("pt_breakeven_done"):
        parts.append("&#x2705; Breakeven")
    if t.get("pt_partial_1_done"):
        parts.append(f"&frac13; TV1 {t.get('pt_partial_1_date','')}")
    if t.get("pt_partial_2_done"):
        parts.append(f"&frac13; TV2 {t.get('pt_partial_2_date','')}")
    if t.get("pt_trailing_stop"):
        parts.append(f"&#x1F4C9; Trail ${t['pt_trailing_stop']:.2f}")
    return "<br>".join(parts) if parts else "–"


def build_html(data: dict) -> str:
    stats   = _stats(data)
    today   = datetime.date.today().isoformat()
    open_t  = data.get("open",   [])
    closed_t = data.get("closed", [])

    # ── Summary bar ──────────────────────────────────────────────────────────
    wr_str  = f"{stats['win_rate']:.0f}%" if stats["win_rate"] is not None else "–"
    pl_str  = _fmt_money(stats["total_pl"])
    aw_str  = _fmt_pct(stats["avg_win"])
    al_str  = _fmt_pct(stats["avg_loss"])

    # ── Open positions rows ───────────────────────────────────────────────────
    open_rows = ""
    for t in open_t:
        plpc       = t.get("unrealized_plpc")
        pl         = t.get("unrealized_pl")
        stop_raised = "↑ " if t.get("stop_raised_date") else ""
        cur_stop   = t.get("current_stop")
        init_stop  = t.get("initial_stop")
        init_stop_str = f'{init_stop:.2f}' if init_stop is not None else '–'
        cur_stop_str  = f'{cur_stop:.2f}'  if cur_stop  is not None else '–'
        stop_style    = 'color:#1a8a1a;font-weight:600' if t.get('stop_raised_date') else ''
        rs_val = t.get('rs_score')
        rs_str = f'{rs_val:.0f}' if rs_val is not None else '–'
        open_rows += f"""
        <tr>
          <td class="left"><strong>{t.get('symbol','')}</strong><br>
            <span style="font-size:.8em;color:#666">{t.get('company','')}</span></td>
          <td class="left" style="font-size:.85em">{t.get('sector','–') or '–'}</td>
          <td class="left">{t.get('pattern','–')}</td>
          <td>{t.get('entry_date','–')}</td>
          <td>{(t.get('entry_price') or 0):.2f}</td>
          <td>{(t.get('qty') or 0):.0f}</td>
          <td style="text-align:center">{rs_str}</td>
          <td>{init_stop_str}</td>
          <td style="{stop_style}">{stop_raised}{cur_stop_str}</td>
          <td>{(t.get('current_price') or 0):.2f}</td>
          <td style="{_color(plpc)}">{_fmt_pct(plpc)}</td>
          <td style="{_color(pl)}">{_fmt_money(pl)}</td>
          <td class="left" style="font-size:.8em">{_pt_status(t)}</td>
        </tr>"""

    if not open_rows:
        open_rows = '<tr><td colspan="13" style="text-align:center;color:#999">Keine offenen Positionen</td></tr>'

    # ── Closed trades rows ────────────────────────────────────────────────────
    closed_rows = ""
    for t in closed_t:
        plpc = t.get("realized_plpc")
        pl   = t.get("realized_pl")
        rs_val = t.get('rs_score')
        rs_str = f'{rs_val:.0f}' if rs_val is not None else '–'
        closed_rows += f"""
        <tr>
          <td class="left"><strong>{t.get('symbol','')}</strong><br>
            <span style="font-size:.8em;color:#666">{t.get('company','')}</span></td>
          <td class="left" style="font-size:.85em">{t.get('sector','–') or '–'}</td>
          <td class="left">{t.get('pattern','–')}</td>
          <td>{t.get('entry_date','–')}</td>
          <td>{t.get('exit_date','–')}</td>
          <td>{(t.get('entry_price') or 0):.2f}</td>
          <td>{(t.get('exit_price') or 0):.2f}</td>
          <td>{(t.get('qty') or 0):.0f}</td>
          <td style="text-align:center">{rs_str}</td>
          <td class="left">{_exit_reason_label(t.get('exit_reason'))}</td>
          <td style="{_color(plpc)}">{_fmt_pct(plpc)}</td>
          <td style="{_color(pl)}">{_fmt_money(pl)}</td>
        </tr>"""

    if not closed_rows:
        closed_rows = '<tr><td colspan="12" style="text-align:center;color:#999">Noch keine abgeschlossenen Trades</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Tradetagebuch</title>
  <style>
    body      {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 2em auto; padding: 0 1em; color: #333; }}
    h1        {{ color: #003d99; margin-bottom: .2em; }}
    h2        {{ color: #003d99; margin-top: 2em; border-bottom: 2px solid #003d99; padding-bottom: .2em; }}
    .meta     {{ color: #888; font-size: .85em; margin-bottom: 1.5em; }}
    .summary  {{ display: flex; gap: 1.5em; flex-wrap: wrap; margin-bottom: 2em; }}
    .kpi      {{ background: #f5f7fa; border: 1px solid #dde; border-radius: 8px;
                 padding: 12px 20px; min-width: 120px; text-align: center; }}
    .kpi .val {{ font-size: 1.5em; font-weight: bold; color: #003d99; }}
    .kpi .lbl {{ font-size: .8em; color: #888; margin-top: 2px; }}
    table     {{ border-collapse: collapse; width: 100%; margin-bottom: 1em; font-size: .9em; }}
    th, td    {{ border: 1px solid #dde; padding: .4em .7em; text-align: right; white-space: nowrap; }}
    th        {{ background: #f0f3fa; color: #003d99; font-weight: 600; }}
    td.left   {{ text-align: left; }}
    tr:hover  {{ background: #f8f9ff; }}
    .back     {{ display:inline-block; margin-bottom:1.5em; color:#003d99; text-decoration:none; font-size:.9em; }}
    .back:hover {{ text-decoration:underline; }}
    .section-header {{ display:flex; align-items:center; justify-content:space-between; margin-top:2em; }}
    .section-header h2 {{ margin:0; border-bottom:none; flex:1; }}
    .section-header::after {{ content:""; display:block; height:2px; background:#003d99; margin-top:.2em; }}
    .dl-btn {{ display:inline-flex; align-items:center; gap:.4em; padding:.35em .9em;
               background:#003d99; color:#fff; border:none; border-radius:5px; cursor:pointer;
               font-size:.82em; font-weight:600; text-decoration:none; white-space:nowrap; }}
    .dl-btn:hover {{ background:#0055cc; }}
    .tbl-wrap {{ border-top:2px solid #003d99; padding-top:.5em; }}
  </style>
  <script src="https://cdn.sheetjs.com/xlsx-latest/package/dist/xlsx.full.min.js"></script>
  <script>
    function exportTable(tableId, filename) {{
      var tbl = document.getElementById(tableId);
      var wb  = XLSX.utils.book_new();
      // Convert HTML table → worksheet; raw:false keeps text as-is
      var ws  = XLSX.utils.table_to_sheet(tbl, {{raw: false}});
      XLSX.utils.book_append_sheet(wb, ws, "Trades");
      XLSX.writeFile(wb, filename);
    }}
  </script>
</head>
<body>
  <a href="../index.html" class="back">← Zurück zur Übersicht</a>
  <h1>Tradetagebuch</h1>
  <p class="meta">Stand: {today} &nbsp;|&nbsp; {stats['count']} abgeschlossene Trades &nbsp;|&nbsp; {len(open_t)} offene Position{'en' if len(open_t) != 1 else ''}</p>

  <div class="summary">
    <div class="kpi"><div class="val">{stats['wins']}/{stats['count']}</div><div class="lbl">Wins / Gesamt</div></div>
    <div class="kpi"><div class="val">{wr_str}</div><div class="lbl">Win Rate</div></div>
    <div class="kpi"><div class="val" style="{'color:#1a8a1a' if stats['total_pl'] > 0 else 'color:#cc2222'}">{pl_str} $</div><div class="lbl">Realized P&amp;L</div></div>
    <div class="kpi"><div class="val" style="color:#1a8a1a">{aw_str}</div><div class="lbl">Ø Gewinner</div></div>
    <div class="kpi"><div class="val" style="color:#cc2222">{al_str}</div><div class="lbl">Ø Verlierer</div></div>
  </div>

  <div class="section-header">
    <h2>Offene Positionen</h2>
    <button class="dl-btn" onclick="exportTable('tbl-open','offene_positionen_{today}.xlsx')">&#8595; Excel</button>
  </div>
  <div class="tbl-wrap">
  <table id="tbl-open">
    <tr>
      <th class="left">Ticker</th>
      <th class="left">Sektor</th>
      <th class="left">Pattern</th>
      <th>Entry-Datum</th>
      <th>Entry-Preis</th>
      <th>Qty</th>
      <th>RS@Entry</th>
      <th>Initial Stop</th>
      <th>Aktueller Stop</th>
      <th>Kurs aktuell</th>
      <th>P&amp;L %</th>
      <th>P&amp;L $</th>
      <th class="left">Gewinnmitnahme</th>
    </tr>
    {open_rows}
  </table>
  </div>

  <div class="section-header">
    <h2>Abgeschlossene Trades</h2>
    <button class="dl-btn" onclick="exportTable('tbl-closed','abgeschlossene_trades_{today}.xlsx')">&#8595; Excel</button>
  </div>
  <div class="tbl-wrap">
  <table id="tbl-closed">
    <tr>
      <th class="left">Ticker</th>
      <th class="left">Sektor</th>
      <th class="left">Pattern</th>
      <th>Entry-Datum</th>
      <th>Exit-Datum</th>
      <th>Entry-Preis</th>
      <th>Exit-Preis</th>
      <th>Qty</th>
      <th>RS@Entry</th>
      <th class="left">Exit-Grund</th>
      <th>P&amp;L %</th>
      <th>P&amp;L $</th>
    </tr>
    {closed_rows}
  </table>
  </div>
</body>
</html>"""


def build_and_save_html(data: dict) -> Path:
    TRADES_HTML.parent.mkdir(parents=True, exist_ok=True)
    TRADES_HTML.write_text(build_html(data), encoding="utf-8")
    return TRADES_HTML
