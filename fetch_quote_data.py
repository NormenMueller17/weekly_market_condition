import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import pandas as pd

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
		"Sector": None,
					"EPS_FWD_TTM": None,
					"EPS_GROWTH_FWD_TTM": None,
					"REV_GROWTH_TTM_YOY": None,
				}
			results[tkr] = data
	return results

# --- Aktuellen Schlusskurs & Marktkapitalisierung ergänzen ---
def fetch_quote_data_single(ticker: str) -> dict:
	"""
	Holt Close, MarketCap (Mio), EPS (Forward/TTM) und Revenue Growth (TTM YoY)
	für EINEN Ticker. Mit kleinem Retry-Mechanismus.
	"""
	MAX_RETRIES = 3
	SLEEP_SECONDS = 0.75

	for attempt in range(MAX_RETRIES):
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

			# Sector
			sector = info.info.get("sector")

			# EPS: Forward + TTM
			eps_forward = (
				fast.get("epsForward")
				or info.info.get("forwardEps")
			)
			eps_trailing = (
				fast.get("epsTrailingTwelveMonths")
				or info.info.get("trailingEps")
			)
			eps_fwd_ttm = eps_forward if eps_forward is not None else eps_trailing

			# EPS-Wachstum (Forward vs. TTM)
			eps_growth_pct = None
			if eps_forward is not None and eps_trailing not in (None, 0):
				try:
					eps_growth_pct = (eps_forward / eps_trailing - 1.0) * 100.0
				except Exception:
					eps_growth_pct = None

			# Revenue Growth (TTM YoY)
			rev_growth_pct = None
			try:
				rg = info.info.get("revenueGrowth")
				if rg is not None:
					rev_growth_pct = float(rg) * 100.0
			except Exception:
				rev_gr
			# --- Additional fundamental metrics (free Yahoo data) ---
			def _sf(x):
				try:
					if x is None:
						return None
					return float(x)
				except Exception:
					return None

			net_income = _sf(info.info.get("netIncomeToCommon") or info.info.get("netIncome"))
			equity = _sf(info.info.get("totalStockholderEquity"))
			operating_income = _sf(info.info.get("operatingIncome"))
			revenue = _sf(info.info.get("totalRevenue"))
			free_cash_flow = _sf(info.info.get("freeCashflow"))
			total_debt = _sf(info.info.get("totalDebt"))

			# ROE (%)
			roe = None
			if net_income is not None and equity not in (None, 0):
				roe = (net_income / equity) * 100.0

			# Operating Margin (%): prefer computed from Operating Income / Revenue; fallback to operatingMargins
			op_margin = None
			if operating_income is not None and revenue not in (None, 0):
				op_margin = (operating_income / revenue) * 100.0
			else:
				om = _sf(info.info.get("operatingMargins"))
				if om is not None:
					op_margin = om * 100.0

			# FCF Margin (%)
			fcf_margin = None
			if free_cash_flow is not None and revenue not in (None, 0):
				fcf_margin = (free_cash_flow / revenue) * 100.0

			# Debt to Equity (ratio)
			debt_to_equity = None
			if total_debt is not None and equity not in (None, 0):
				debt_to_equity = total_debt / equity
			else:
				d2e = _sf(info.info.get("debtToEquity"))
				if d2e is not None:
					# Yahoo sometimes returns debtToEquity as percentage (e.g., 45.3). Convert if it looks like percent.
					debt_to_equity = d2e / 100.0 if d2e > 10 else d2e

			# EPS Acceleration (percentage points): derived from quarterly income statement EPS if available
			eps_acceleration = None
			try:
				qs = getattr(info, "quarterly_income_stmt", None)
				if qs is None or getattr(qs, "empty", True):
					get_stmt = getattr(info, "get_income_stmt", None)
					if callable(get_stmt):
						qs = get_stmt(freq="quarterly")
				if qs is not None and not qs.empty:
					# Try to find an EPS row
					row_name = None
					for cand in ["Diluted EPS", "Basic EPS", "Earnings Per Share"]:
						if cand in qs.index:
							row_name = cand
							break
					if row_name is not None:
						eps_series = pd.to_numeric(qs.loc[row_name], errors="coerce").dropna()
						# Need at least 6 quarters to compute 2 YoY growth values (q0 vs q4, q1 vs q5)
						if len(eps_series) >= 6:
							eps_vals = eps_series.values
							# Ensure order newest->oldest
							# yfinance columns are typically in reverse-chronological already; sort just in case
							if hasattr(eps_series, "index"):
								eps_series = eps_series.sort_index(ascending=False)
								eps_vals = eps_series.values
							g_latest = None
							g_prev = None
							if eps_vals[4] not in (0, None) and not pd.isna(eps_vals[4]):
								g_latest = (eps_vals[0] / eps_vals[4] - 1.0) * 100.0
							if eps_vals[5] not in (0, None) and not pd.isna(eps_vals[5]):
								g_prev = (eps_vals[1] / eps_vals[5] - 1.0) * 100.0
							if g_latest is not None and g_prev is not None:
								eps_acceleration = g_latest - g_prev
			except Exception:
				eps_acceleration = None

owth_pct = None

			return {
				"Close": close,
				"MarketCap_Mio": market_cap_mio,
				"Sector": sector,
				"EPS_FWD_TTM": eps_fwd_ttm,
				"EPS_GROWTH_FWD_TTM": eps_growth_pct,
				"REV_GROWTH_TTM_YOY": rev_growth_pct,
			"ROE": roe,
			"Operating_Margin": op_margin,
			"FCF_Margin": fcf_margin,
			"Debt_to_Equity": debt_to_equity,
			"EPS_Acceleration": eps_acceleration,
			}

		except Exception as e:
			if attempt < MAX_RETRIES - 1:
				print(f"[WARN] fetch_quote_data_single({ticker}) Versuch {attempt+1} fehlgeschlagen ({e}). "
					  f"Retry in {SLEEP_SECONDS}s ...")
				time.sleep(SLEEP_SECONDS)
				continue
			else:
				print(f"[ERROR] fetch_quote_data_single({ticker}) dauerhaft fehlgeschlagen ({e}).")

	# Fallback: alles None
	return {
		"Close": None,
		"MarketCap_Mio": None,
		"Sector": None,
		"EPS_FWD_TTM": None,
		"EPS_GROWTH_FWD_TTM": None,
		"REV_GROWTH_TTM_YOY": None,
		"ROE": None,
		"Operating_Margin": None,
		"FCF_Margin": None,
		"Debt_to_Equity": None,
		"EPS_Acceleration": None,
	}

