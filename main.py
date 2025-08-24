import os
from datetime import datetime
from config import SETTINGS
from data_sources import get_universe, load_weekly_history, load_index_series
from breadth import compute_breadth
from report_builder import build_html_report
from emailer import send_email
from indicators import compute_index_indicators  # falls du es nutzt
from risk import compute_risk_metrics  # falls ausgelagert

def run():
    # 1) Daten laden
    universe = get_universe()
    weekly = load_weekly_history(universe, weeks=SETTINGS.lookback_weeks)
    idx = load_index_series()

    # 2) Breadth berechnen
    breadth_df = compute_breadth(weekly)

    # 3) Risiko/Sentiment berechnen
    risk = compute_risk_metrics()  # z. B. { "VIX": ..., "CPC": ..., ... }

    # 4) Index-Indikatoren berechnen
    weekly_data = compute_index_indicators(idx)

    # 5) Fazit / Zusammenfassung erzeugen
    summary = "Akkumulationsmodus: Übergewichtung zulässig, selektiv zukaufen."

    # 6) Aktuelles Datum für Report
    report_date = datetime.today().strftime("%Y-%m-%d")

    # 7) HTML-Report erzeugen
    html = build_html_report(
        breadth_df,
        idx,
        risk,
        summary,
        report_date,
        weekly_data
    )

    # 8) Mail senden
    send_email(html)

if __name__ == "__main__":
    run()
