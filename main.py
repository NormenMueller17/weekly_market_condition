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
    leaders = screen_universe_minervini(universe, min_score=7)
    info_map = get_company_info_map_from_csv()
    
    # --- Aktuellen Schlusskurs & Marktkapitalisierung ergänzen ---
    def fetch_quote_data(ticker: str) -> dict:
        try:
            info = yf.Ticker(ticker)
            fast = getattr(info, "fast_info", {}) or {}
            close = fast.get("lastPrice") or fast.get("last_price") or info.info.get("regularMarketPrice")
            market_cap = fast.get("marketCap") or info.info.get("marketCap")
    
            # Marktkapitalisierung in Mio USD
            market_cap_mio = market_cap / 1_000_000 if market_cap else None
            return {"Close": close, "MarketCap_Mio": market_cap_mio}
        except Exception:
            return {"Close": None, "MarketCap_Mio": None}
    
    if not leaders.empty:
        # Spalten 'Company' und 'Industry' ergänzen
        company_series = leaders.index.map(lambda t: info_map.get(t, {}).get("Company", "n/a"))
        industry_series = leaders.index.map(lambda t: info_map.get(t, {}).get("Industry", "n/a"))
        quote_data = leaders.index.map(lambda t: fetch_quote_data(t))
        quote_df = pd.DataFrame(list(quote_data), index=leaders.index)    
        
        leaders.insert(0, "Industry", industry_series)
        leaders.insert(0, "Company", company_series)
        leaders.insert(leaders.columns.get_loc("Industry") + 1, "Close", quote_df["Close"])
        leaders.insert(leaders.columns.get_loc("Industry") + 2, "MarketCap (Mio USD)", quote_df["MarketCap_Mio"])
    
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
    
    # 3) Auto-Fit nur, wenn Datei existiert und nicht leer
    if out_path.exists() and leaders_out.shape[1] > 0:
        wb = load_workbook(out_path)
        ws = wb.active
    
        for col_idx, col_cells in enumerate(ws.columns, start=1):
            max_len = 0
            for cell in col_cells:
                val = "" if cell.value is None else str(cell.value)
                if len(val) > max_len:
                    max_len = len(val)
            ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2
            
        style_boolean_columns(ws)
        wb.save(out_path)
    else:
        print(f"[WARN] Excel not created or empty: {out_path}")
    
    # 4) Beim Mailversand denselben Pfad anhängen
    send_email(html, subject_suffix="Weekly US Market Report", attachments=[str(out_path)])
        
    # 5) Report senden
    #send_email(html, subject_suffix="Weekly US Market Report", attachments=[out_path])

if __name__ == "__main__":
    run()
