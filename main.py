def run():
  # 1) Daten laden
  universe = get_universe()
  weekly = load_weekly_history(universe, weeks=SETTINGS.lookback_weeks)
  idx = load_index_series()
  # 2) Breadth berechnen
  breadth_df = compute_breadth(weekly)

  # 3) Report bauen
  html = build_html_report(breadth_df, idx)
  # 4) Mail senden
  send_email(html)

if __name__ == "__main__":
  run()
