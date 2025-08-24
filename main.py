import os
import pandas as pd
from datetime import datetime
from config import SETTINGS
from data_sources import get_universe, load_weekly_history, load_index_series
from breadth import compute_breadth
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
    universe = get_universe()
    weekly = load_weekly_history(universe, weeks=SETTINGS.lookback_weeks)
    idx_data = load_index_series()

    breadth_df = compute_breadth(weekly)
    breadth_snap = compute_breadth_snapshots(weekly, offsets=[0, 1, 4])
    idx_rows = build_index_rows(idx_data)
    idx_df = pd.DataFrame.from_dict(dict(idx_rows), orient="index")
    risk_rows = build_risk_rows(idx_data)
    risk_df = pd.DataFrame(risk_rows, columns=["Metrik", "Aktuell", "Vorwoche"]).set_index("Metrik")
    risk_df["Δ"] = risk_df["Aktuell"] - risk_df["Vorwoche"]

    summary = heuristic_verdict(breadth_df, idx_rows)
    report_date = datetime.today().strftime("%Y-%m-%d")

    html = build_html_report(
        breadth=breadth_df,
        breadth_snap=breadth_snap,
        idx=idx_df,
        risk=risk_df,
        summary=summary,
        report_date=report_date
    )

    send_email(html)

if __name__ == "__main__":
    run()
