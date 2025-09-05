import os
import pandas as pd
from datetime import datetime
from config import SETTINGS
from data_sources import get_universe, load_weekly_history, load_index_series
#from breadth import compute_breadth
from breadth import compute_breadth, compute_breadth_snapshots_with_advancers as compute_breadth_snapshots
from report_builder import build_html_report
from emailer import send_email
from indicators import compute_index_indicators  # falls du es nutzt
from risk import compute_risk_metrics  # falls ausgelagert

from report_builder import (
    build_html_report,
    build_index_rows,
    build_risk_rows,
    heuristic_verdict,
    compute_breadth_snapshots
)

def run():
    # 1) Daten laden
    universe = get_universe()
    weekly = load_weekly_history(universe, weeks=SETTINGS.lookback_weeks)
    idx_data = load_index_series()

    # 2) Kennzahlen berechnen
    breadth_df = compute_breadth(weekly)
    #print("Breadth DataFrame:", breadth_df)
    idx_rows = build_index_rows(idx_data)
    risk_rows = build_risk_rows(idx_data)

    # 3) Report erzeugen
    summary = heuristic_verdict(breadth_df, idx_rows)
    report_date = pd.Timestamp.now().strftime("%Y-%m-%d")

    #idx_df = pd.DataFrame.from_dict(dict(idx_rows), orient="index")
    idx_df = pd.DataFrame.from_dict(dict(idx_rows), orient="index").T
    risk_df = pd.DataFrame(risk_rows, columns=["Metrik", "Aktuell", "Vorwoche", "Δ"]).set_index("Metrik")
    
    # Snapshots inkl. Advancers
    breadth_snap = compute_breadth_snapshots(weekly)

    # VIX Sonderbehandlung für Farbgebung im Report: sinkender VIX = positiv
    if "VIX" in risk_df.index:
        delta = risk_df.loc["VIX", "Δ"]
    if pd.notna(delta):
        risk_df.loc["VIX", "Δ_farbe"] = "pos" if delta < 0 else "neg" if delta > 0 else
    

    #html = build_html_report(breadth_df.iloc[0], idx_df, risk_rows, summary, report_date, weekly)
    #html = build_html_report(breadth_df, idx_df, risk_rows, summary, report_date, weekly)
    html = build_html_report(breadth_df, idx_df, risk_df, summary, report_date, weekly)
    #html = build_html_report(breadth_df, idx_df, risk_df, summary, report_date, breadth_snap)

    # 4) Report senden
    send_email(html)


if __name__ == "__main__":
    run()
