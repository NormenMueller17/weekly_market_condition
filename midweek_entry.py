"""Mid-Week-Entry: prueft mittwochs die Watchlist des letzten Wochenlaufs.

  python midweek_entry.py                # Live (platziert Orders)
  python midweek_entry.py --dry-run      # nur pruefen, nichts platzieren

Warum es das gibt
-----------------
Der Wochentakt kostet Ausbrueche, die mitten in der Woche starten. Fuer echte
Signale ist das unkritisch: deren Buy-Stop-Orders liegen seit Samstag als GTC im
Markt und feuern von allein. Die Luecke sind die Titel, die die THESE erfuellen
(Trendstruktur, relative Staerke, Fundamentaldaten, Industrie), am Samstag aber
noch keinen bestaetigten Ausbruch hatten — fuer die existiert keine Order, und
der naechste Lauf ist erst wieder am Samstag.

Genau diese Menge schreibt der Wochenlauf nach `docs/data/midweek_watchlist.json`
(signal_generator.build_midweek_watchlist). Dieses Skript prueft nur sie:

  1. Watchlist laden — zu alt oder fehlend ist ein FEHLER, kein Leerlauf
  2. Marktfilter: SPY ueber seiner 50-Tage-Linie (Tagesbasis, nicht Teilwoche)
  3. Wochenbudget: bereits gekaufte Titel dieser Kalenderwoche abziehen
  4. Je Titel: Pivot genommen UND Tagesvolumen ueber dem 20-Tage-Schnitt
  5. Order platzieren (Markt ist mittwochs offen — kein Umweg ueber Montag)

Kein zweiter Universumslauf, keine Neuberechnung der Marktampel, keine
Teilwochen-Aggregate. Der Wochenlauf hat die These geprueft; hier geht es
ausschliesslich um das Timing.

Stille ist ein Ergebnis, kein Ausfall: Ohne Treffer wird keine Mail verschickt.
Damit ein ABGESTUERZTER Lauf nicht genauso aussieht wie einer ohne Treffer,
endet jeder Fehlerpfad mit Exit-Code != 0 und einer Fehlermail.

Warum spaet am Tag
------------------
Der Volumentest vergleicht das Tagesvolumen mit dem 20-Tage-Schnitt. Frueh in
der Sitzung ist das Tagesvolumen naturgemaess klein, der Test schluege also
praktisch nie an. Der Lauf liegt deshalb kurz vor Handelsschluss (19:30 UTC =
15:30 ET). Die Buy-Stop-Order liegt 0,1 % ueber dem aktuellen Kurs und ist GTC:
sie fuellt noch am selben Tag, wenn der Titel weiterlaeuft, sonst am naechsten —
und faellt spaetestens beim naechsten Wochenlauf dem `cancel_open_orders()` zum
Opfer, der dann ohnehin neu bewertet.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import yfinance as yf

import alpaca_client
from config import SETTINGS
from emailer import send_email
from signal_generator import (
    _DEFAULT_PORTFOLIO_MAX_POSITIONS,
    DEFAULT_RULES,
    TradeSignal,
    _stop_default,
    apply_stop_bounds,
    size_position,
)

WATCHLIST_PATH = Path("docs") / "data" / "midweek_watchlist.json"

# Aelter als das, und die Watchlist beschreibt eine Marktlage, die es nicht
# mehr gibt. Sieben Tage decken den Normalfall (Samstag -> Mittwoch = 4 Tage)
# plus einen ausgefallenen Wochenlauf ab; danach wird abgebrochen.
MAX_WATCHLIST_AGE_DAYS = 7

# Marktfilter auf Tagesbasis. 50 Handelstage ~ 10 Wochen — dieselbe Linie wie
# das Ampelkriterium "SPY ueber 10W MA", nur ohne Teilwochen-Aggregat.
MARKET_MA_DAYS = 50


class MidweekError(RuntimeError):
    """Abbruchgrund, der als Fehlermail rausgeht."""


# ── Watchlist ─────────────────────────────────────────────────────────────────

def load_watchlist(path: Path = WATCHLIST_PATH, today: date | None = None) -> dict:
    if not path.exists():
        raise MidweekError(
            f"{path} fehlt. Der Wochenlauf schreibt die Datei; ohne sie hat der "
            f"Mid-Week-Check keine Grundlage."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise MidweekError(f"{path} ist nicht lesbar: {e}") from e

    generated = data.get("generated")
    if not generated:
        raise MidweekError(f"{path} hat kein Feld 'generated'.")
    try:
        gen_date = date.fromisoformat(generated)
    except ValueError as e:
        raise MidweekError(f"{path}: 'generated' = {generated!r} ist kein Datum.") from e

    age = ((today or date.today()) - gen_date).days
    if age > MAX_WATCHLIST_AGE_DAYS:
        raise MidweekError(
            f"Watchlist ist {age} Tage alt (vom {generated}, Grenze "
            f"{MAX_WATCHLIST_AGE_DAYS}). Der letzte Wochenlauf ist vermutlich "
            f"ausgefallen — es wird nichts gekauft."
        )
    if age < 0:
        raise MidweekError(f"Watchlist ist auf {generated} datiert und damit in der Zukunft.")
    return data


# ── Marktfilter ───────────────────────────────────────────────────────────────

def market_ok() -> tuple[bool, str]:
    """SPY ueber seiner 50-Tage-Linie? Fail-closed bei Datenausfall."""
    df = yf.download("SPY", period="6mo", interval="1d",
                     auto_adjust=False, progress=False, threads=False)
    if df is None or df.empty:
        raise MidweekError("SPY-Kurse nicht abrufbar — Marktfilter nicht prüfbar.")
    close = pd.to_numeric(_series(df, "Close"), errors="coerce").dropna()
    if len(close) < MARKET_MA_DAYS:
        raise MidweekError(
            f"SPY-Historie zu kurz ({len(close)} Tage) für die "
            f"{MARKET_MA_DAYS}-Tage-Linie."
        )
    ma   = float(close.rolling(MARKET_MA_DAYS).mean().iloc[-1])
    last = float(close.iloc[-1])
    pct  = (last / ma - 1.0) * 100.0
    return last >= ma, f"SPY {last:.2f} vs {MARKET_MA_DAYS}d-MA {ma:.2f} ({pct:+.2f} %)"


def _series(df: pd.DataFrame, col: str) -> pd.Series:
    """Spalte aus einem yfinance-Frame ziehen, egal ob MultiIndex oder flach."""
    if isinstance(df.columns, pd.MultiIndex):
        sub = df.xs(col, axis=1, level=0)
        return sub.iloc[:, 0] if isinstance(sub, pd.DataFrame) else sub
    return df[col]


# ── Wochenbudget ──────────────────────────────────────────────────────────────

def buys_this_week(today: date | None = None) -> int:
    """Anzahl in dieser Kalenderwoche gefuellter Kauforders.

    Der Wochenlauf deckelt Neukaeufe ueber `max_new_per_week_bull`, wertet den
    Deckel aber pro LAUF aus. Ohne diesen Abgleich haette die Woche mit einem
    zweiten Lauf schlicht das doppelte Budget.
    """
    today  = today or date.today()
    monday = today - timedelta(days=today.weekday())
    try:
        filled = alpaca_client.get_filled_orders(side="buy", days_back=14)
    except Exception as e:
        raise MidweekError(f"Kauforders nicht abrufbar, Wochenbudget unbekannt: {e}") from e

    n = 0
    for o in filled:
        raw = o.get("filled_at")
        if not raw:
            continue
        try:
            d = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if d >= monday:
            n += 1
    return n


# ── Ausbruchspruefung ─────────────────────────────────────────────────────────

def check_breakout(ticker: str, buy_stop: float, r: dict) -> dict | None:
    """Hat der Titel heute den Pivot mit Volumen genommen?

    Beides muss zusammenkommen — ein Pivot ohne Volumen ist genau der Ausbruch,
    der wieder zurueckfaellt. Der Volumenschwellwert ist derselbe wie im
    Wochenlauf (`volume_breakout_score`), nur auf den laufenden Tag statt auf
    den Wochendurchschnitt angewandt.
    """
    df = yf.download(ticker, period="6mo", interval="1d",
                     auto_adjust=False, progress=False, threads=False)
    if df is None or df.empty:
        print(f"[MIDWEEK]    {ticker}: keine Kursdaten — uebersprungen")
        return None

    high   = pd.to_numeric(_series(df, "High"),   errors="coerce").dropna()
    close  = pd.to_numeric(_series(df, "Close"),  errors="coerce").dropna()
    volume = pd.to_numeric(_series(df, "Volume"), errors="coerce").dropna()
    if len(close) < 21 or high.empty or volume.empty:
        print(f"[MIDWEEK]    {ticker}: Historie zu kurz — uebersprungen")
        return None

    day_high = float(high.iloc[-1])
    day_close = float(close.iloc[-1])
    day_vol  = float(volume.iloc[-1])
    vol20    = float(volume.rolling(20).mean().iloc[-2])   # ohne den laufenden Tag
    vol_mult = r.get("volume_breakout_score", 1.3)

    pivot_taken = day_high >= buy_stop
    vol_ok      = vol20 > 0 and day_vol >= vol20 * vol_mult

    # ATR% auf Tagesbasis — dieselbe Groesse, die der Wochenlauf fuer den Stop
    # nutzt, hier aus den aktuellen Kursen statt aus der Watchlist.
    low = pd.to_numeric(_series(df, "Low"), errors="coerce").reindex(close.index)
    prev_close = close.shift(1)
    tr = pd.concat([
        high.reindex(close.index) - low,
        (high.reindex(close.index) - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])
    atr_pct = atr14 / day_close * 100.0 if day_close else float("nan")

    status = (f"Hoch {day_high:.2f} vs Buy-Stop {buy_stop:.2f}, "
              f"Volumen {day_vol / vol20:.2f}x" if vol20 > 0 else "Volumen unbekannt")
    if not (pivot_taken and vol_ok):
        print(f"[MIDWEEK]    {ticker}: kein Ausbruch — {status}")
        return None

    print(f"[MIDWEEK] ✅ {ticker}: Ausbruch — {status}")
    return {"entry": day_close, "atr_pct": atr_pct,
            "day_high": day_high, "vol_ratio": day_vol / vol20}


# ── Signal bauen ──────────────────────────────────────────────────────────────

def build_signal(item: dict, breakout: dict, equity: float,
                 market_bullish: bool, r: dict) -> TradeSignal:
    """Baut ein TradeSignal aus Watchlist-Eintrag und Live-Ausbruch.

    Der Stop laeuft bewusst ueber den Default-Pfad (`_stop_default` + Floor +
    Cap) statt ueber die Muster-Geometrie: Pivot und Box stammen aus
    Wochenbalken, und mitten in der Woche gibt es keinen abgeschlossenen
    Wochenbalken, aus dem sie sich sauber ableiten liessen. Praktisch macht das
    wenig aus — laut apply_stop_bounds traegt der Floor ohnehin die Arbeit.
    """
    entry   = float(breakout["entry"])
    atr_pct = float(breakout["atr_pct"]) if breakout["atr_pct"] == breakout["atr_pct"] else 5.0

    stop = _stop_default(entry, atr_pct, mult=r["stop_atr_mult"],
                         min_pct=r["min_stop_pct"] / 100.0)
    stop, stop_pct = apply_stop_bounds(entry, stop, atr_pct, r)

    pos_pct, pos_value = size_position(stop_pct, equity, market_bullish, r)

    pivot     = float(item["pivot"])
    buf       = 1.0 + r.get("buy_stop_buffer_pct", 0.1) / 100.0
    gap_limit = 1.0 + r.get("gap_limit_pct", 5.0) / 100.0

    return TradeSignal(
        ticker            = item["ticker"],
        company           = item.get("company", ""),
        industry          = item.get("industry", ""),
        sector            = item.get("sector", ""),
        entry_price       = round(entry, 2),
        buy_stop          = round(max(entry, pivot) * buf, 2),
        max_gap_price     = round(max(entry, pivot) * gap_limit, 2),
        stop_loss         = round(stop, 2),
        stop_loss_pct     = round(stop_pct, 4),
        pattern           = "Mid-Week",
        breakout_level    = round(pivot, 2),
        roe               = None,
        op_margin         = None,
        revenue_growth    = None,
        eps_growth_last_q = None,
        rs_score          = item.get("rs_score"),
        rs_delta_4w       = None,
        atr_pct           = round(atr_pct, 2),
        dist_52w_high_pct = None,
        vol_score         = round(breakout["vol_ratio"], 2),
        position_size_pct = round(pos_pct, 4),
        position_value    = round(pos_value, 2),
        risk_value        = round(pos_value * stop_pct, 2),
        risk_on_equity_pct= round(pos_value * stop_pct / equity, 4) if equity else 0.0,
        industry_ranking  = item.get("industry_ranking"),
        market_regime     = "bullish" if market_bullish else "bearish",
        sa_link           = item.get("sa_link", ""),
        rank              = 0,
        is_top_pick       = True,   # nur Treffer kommen bis hierher
        signal_date       = date.today().isoformat(),
    )


# ── Mail ──────────────────────────────────────────────────────────────────────

def _rows(signals: list[TradeSignal], results: list[dict]) -> str:
    by_ticker = {r["ticker"]: r for r in results}
    out = []
    for s in signals:
        res = by_ticker.get(s.ticker, {})
        out.append(
            "<tr>"
            f"<td class='left'><b>{s.ticker}</b></td>"
            f"<td class='left'>{s.company}</td>"
            f"<td class='left'>{s.industry}</td>"
            f"<td>{s.entry_price:.2f}</td>"
            f"<td>{s.buy_stop:.2f}</td>"
            f"<td>{s.stop_loss:.2f}</td>"
            f"<td>{s.stop_loss_pct * 100:.1f} %</td>"
            f"<td>{s.position_value:,.0f}</td>"
            f"<td>{s.vol_score:.2f}x</td>"
            f"<td class='left'>{res.get('status', 'n/a')}</td>"
            "</tr>"
        )
    return "\n".join(out)


def build_mail(signals: list[TradeSignal], results: list[dict],
               market_note: str, dry_run: bool) -> str:
    banner = ("<p style='background:#fff3cd;border:1px solid #ffeeba;padding:.6em;'>"
              "⚠️ TEST-MODUS — keine Orders platziert</p>") if dry_run else ""
    return f"""
