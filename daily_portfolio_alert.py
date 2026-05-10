"""
daily_portfolio_alert.py — Täglicher Portfolio-Alert (Mo–Fr).

Lädt für alle offenen Positionen den aktuellen Tageskurs und prüft:
  1. Stop-Loss   — Kurs unter 50-Tage-MA (+ MACD-Kreuz als Bestätigung)
  2. Take-Profit — Schein-Gewinn-Proxy ≥ 40 % oder ≥ 60 %
  3. Roll        — Zeitwert (Restlaufzeit < 6 Monate) oder Strike (Hebel < 2×)

E-Mail wird nur versendet wenn mindestens ein Signal aktiv ist.
Kein Report, kein GitHub-Pages-Commit — nur Alert-Mail.

Aufruf:
    python daily_portfolio_alert.py            # normaler Lauf
    python daily_portfolio_alert.py --dry-run  # gibt Signale aus, kein Mailversand
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from config import SETTINGS
from emailer import send_email
from zertifikate.portfolio import load_portfolio

_RULES_PATH = ROOT / "zertifikate" / "rules.json"

# ── Schwellenwerte (Fallbacks; werden aus rules.json gelesen) ─────────────────
_MA_DAYS        = 50
_TP_HALB        = 40.0
_TP_EMPFOHLEN   = 60.0
_HEBEL_MIN      = 2.0
_ZEIT_ROLL_M    = 6
_VERLUST_WARN   = -30.0


def run(dry_run: bool = False) -> None:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  Portfolio-Alert  —  {today}")
    print(f"{'='*55}\n")

    rules    = _load_rules()
    r        = rules.get("rollen", {})
    tp_halb  = float(r.get("tp_halb_pct",         _TP_HALB))
    tp_empf  = float(r.get("tp_empfohlen_pct",     _TP_EMPFOHLEN))
    hebel_mn = float(r.get("hebel_min",            _HEBEL_MIN))
    zeit_m   = float(r.get("zeitwert_min_restlaufzeit_monate", _ZEIT_ROLL_M))

    portfolio = load_portfolio()
    positionen = portfolio.get("open", [])

    if not positionen:
        print("Keine offenen Positionen — kein Alert nötig.")
        return

    tickers = [p["basiswert"] for p in positionen if p.get("basiswert")]
    print(f"Lade Tagesdaten für {len(tickers)} Titel: {', '.join(tickers)}")
    daily_data = _fetch_daily(tickers)

    signale: list[dict] = []
    for pos in positionen:
        ticker = pos.get("basiswert", "")
        df     = daily_data.get(ticker)
        sig    = _check_position(pos, df, tp_halb, tp_empf, hebel_mn, zeit_m)
        if sig["hat_signal"]:
            signale.append(sig)
            print(f"  [{ticker}] {', '.join(sig['texte'])}")
        else:
            print(f"  [{ticker}] kein Signal")

    if not signale:
        print("\nKeine aktiven Signale — kein E-Mail-Versand.")
        return

    html  = _build_alert_html(signale, today)
    n     = len(signale)
    tix   = ", ".join(s["ticker"] for s in signale)
    subj  = f"Portfolio-Alert {today} — {n} Signal{'e' if n > 1 else ''}: {tix}"

    if dry_run:
        print(f"\n[DRY-RUN] {n} Signal(e) — kein Versand.")
        print(f"[DRY-RUN] Betreff: {subj}")
        return

    try:
        send_email(html_body=html, subject_suffix=subj)
        print(f"\n[MAIL] Alert gesendet: {subj}")
    except Exception as exc:
        print(f"[MAIL][WARN] Versand fehlgeschlagen: {exc}")
        sys.exit(1)


# ── Datenabruf ────────────────────────────────────────────────────────────────

def _fetch_daily(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Lädt Tagesdaten (1 Jahr) für alle übergebenen Ticker."""
    if not tickers:
        return {}
    try:
        raw = yf.download(
            tickers=" ".join(tickers),
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
        )
        if raw.empty:
            return {}

        result: dict[str, pd.DataFrame] = {}
        if isinstance(raw.columns, pd.MultiIndex):
            for t in tickers:
                found = next(
                    (c for c in raw.columns.get_level_values(0).unique()
                     if c.upper() == t.upper()), None
                )
                if found is not None and not raw[found].empty:
                    result[t] = raw[found].dropna(how="all")
        else:
            # Einzelner Ticker — raw ist direkt der DataFrame
            if len(tickers) == 1:
                result[tickers[0]] = raw.dropna(how="all")
        return result
    except Exception as exc:
        print(f"[DAILY] Datenabruf fehlgeschlagen: {exc}")
        return {}


