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
from excel_formatting import format_sheet, apply_debt_eps_conditional_formatting
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import warnings

# Silence noisy third-party warnings (optional)
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"yfinance\.scrapers\.fundamentals")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"breadth")
# -------------------------------------------------------------------
# Conditional-format thresholds (easy to adjust later)
# -------------------------------------------------------------------
DEBT_EQ_THR_LOW = 0.50   # <= low -> green
DEBT_EQ_THR_MED = 1.00   # (low..med] -> yellow
DEBT_EQ_THR_HIGH = 2.00  # (med..high] -> orange; >high -> red

EPS_ACCEL_THR_STRONG = 10.0  # >= strong -> green
EPS_ACCEL_THR_MILD = 3.0     # >= mild -> light green
EPS_ACCEL_THR_FLAT = 3.0     # (-flat..flat) -> yellow; <= -flat -> orange/red



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
        leaders.insert(4, "Sektor", leaders.index.map(lambda t: quote_map.get(t, {}).get("Sector", "n/a")))
        leaders.insert(5, "Close", leaders.index.map(lambda t: quote_map.get(t, {}).get("Close")))
        leaders.insert(6, "MarketCap (Mio USD)", leaders.index.map(lambda t: quote_map.get(t, {}).get("MarketCap_Mio")))
        leaders.insert(7, "EPS (Forward/TTM)", leaders.index.map(lambda t: quote_map.get(t, {}).get("EPS_FWD_TTM")))
        leaders.insert(8, "EPS Wachstum FWD/TTM (%)", leaders.index.map(lambda t: quote_map.get(t, {}).get("EPS_GROWTH_FWD_TTM")))
        leaders.insert(9, "Revenue Wachstum TTM YoY (%)", leaders.index.map(lambda t: quote_map.get(t, {}).get("REV_GROWTH_TTM_YOY")))
        leaders.insert(10, "ROE (%)", leaders.index.map(lambda t: quote_map.get(t, {}).get("ROE")))
        leaders.insert(11, "Operating Margin (%)", leaders.index.map(lambda t: quote_map.get(t, {}).get("Operating_Margin")))
        leaders.insert(12, "FCF Margin (%)", leaders.index.map(lambda t: quote_map.get(t, {}).get("FCF_Margin")))
        leaders.insert(13, "Debt to Equity", leaders.index.map(lambda t: quote_map.get(t, {}).get("Debt_to_Equity")))
        leaders.insert(14, "EPS Acceleration (pp)", leaders.index.map(lambda t: quote_map.get(t, {}).get("EPS_Acceleration")))

      # Falls Screener noch keine 52W-Spalten liefert, zur Sicherheit anlegen
        if "52W High" not in leaders.columns:
            leaders["52W High"] = pd.NA
        if "Dist to 52W High (%)" not in leaders.columns:
            leaders["Dist to 52W High (%)"] = pd.NA

        # NEU: "Close Vorwoche" und "Veränderung in %"
        if "close_weekly_prev" in leaders.columns:
            leaders.insert(15, "Close Vorwoche", leaders["close_weekly_prev"])
        else:
            leaders.insert(15, "Close Vorwoche", pd.NA)

        if "close_weekly_change_pct" in leaders.columns:
            leaders.insert(16, "Veränderung in %", leaders["close_weekly_change_pct"])
        else:
            leaders.insert(16, "Veränderung in %", pd.NA)
        
        leaders.insert(17, "Ø-Volume 20T", leaders["vol20"])
        leaders.insert(18, "Volume Score", leaders["vol_score"])
        
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
        industry_table = pd.DataFrame()  # will be populated by compute_industry_scores
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
        "ROE (%)",
        "Operating Margin (%)",
        "FCF Margin (%)",
        "Debt to Equity",
        "EPS Acceleration (pp)",
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
        "ROE",
        "Operating_Margin",
        "FCF_Margin",
        "Debt_to_Equity",
        "EPS_Acceleration",
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
    # Excel Export:
    # - Sheet 'Leaders' enthält nur Industry Ranking + Industry RS Score als Industry-Metriken
    # - Sheet 'Industries' enthält alle Industry-Metriken (Ranking + Teilmetriken + Composite)
    leaders_out_excel = leaders_out.copy()
    drop_ind_cols = [
        'Industry Score',
        'Industry Strong Stock Score',
        'Industry Volume Score',
    ]
    leaders_out_excel.drop(columns=[c for c in drop_ind_cols if c in leaders_out_excel.columns], inplace=True, errors='ignore')
    
    with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
        leaders_out_excel.to_excel(writer, index=False, sheet_name='Leaders')
        # Industries sheet (may be empty if scoring failed)
        if industry_table is not None and not industry_table.empty:
            # Sort industries by ranking (ascending: 1 is best)
            industry_out = industry_table.copy()
            if 'Industry Ranking' in industry_out.columns:
                industry_out = industry_out.sort_values('Industry Ranking', ascending=True, kind='mergesort')
            # Ensure column order (Industry, Sektor, ...)
            if 'Sektor' in industry_out.columns:
                cols = ['Industry', 'Sektor'] + [c for c in industry_out.columns if c not in ('Industry','Sektor')]
                industry_out = industry_out[cols]
            industry_out.to_excel(writer, index=False, sheet_name='Industries')
        else:
            # Write an empty template so the sheet always exists
            pd.DataFrame(columns=['Industry', 'Sektor', 'Industry Ranking', 'Industry_RS_raw', 'Valid_RS_Count',
                                  'Industry RS Score', 'Industry Strong Stock Score', 'Activity', 'Direction',
                                  'Industry Volume Score', 'Industry Score']).to_excel(
                writer, index=False, sheet_name='Industries'
            )

    
    # Excel laden - neu
    wb = load_workbook(out_path)
    ws = wb['Leaders']

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
    # Excel formatting (reusable helper)
    # -------------------------------
    leaders_formats = {
        # 2 decimals
        "Industry Score": "0.00",
        "Industry RS Score": "0.00",
        "Industry Strong Stock Score": "0.00",
        "Industry Volume Score": "0.00",
        "EPS (Forward/TTM)": "0.00",
        "EPS Wachstum FWD/TTM (%)": "0.00",
        "Revenue Wachstum TTM YoY (%)": "0.00",
        "Close": "0.00",
        "Close Vorwoche": "0.00",
        "Veränderung in %": "0.00",
        "52W High": "0.00",
        "Dist to 52W High (%)": "0.00",
        "Volume Score": "0.00",

        # 5 new fundamentals (Excel headers)
        "ROE (%)": "0.00",
        "Operating Margin (%)": "0.00",
        "FCF Margin (%)": "0.00",
        "Debt to Equity": "0.00",
        "EPS Acceleration (pp)": "0.00",

        # integers
        "Industry Ranking": "0",
        "MarketCap (Mio USD)": "#,##0",
        "Ø-Volume 20T": "#,##0",
    }

    # Leaders: do NOT sort (keep existing ordering)
    format_sheet(
        ws,
        formats_by_colname=leaders_formats,
        autofit_headers=True,
        sort_by=None,
    )

    # Conditional formatting (Ampel) for risk & momentum flags
    apply_debt_eps_conditional_formatting(
    ws,
    debt_col="Debt to Equity",
    eps_col="EPS Acceleration (pp)",
    debt_thr_low=DEBT_EQ_THR_LOW,
    debt_thr_med=DEBT_EQ_THR_MED,
    debt_thr_high=DEBT_EQ_THR_HIGH,
    eps_thr_strong=EPS_ACCEL_THR_STRONG,
    eps_thr_mild=EPS_ACCEL_THR_MILD,
    eps_thr_flat=EPS_ACCEL_THR_FLAT,
    )

    # Industries: sort by rank + apply formats
    if 'Industries' in wb.sheetnames:
        ws_ind = wb['Industries']
        industries_formats = {
            "Industry Ranking": "0",
            "Industry RS Score": "0.00",
            "Industry Strong Stock Score": "0.00",
            "Activity": "0.00",
            "Direction": "0.00",
            "Industry Volume Score": "0.00",
            "Industry Score": "0.00",
            "Industry_RS_raw": "0.00",
        }

        format_sheet(
            ws_ind,
            formats_by_colname=industries_formats,
            autofit_headers=True,
            sort_by="Industry Ranking",
            sort_ascending=True,
        )

    wb.save(out_path)

    
    # 4) Beim Mailversand denselben Pfad anhängen
    send_email(html, subject_suffix="Weekly US Market Report", attachments=[str(out_path)])

if __name__ == "__main__":
    run()
