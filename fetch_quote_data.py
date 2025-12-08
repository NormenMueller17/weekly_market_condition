def batch_fetch_quote_data(tickers) -> dict:
	"""
	Holt Fundamentaldaten für eine Liste von Tickern parallel.
	Rückgabe: dict[ticker] -> dict mit Feldern wie in fetch_quote_data_single.
	"""
	results = {}
	tickers = list(dict.fromkeys(tickers))  # Duplikate raus

	# Anzahl Threads begrenzen – 8-16 ist ein guter Startwert
	max_workers = min(16, max(4, len(tickers) // 20))  # z.B. 4–16 Threads

	print(f"[INFO] Starte batch_fetch_quote_data für {len(tickers)} Ticker "
		  f"mit {max_workers} Threads ...")

	with ThreadPoolExecutor(max_workers=max_workers) as executor:
		future_to_ticker = {executor.submit(fetch_quote_data_single, t): t for t in tickers}

		for future in as_completed(future_to_ticker):
			tkr = future_to_ticker[future]
			try:
				data = future.result()
			except Exception as e:
				print(f"[ERROR] batch_fetch_quote_data: {tkr} -> {e}")
				data = {
					"Close": None,
					"MarketCap_Mio": None,
					"EPS_FWD_TTM": None,
					"EPS_GROWTH_FWD_TTM": None,
					"REV_GROWTH_TTM_YOY": None,
				}
			results[tkr] = data

	return results    