# ── Signal-Prüfung ────────────────────────────────────────────────────────────

def _check_position(
    pos: dict,
    df: pd.DataFrame | None,
    tp_halb: float,
    tp_empf: float,
    hebel_min: float,
    zeit_roll_m: float,
) -> dict:
    ticker = pos.get("basiswert", "?")
    texte:  list[str] = []
    level:  str       = "info"   # info | warn | kritisch

    current_close = None
    if df is not None and not df.empty:
        close_col = next((c for c in df.columns if c.lower() == "close"), None)
        if close_col:
            current_close = float(df[close_col].dropna().iloc[-1])

    # ── Stop-Loss ─────────────────────────────────────────────────────────────
    if current_close is not None and df is not None:
        close_col = next((c for c in df.columns if c.lower() == "close"), None)
        if close_col:
            closes = df[close_col].dropna()
            ma50   = float(closes.rolling(_MA_DAYS).mean().iloc[-1]) if len(closes) >= _MA_DAYS else None

            if ma50 and current_close < ma50:
                # MACD als Bestätigung (12/26 Tage)
                macd_confirm = _macd_bearish(closes)
                if macd_confirm:
                    texte.append(f"STOP-LOSS: Kurs {current_close:.2f} unter MA50 {ma50:.2f} + MACD bearish")
                    level = "kritisch"
                else:
                    texte.append(f"Unter MA50 ({current_close:.2f} < {ma50:.2f}) — Stopp beobachten")
                    level = _upgrade(level, "warn")

    # ── Take-Profit ───────────────────────────────────────────────────────────
    kauf_bw = pos.get("kauf_kurs_basiswert")
    hebel   = pos.get("hebel_kauf")
    if kauf_bw and hebel and current_close:
        basis_perf   = (current_close / float(kauf_bw) - 1) * 100
        schein_gew   = basis_perf * float(hebel)
        if schein_gew >= tp_empf:
            texte.append(f"TEILVERKAUF empfohlen: Schein ~+{schein_gew:.0f}% est. (Basis +{basis_perf:.1f}%)")
            level = _upgrade(level, "warn")
        elif schein_gew >= tp_halb:
            texte.append(f"Hälfte nehmen erwägen: Schein ~+{schein_gew:.0f}% est. (Basis +{basis_perf:.1f}%)")
            level = _upgrade(level, "warn")
        elif schein_gew <= _VERLUST_WARN:
            texte.append(f"Verlust-Warnung: Schein ~{schein_gew:.0f}% est. (Basis {basis_perf:.1f}%)")
            level = _upgrade(level, "warn")

    # ── Roll-Signal ───────────────────────────────────────────────────────────
    faelligkeit = pos.get("faelligkeitsdatum", "")
    if faelligkeit:
        try:
            faell_date  = datetime.strptime(faelligkeit, "%Y-%m-%d").date()
            rest_monate = (faell_date - date.today()).days / 30.44
            if rest_monate < zeit_roll_m:
                dring = "DRINGEND" if rest_monate < 3 else "prüfen"
                texte.append(f"ZEITWERT-ROLL {dring}: {rest_monate:.1f} Monate Restlaufzeit")
                level = _upgrade(level, "kritisch" if rest_monate < 3 else "warn")
        except ValueError:
            pass

    if kauf_bw and hebel and current_close and float(current_close) > 0:
        hebel_est = float(kauf_bw) * float(hebel) / float(current_close)
        if hebel_est < hebel_min:
            texte.append(f"STRIKE-ROLL: Hebel est. {hebel_est:.1f}× (unter Minimum {hebel_min}×)")
            level = _upgrade(level, "warn")

    return {
        "ticker":      ticker,
        "company":     pos.get("company", ticker),
        "hat_signal":  bool(texte),
        "level":       level,
        "texte":       texte,
        "kurs":        round(current_close, 2) if current_close else None,
        "schein_name": pos.get("schein_name", "—"),
        "kauf_datum":  pos.get("kauf_datum", "—"),
    }


