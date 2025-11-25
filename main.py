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

from report_builder import (
    build_html_report,
    build_index_rows,
    build_risk_rows,
    heuristic_verdict,
)

#_CVS_FILE = "SP_micro_3.csv"

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
    leaders = screen_universe_minervini(universe, min_score=6)
    info_map = get_company_info_map_from_csv()
    
    if not leaders.empty:
        # Spalten 'Company' und 'Industry' ergänzen
        company_series = leaders.index.map(lambda t: info_map.get(t, {}).get("Company", "n/a"))
        industry_series = leaders.index.map(lambda t: info_map.get(t, {}).get("Industry", "n/a"))
    
        leaders.insert(0, "Industry", industry_series)
        leaders.insert(0, "Company", company_series)
    
    html = build_html_report(breadth_df, idx_df, risk_df, summary, report_date, weekly, leaders)

    #Screener-Ausgabe prüfen
    print(f"[DEBUG] Found {len(leaders)} Minervini leaders")

    # leaders ist das Ergebnis deines screeners, inkl. Company/Industry-Spalten
    # Optional sortieren:
    #leaders_out = leaders.sort_values(["Score", "Ticker"], ascending=[False, True])
    leaders_out = leaders
    
    # Dateiname + Pfad
    out_path = f"market_leaders_{report_date}.xlsx"  # z.B. 2025-09-04
    
    wb = load_workbook(out_path)
    ws = wb.active
    for col_idx, col_cells in enumerate(ws.columns, start=1):
    max_len = 0
    for cell in col_cells:
        try:
            val = str(cell.value) if cell.value is not None else ""
            if len(val) > max_len:
                max_len = len(val)
        except Exception:
            pass
        adjusted_width = (max_len + 2)
        ws.column_dimensions[get_column_letter(col_idx)].width = adjusted_width
    
    wb.save(out_path)
        
    # 5) Report senden
    #send_email(html)
    send_email(html, subject_suffix="Weekly US Market Report", attachments=[out_path])
    #send_email(html, subject_suffix="Weekly US Market Report", attachments=["market_leaders_2025-11-26.xlsx"])

if __name__ == "__main__":
    run()
