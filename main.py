import os
import pandas as pd
from datetime import datetime
from config import SETTINGS
from data_sources import get_universe, get_company_info_map_from_csv, load_weekly_history, load_index_series
from breadth import compute_breadth, compute_breadth_snapshots_with_advancers as compute_breadth_snapshots
from emailer import send_email
from screener import screen_universe_minervini
from openpyxl.utils import get_column_letter
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment
from pathlib import Path
import yfinance as yf

from report_builder import (
    build_html_report,
    build_index_rows,
    build_risk_rows,
    heuristic_verdict,
)

BOOLEAN_HEADERS = [
    "SMA10W steigend",
    "SMA30W steigend",
    "SMA40W steigend",
    "MA-Ordnung 10>30>40",
    "52W Range OK",
    "RS-Trend ↑",
    "Vol-Breakout",
    "Close > Vorwoche",
        ]

def style_boolean_columns(ws, headers=BOOLEAN_HEADERS, header_row: int = 1) -> None:
    """Färbt Bool-Spalten: True -> hellgrün, False -> hellrot; Text grau & zentriert."""
    # Header -> Spaltenindex (1-based)
    header_to_col = {cell.value: cell.column for cell in ws[header_row] if cell.value}

    fill_green = PatternFill(fill_type="solid", fgColor="E6F4EA")  # hellgrün
    fill_red   = PatternFill(fill_type="solid", fgColor="FDE8E8")  # hellrot
    font_gray  = Font(color="666666")
    center     = Alignment(horizontal="center", vertical="center")

    for head in headers:
        col = header_to_col.get(head)
        if not col:
            continue
        for row in range(header_row + 1, ws.max_row + 1):
            cell = ws.cell(row=row, column=col)
            # robust: bool, WAHR/FALSCH, TRUE/FALSE, 1/0 …
            val = cell.value
            sval = ("" if val is None else str(val)).strip().lower()
            is_true  = sval in ("true", "wahr", "1")
            is_false = sval in ("false", "falsch", "0")
            cell.font = font_gray
            cell.alignment = center
            if is_true:
                cell.fill = fill_green
            elif is_false:
                cell.fill = fill_red
            else:
                # neutral: z.B. leere Zellen
                pass