<div style="font-family:Arial,sans-serif;max-width:900px;margin:0 auto;padding:1em;">
  <h2 style="color:#003d99;">Mid-Week-Entry — {date.today().isoformat()}</h2>
  {banner}
  <p>{len(signals)} Titel der Wochen-Watchlist hat den Pivot mit Volumen genommen.</p>
  <table style="border-collapse:collapse;font-size:.93em;">
    <tr>
      <th>Ticker</th><th>Unternehmen</th><th>Industry</th><th>Kurs</th>
      <th>Buy-Stop</th><th>Stop</th><th>Stop&nbsp;%</th><th>Position&nbsp;$</th>
      <th>Volumen</th><th>Order</th>
    </tr>
    {_rows(signals, results)}
  </table>
  <p style="color:#555;font-size:.9em;">{market_note}</p>
  <style>
    th,td {{ border:1px solid #d0d5e8; padding:.45em .9em; text-align:right; }}
    th {{ background:#eef2fa; color:#003d99; }}
    .left {{ text-align:left; }}
  </style>
</div>
"""


def send_error_mail(msg: str) -> None:
    body = (f"<div style='font-family:Arial,sans-serif;'>"
            f"<h2 style='color:#721c24;'>Mid-Week-Entry fehlgeschlagen</h2>"
            f"<pre style='background:#f8f8f8;padding:1em;white-space:pre-wrap;'>{msg}</pre>"
            f"<p>Es wurde nichts gekauft.</p></div>")
    try:
        send_email(body, subject_suffix="Mid-Week-Entry FEHLGESCHLAGEN")
    except Exception as e:
        print(f"[MIDWEEK] Fehlermail konnte nicht verschickt werden: {e}")


# ── Ablauf ────────────────────────────────────────────────────────────────────

def run(dry_run: bool) -> int:
    r = dict(DEFAULT_RULES)

    data  = load_watchlist()
    items = data.get("watchlist", [])
    print(f"[MIDWEEK] Watchlist vom {data['generated']}: {len(items)} Titel")
    if not items:
        print("[MIDWEEK] Watchlist leer — nichts zu pruefen.")
        return 0

    ok, note = market_ok()
    print(f"[MIDWEEK] Marktfilter: {note}")
    if not ok:
        print("[MIDWEEK] SPY unter der 50-Tage-Linie — keine Neukaeufe.")
        return 0

    portfolio = alpaca_client.get_portfolio()
    if portfolio is None:
        raise MidweekError("Alpaca-Portfolio nicht abrufbar — Bestand und Equity unbekannt.")
    held   = {p["symbol"] for p in portfolio["positions"]}
    equity = float(portfolio["equity"]) or SETTINGS.account_equity

    budget = r.get("max_new_per_week_bull", 3) - buys_this_week()
    free_slots = _DEFAULT_PORTFOLIO_MAX_POSITIONS - len(held)
    allowed = max(0, min(budget, free_slots))
    print(f"[MIDWEEK] Wochenbudget uebrig: {budget} — freie Depotplaetze: {free_slots} "
          f"→ maximal {allowed} Kauf(e)")
    if allowed <= 0:
        print("[MIDWEEK] Kein Budget oder kein Platz — keine Neukaeufe.")
        return 0

    signals: list[TradeSignal] = []
    for item in items:
        if len(signals) >= allowed:
            print("[MIDWEEK] Budget ausgeschoepft — Rest der Watchlist nicht geprueft.")
            break
        ticker = item["ticker"]
        if ticker in held:
            print(f"[MIDWEEK]    {ticker}: bereits im Depot — uebersprungen")
            continue
        breakout = check_breakout(ticker, float(item["buy_stop"]), r)
        if breakout:
            signals.append(build_signal(item, breakout,
                                        equity, bool(data.get("market_bullish", True)), r))

    if not signals:
        print("[MIDWEEK] Kein Ausbruch — keine Mail, keine Order.")
        return 0

    results = alpaca_client.place_signal_orders(signals, dry_run=dry_run)
    for res in results:
        print(f"[MIDWEEK] Order {res['ticker']}: {res['status']}")

    send_email(build_mail(signals, results, note, dry_run),
               subject_suffix=f"Mid-Week-Entry — {len(signals)} Ausbruch"
                              f"{'' if len(signals) == 1 else 'e'}")
    print(f"[MIDWEEK] Mail verschickt ({len(signals)} Treffer).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Mid-Week-Entry auf der Wochen-Watchlist")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur pruefen, keine Orders platzieren")
    args = ap.parse_args()

    try:
        return run(dry_run=args.dry_run)
    except MidweekError as e:
        print(f"[MIDWEEK] ❌ {e}")
        send_error_mail(str(e))
        return 1
    except Exception:
        tb = traceback.format_exc()
        print(f"[MIDWEEK] ❌ Unerwarteter Fehler:\n{tb}")
        send_error_mail(tb)
        return 1


if __name__ == "__main__":
    sys.exit(main())
