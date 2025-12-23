import os
import time
import pandas as pd
from datetime import datetime
from config import SETTINGS
from data_sources import get_universe, get_company_info_map_from_csv, load_weekly_history, load_index_series
from breadth import compute_breadth, compute_breadth_snapshots_with_advancers as compute_breadth_snapshots
from emailer import send_email
from screener import screen_universe_minervini
from fetch_quote_data import batch_fetch_quote_data, fetch_quote_data_single
from openpyxl.utils import get_column_letter
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf

from industry_strength import compute_industry_scores

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
     
    if not leaders.empty:
        # --- Sicherheitskopie (sehr wichtig für HTML-Formatierung später) ---
        leaders = leaders.copy()
    
        # Hilfsfunktion zum Entfernen von .DE
        def _make_sa_url(ticker: str) -> str:
            """
            Für US-Aktien:
                https://stockanalysis.com/stocks/TICKER
            Für deutsche (.DE):
                https://stockanalysis.com/quote/etr/TICKER (ohne .DE)
            """
            base = ticker.split(".")[0]
            if ticker.upper().endswith(".DE"):
                return f"https://stockanalysis.com/quote/etr/{base}"
            else:
                return f"https://stockanalysis.com/stocks/{base}"
                
        # Company & Industry (bereits geladen über info_map)
        leaders.insert(1, "Company", leaders.index.map(lambda t: info_map.get(t, {}).get("Company", "n/a")))
        leaders.insert(2, "SA", leaders.index.map(_make_sa_url))      
        leaders.insert(3, "Industry", leaders.index.map(lambda t: info_map.get(t, {}).get("Industry", "n/a")))

        # --- NEU: Fundamentaldaten für ALLE Leaders in einem Rutsch laden ---
        quote_map = batch_fetch_quote_data(leaders.index.tolist())
        leaders.insert(4, "Close", leaders.index.map(lambda t: quote_map.get(t, {}).get("Close")))
        leaders.insert(5, "MarketCap (Mio USD)", leaders.index.map(lambda t: quote_map.get(t, {}).get("MarketCap_Mio")))
        leaders.insert(6, "EPS (Forward/TTM)", leaders.index.map(lambda t: quote_map.get(t, {}).get("EPS_FWD_TTM")))
        leaders.insert(7, "EPS Wachstum FWD/TTM (%)", leaders.index.map(lambda t: quote_map.get(t, {}).get("EPS_GROWTH_FWD_TTM")))
        leaders.insert(8, "Revenue Wachstum TTM YoY (%)", leaders.index.map(lambda t: quote_map.get(t, {}).get("REV_GROWTH_TTM_YOY")))

      # Falls Screener noch keine 52W-Spalten liefert, zur Sicherheit anlegen
        if "52W High" not in leaders.columns:
            leaders["52W High"] = pd.NA
        if "Dist to 52W High (%)" not in leaders.columns:
            leaders["Dist to 52W High (%)"] = pd.NA

        # NEU: "Close Vorwoche" und "Veränderung in %"
        if "close_weekly_prev" in leaders.columns:
            leaders.insert(9, "Close Vorwoche", leaders["close_weekly_prev"])
        else:
            leaders.insert(9, "Close Vorwoche", pd.NA)

        if "close_weekly_change_pct" in leaders.columns:
            leaders.insert(10, "Veränderung in %", leaders["close_weekly_change_pct"])
        else:
            leaders.insert(10, "Veränderung in %", pd.NA)
        
        leaders.insert(11, "Ø-Volume 20T", leaders["vol20"])
        leaders.insert(12, "Volume Score", leaders["vol_score"])
        
        if "RS_delta_4w" in leaders.columns and "ΔRS 4W" not in leaders.columns:
            leaders["ΔRS 4W"] = leaders["RS_delta_4w"]
             
        # Alte Roh-Spalten nicht mehr gebraucht
        drop_cols = [
            "vol20",
            "vol_score",
            "close_weekly_now",
            "close_weekly_prev",
            "close_weekly_change_pct",
            "RS_now",
            "RS_4w",
            "RS_delta_4w",
        ]
        drop_cols = [c for c in drop_cols if c in leaders.columns]
        if drop_cols:
            leaders.drop(columns=drop_cols, inplace=True)

        # ------------------------------------------------------------
        # NEU: Industry Strength Scoring
        # Adds: Industry RS Score, Industry Strong Stock Score,
        #       Industry Volume Score (Activity*Direction, Variant 2),
        #       and the composite Industry Score.
        # ------------------------------------------------------------
        try:
            leaders, industry_table = compute_industry_scores(leaders)
            if industry_table is not None and not industry_table.empty:
                print(f"[INFO] Computed industry scores for {len(industry_table)} industries")
        except Exception as e:
            print(f"[WARN] Industry scoring failed: {e}")

        # ---- Spaltenreihenfolge: Score sichtbar + 52W-Spalten nach Close ----
    # score kommt aus dem Screener; wir nehmen ihn explizit nach vorne
    preferred_order = [
        "Company",
        "SA",
        "Industry",
        "Industry Ranking",
        "Industry Score",
        "Industry RS Score",
        "Industry Strong Stock Score",
        "Industry Volume Score",
        "MarketCap (Mio USD)",
        "EPS (Forward/TTM)",
        "EPS Wachstum FWD/TTM (%)",
        "Revenue Wachstum TTM YoY (%)",
        "Close Vorwoche",
        "Close", 
        "Veränderung in %",        
        "52W High",
        "Dist to 52W High (%)",
        "Ø-Volume 20T",
        "Volume Score",
        "RS (O'Neil)",
        "ΔRS 4W",
        "score",
        "SMA10W steigend",
        "SMA30W steigend",
        "SMA40W steigend",
        "MA-Ordnung 10>30>40",
        "52W Range OK",
        "RS-Trend ↑",
        "Vol-Breakout",
        "Close > Vorwoche",
        "VCP",
        "VCP Waves",
        "VCP Entry",
        "VCP Breakout Level",
    ]

    existing_pref = [c for c in preferred_order if c in leaders.columns]
    remaining = [c for c in leaders.columns if c not in existing_pref]
    leaders = leaders[existing_pref + remaining]

    # --- Formatierte Kopie NUR für HTML-Report ---
    leaders_html = leaders.copy()

    def fmt_2dec(x):
        return f"{x:.2f}" if pd.notna(x) else ""

    def fmt_int(x):
        return f"{x:,.0f}" if pd.notna(x) else ""
    
    # Spalten mit 2 Nachkommastellen
    for col in [
        "Industry Score",
        "Industry RS Score",
        "Industry Strong Stock Score",
        "Industry Volume Score",
        "EPS (Forward/TTM)",
        "EPS Wachstum FWD/TTM (%)",
        "Revenue Wachstum TTM YoY (%)",
        "Close",
        "Close Vorwoche",
        "Veränderung in %",
        "52W High",
        "Dist to 52W High (%)",
        "Volume Score",
        "ΔRS 4W",
        "VCP Entry",
        "VCP Breakout Level",
    ]:
        if col in leaders_html.columns:
            leaders_html[col] = leaders_html[col].apply(fmt_2dec)

    # Spalten mit ganzen Zahlen
    for col in [
        "MarketCap (Mio USD)",
        "Ø-Volume 20T",
    ]:
        if col in leaders_html.columns:
            leaders_html[col] = leaders_html[col].apply(fmt_int)

    # HTML-Report bekommt die formatierte Kopie
    html = build_html_report(breadth_df, idx_df, risk_df, summary, report_date, weekly, leaders_html)    
    
    #Screener-Ausgabe prüfen
    print(f"[DEBUG] Found {len(leaders)} Minervini leaders")

    # leaders ist das Ergebnis deines screeners, inkl. Company/Industry-Spalten
    leaders_out = leaders.reset_index().rename(columns={"index": "Ticker"})
    
    # 1) Zielpfad sicherstellen (eigener Output-Ordner ist sauberer)
    out_dir = Path("artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"market_leaders_{report_date}.xlsx"
    
    # 2) Immer schreiben – auch wenn leer (dann gibt's wenigstens Header)
    leaders_out.to_excel(out_path, index=False, sheet_name="Leaders")

    
    # Excel laden - neu
    wb = load_workbook(out_path)
    ws = wb.active

    # Spalte "SA" finden
    sa_col_idx = None
    for cell in ws[1]:
        if cell.value == "SA":
            sa_col_idx = cell.column
            break
    
    if sa_col_idx is not None:
        sa_col_letter = get_column_letter(sa_col_idx)
        for row in range(2, ws.max_row + 1):
            cell = ws[f"{sa_col_letter}{row}"]
            url = cell.value
            if url and isinstance(url, str):
                cell.value = "SA"
                cell.hyperlink = url
                cell.font = Font(color="0000EE", underline="single")  # Blau + Unterstrichen
    
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
        "Industry Score",
        "Industry RS Score",
        "Industry Strong Stock Score",
        "Industry Volume Score",
        "EPS (Forward/TTM)",
        "EPS Wachstum FWD/TTM (%)",
        "Revenue Wachstum TTM YoY (%)",
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
        "Ø-Volume 20T",
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
                    
    # --- Boolesche Spalten (Minervini-Kriterien) einfärben ---
    style_boolean_columns(ws)
    wb.save(out_path)
    
    # 4) Beim Mailversand denselben Pfad anhängen
    send_email(html, subject_suffix="Weekly US Market Report", attachments=[str(out_path)])

if __name__ == "__main__":
    run()