def _macd_bearish(closes: pd.Series) -> bool:
    """Einfacher MACD-Bearish-Check: Linie kreuzt Signal von oben nach unten."""
    if len(closes) < 27:
        return False
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    if len(macd) < 2:
        return False
    return (float(macd.iloc[-1]) < float(sig.iloc[-1])
            and float(macd.iloc[-2]) >= float(sig.iloc[-2]))


def _upgrade(current: str, new: str) -> str:
    """Eskaliert level nur aufwärts: info < warn < kritisch."""
    order = {"info": 0, "warn": 1, "kritisch": 2}
    return new if order.get(new, 0) > order.get(current, 0) else current


# ── HTML-Alert ────────────────────────────────────────────────────────────────

def _build_alert_html(signale: list[dict], today: str) -> str:
    _LEVEL_COLOR = {
        "kritisch": ("#fadbd8", "#e74c3c", "#922b21"),
        "warn":     ("#fef9e7", "#f39c12", "#9a7d0a"),
        "info":     ("#eaf4fb", "#2980b9", "#1a5276"),
    }

    cards = ""
    for s in signale:
        bg, border, text = _LEVEL_COLOR.get(s["level"], _LEVEL_COLOR["info"])
        items = "".join(f"<li style='margin:.3em 0'>{t}</li>" for t in s["texte"])
        cards += f"""
<div style="background:{bg};border-left:5px solid {border};border-radius:6px;
            padding:14px 18px;margin-bottom:12px">
  <div style="font-size:1.05em;font-weight:700;color:{text};margin-bottom:6px">
    {s['ticker']} &nbsp;<span style="font-weight:400;font-size:0.88em">{s['company']}</span>
  </div>
  <div style="font-size:0.82em;color:#555;margin-bottom:8px">
    Schein: {s['schein_name']} &nbsp;|&nbsp; Kauf: {s['kauf_datum']}
    {f"&nbsp;|&nbsp; Kurs heute: {s['kurs']}" if s['kurs'] else ""}
  </div>
  <ul style="margin:0;padding-left:1.2em;color:{text};font-size:0.9em">{items}</ul>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio-Alert {today}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             background:#f5f6fa;color:#2c3e50;font-size:14px;margin:0;padding:20px">
  <div style="max-width:640px;margin:0 auto">
    <div style="background:#2c3e50;color:#fff;padding:16px 20px;border-radius:8px;margin-bottom:16px">
      <div style="font-size:1.2em;font-weight:700">⚠️ Portfolio-Alert</div>
      <div style="opacity:.8;font-size:.88em">{today} — {len(signale)} Position(en) mit Handlungsbedarf</div>
    </div>
    {cards}
    <div style="margin-top:16px;font-size:0.78em;color:#95a5a6;text-align:center">
      Täglicher Portfolio-Check — Zertifikate-Scanner<br>
      Schein-Gewinn-Angaben sind Schätzungen (Basiswert × Hebel). Keine Anlageberatung.
    </div>
  </div>
</body>
</html>"""


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _load_rules() -> dict:
    with open(_RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Täglicher Portfolio-Alert")
    parser.add_argument("--dry-run", action="store_true",
                        help="Signale ausgeben, keine E-Mail versenden")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