def run():
    # 1) Daten laden
    universe = get_universe()
    weekly = load_weekly_history(universe, weeks=SETTINGS.lookback_weeks)
    idx_data = load_index_series()

    print(f"[DEBUG] Universe size: {len(universe)}")
    non_empty = sum(1 for _, df in weekly.items() if isinstance(df, pd.DataFrame) and "Close" in df.columns and not df["Close"].dropna().empty)
    print(f"[DEBUG] Weekly non-empty datasets: {non_empty}")    

    # 2) Kennzahlen berechnen
    breadth_df = compute_breadth(weekly)
    idx_rows = build_index_rows(idx_data)
    risk_rows = build_risk_rows(idx_data)

    # 3) Report erzeugen
    summary = heuristic_verdict(breadth_df, idx_rows)
    report_date = pd.Timestamp.now().strftime("%Y-%m-%d")

    idx_df = pd.DataFrame.from_dict(dict(idx_rows), orient="index").T
    risk_df = pd.DataFrame(risk_rows, columns=["Metrik", "Aktuell", "Vorwoche", "Δ"]).set_index("Metrik")
    risk_df.rename(index={"TNX": "10Y Interest Rate (TNX)"}, inplace=True)
    risk_df.rename(index={"VIX": "Volatility Index (VIX)"}, inplace=True)
    risk_df.rename(index={"UUP": "US Dollar Index (UUP)"}, inplace=True)
    
    # Snapshots inkl. Advancers
    #breadth_snap = compute_breadth_snapshots(weekly)

    # Hintergrundfarben für alle Δ-Werte in risk_df
    def classify_delta(value: float, invert: bool = False) -> str:
        if pd.isna(value):
            return "neutral"
        if value > 0:
            return "neg" if invert else "pos"
        if value < 0:
            return "pos" if invert else "neg"
        return "neutral"
    
    risk_df["Δ_farbe"] = [
        classify_delta(delta, invert=("Volatility Index" in name))
        for name, delta in zip(risk_df.index, risk_df["Δ"])
    ]
    
    # 4) Marktführer nach Minervini screenen
    leaders = screen_universe_minervini(universe, min_score=0)
    info_map = get_company_info_map_from_csv()
    
    # --- Aktuellen Schlusskurs & Marktkapitalisierung ergänzen ---
    def fetch_quote_data(ticker: str) -> dict:
        """
        Holt Schlusskurs, MarketCap (Mio) und EPS (Forward, mit TTM-Fallback) robust über yfinance.
        Wenn eine Kennzahl nicht verfügbar ist, wird None zurückgegeben.
        """
        try:
            info = yf.Ticker(ticker)
            fast = getattr(info, "fast_info", {}) or {}
    
            # Close
            close = (
                fast.get("lastPrice")
                or fast.get("last_price")
                or info.info.get("regularMarketPrice")
            )
    
            # MarketCap
            market_cap = fast.get("marketCap") or info.info.get("marketCap")
            market_cap_mio = market_cap / 1_000_000 if market_cap else None
    
            # EPS: Forward + TTM
            eps_forward = (
                fast.get("epsForward")
                or info.info.get("forwardEps")
            )
            eps_trailing = (
                fast.get("epsTrailingTwelveMonths")
                or info.info.get("trailingEps")
            )
            
            # EPS, wie bisher in der Spalte "EPS (Forward/TTM)" angezeigt:
            eps_fwd_ttm = eps_forward if eps_forward is not None else eps_trailing
    
            # EPS-Wachstum (Forward vs. TTM) in %:
            eps_growth_pct = None
            if eps_forward is not None and eps_trailing not in (None, 0):
                try:
                    eps_growth_pct = (eps_forward / eps_trailing - 1.0) * 100.0
                except Exception:
                    eps_growth_pct = None
    
            return {
                "Close": close,
                "MarketCap_Mio": market_cap_mio,
                "EPS_FWD_TTM": eps_fwd_ttm,
                "EPS_GROWTH_FWD_TTM": eps_growth_pct,
            }
        except Exception:
            return {
                "Close": None,
                "MarketCap_Mio": None,
                "EPS_FWD_TTM": None,
                "EPS_GROWTH_FWD_TTM": None,
            }
    
    if not leaders.empty:
        # Company & Industry (bereits geladen über info_map)
        leaders.insert(1, "Company", leaders.index.map(lambda t: info_map.get(t, {}).get("Company", "n/a")))
        leaders.insert(2, "Industry", leaders.index.map(lambda t: info_map.get(t, {}).get("Industry", "n/a")))
        # Close, MarketCap & EPS ergänzen
        leaders.insert(3, "Close", leaders.index.map(lambda t: fetch_quote_data(t).get("Close")))
        leaders.insert(4, "MarketCap (Mio USD)", leaders.index.map(lambda t: fetch_quote_data(t).get("MarketCap_Mio")))
        leaders.insert(5, "EPS (Forward/TTM)", leaders.index.map(lambda t: fetch_quote_data(t).get("EPS_FWD_TTM")))
        leaders.insert(6, "EPS Wachstum FWD/TTM (%)", leaders.index.map(lambda t: fetch_quote_data(t).get("EPS_GROWTH_FWD_TTM")))

      # Falls Screener noch keine 52W-Spalten liefert, zur Sicherheit anlegen
        if "52W High" not in leaders.columns:
            leaders["52W High"] = pd.NA
        if "Dist to 52W High (%)" not in leaders.columns:
            leaders["Dist to 52W High (%)"] = pd.NA

        # NEU: "Close Vorwoche" und "Veränderung in %"
        if "close_weekly_prev" in leaders.columns:
            leaders.insert(7, "Close Vorwoche", leaders["close_weekly_prev"])
        else:
            leaders.insert(7, "Close Vorwoche", pd.NA)

        if "close_weekly_change_pct" in leaders.columns:
            leaders.insert(8, "Veränderung in %", leaders["close_weekly_change_pct"])
        else:
            leaders.insert(8, "Veränderung in %", pd.NA)
        
        leaders.insert(9, "Ø-Volume 20W", leaders["vol20"])
        leaders.insert(10, "Volume Score", leaders["vol_score"])
        
        # Alte Roh-Spalten nicht mehr gebraucht
        drop_cols = [c for c in ["vol20", "vol_score", "close_weekly_now", "close_weekly_prev", "close_weekly_change_pct"] if c in leaders.columns]
        if drop_cols:
            leaders.drop(columns=drop_cols, inplace=True)

        # ---- Spaltenreihenfolge: Score sichtbar + 52W-Spalten nach Close ----
    # score kommt aus dem Screener; wir nehmen ihn explizit nach vorne
    preferred_order = [
        "Company",
        "Industry",
        "MarketCap (Mio USD)",
        "EPS (Forward/TTM)",
        "EPS Wachstum FWD/TTM (%)",
        "Close",
        "Close Vorwoche",
        "Veränderung in %",        
        "52W High",
        "Dist to 52W High (%)",
        "Ø-Volume 20W",
        "Volume Score",
        "score",
        "RS (O'Neil)",
        "SMA10W steigend",
        "SMA30W steigend",
        "SMA40W steigend",
        "MA-Ordnung 10>30>40",
        "52W Range OK",
        "RS-Trend ↑",
        "Vol-Breakout",
        "Close > Vorwoche",
    ]

    existing_pref = [c for c in preferred_order if c in leaders.columns]
    remaining = [c for c in leaders.columns if c not in existing_pref]
    leaders = leaders[existing_pref + remaining]
    
    html = build_html_report(breadth_df, idx_df, risk_df, summary, report_date, weekly, leaders)

    #Screener-Ausgabe prüfen
    print(f"[DEBUG] Found {len(leaders)} Minervini leaders")

    # leaders ist das Ergebnis deines screeners, inkl. Company/Industry-Spalten
    #leaders_out = leaders
    leaders_out = leaders.reset_index().rename(columns={"index": "Ticker"})
    #leaders_out = leaders.sort_values(["score", "Ticker"], ascending=[False, True])
    
    # 1) Zielpfad sicherstellen (eigener Output-Ordner ist sauberer)
    out_dir = Path("artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"market_leaders_{report_date}.xlsx"
    
    # 2) Immer schreiben – auch wenn leer (dann gibt's wenigstens Header)
    leaders_out.to_excel(out_path, index=False, sheet_name="Leaders")

    
    # Excel laden - neu
    wb = load_workbook(out_path)
    ws = wb.active
    
    # -------------------------------
    # Auto-Fit für alle Spalten
    # -------------------------------
    for col_idx, col_cells in enumerate(ws.columns, start=1):
        max_len = 0
        for cell in col_cells:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, len(val))
        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2
    
    # -------------------------------
    # Number-Format Regeln
    # -------------------------------
    
    # Spaltennamen zu Spaltenindex mappen
    header_row = {cell.value: cell.column for cell in ws[1] if cell.value}
    
    # 2 Nachkommastellen
    two_dec_cols = [
        "EPS (Forward/TTM)",
        "EPS Wachstum FWD/TTM (%)",
        "Close",
        "Close Vorwoche",
        "Veränderung in %",
        "52W High",
        "Dist to 52W High (%)",
        "Volume Score",
    ]
    
    # Ganze Zahlen (ohne Nachkommastellen)
    zero_dec_cols = [
        "MarketCap (Mio USD)",
        "Ø-Volume 20W",
    ]
    
    # --- Anwenden der Formate ---
    for col_name in two_dec_cols:
        if col_name in header_row:
            col_letter = get_column_letter(header_row[col_name])
            for cell in ws[col_letter][1:]:  # alle Zeilen außer Header
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "0.00"
    
    for col_name in zero_dec_cols:
        if col_name in header_row:
            col_letter = get_column_letter(header_row[col_name])
            for cell in ws[col_letter][1:]:
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "#,##0"
    
    wb.save(out_path)

    #-ende neu
    
    # 3) Auto-Fit nur, wenn Datei existiert und nicht leer
    #if out_path.exists() and leaders_out.shape[1] > 0:
    #    wb = load_workbook(out_path)
    #    ws = wb.active
    #
    #    for col_idx, col_cells in enumerate(ws.columns, start=1):
    #        max_len = 0
    #        for cell in col_cells:
    #            val = "" if cell.value is None else str(cell.value)
    #            if len(val) > max_len:
    #                max_len = len(val)
    #        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2
    #        
    #    style_boolean_columns(ws)
    #    wb.save(out_path)
    #else:
    #    print(f"[WARN] Excel not created or empty: {out_path}")
    
    # 4) Beim Mailversand denselben Pfad anhängen
    send_email(html, subject_suffix="Weekly US Market Report", attachments=[str(out_path)])
        
    # 5) Report senden
    #send_email(html, subject_suffix="Weekly US Market Report", attachments=[out_path])

if __name__ == "__main__":
    run()
