import time
import random
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import pandas as pd

# Optional but highly recommended: reuse a single HTTP session for all yfinance calls
# to reduce overhead and lower the chance of triggering captchas / throttling.
try:
    yf.shared._requests = requests.Session()
except Exception:
    # If yfinance internals change, we still want the module to work.
    pass

def batch_fetch_quote_data(tickers) -> dict:
    """
    Holt Fundamentaldaten für eine Liste von Tickern parallel.
    Rückgabe: dict[ticker] -> dict mit Feldern wie in fetch_quote_data_single.
    """
    results = {}
    tickers = list(dict.fromkeys(tickers))  # Duplikate raus

    # Yahoo Finance is very sensitive to parallel requests.
    # Keep concurrency low to avoid hard rate limiting.
    MAX_WORKERS = 4
    max_workers = min(MAX_WORKERS, max(1, len(tickers)))

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
                # Keep the shape stable so merges into the Excel dataframe don't break.
                data = {
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
                    "ROIC": None,
                    "Cash_Conversion": None,
                    "Op_Margin_Stability_5y": None,
                    "REV_Neg_YoY_Count_5y": None,
                    "REV_Growth_Std_5y": None,
                    "EPS_Neg_YoY_Count_5y": None,
                    "EPS_Growth_Std_5y": None,
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
    BASE_SLEEP_SECONDS = 0.75

    for attempt in range(MAX_RETRIES):
        try:
            tkr = yf.Ticker(ticker)
            # IMPORTANT: `.info` can be None; always normalize to dict.
            info = (tkr.info or {})
            fast = getattr(tkr, "fast_info", {}) or {}

            # Close
            close = (
                fast.get("lastPrice")
                or fast.get("last_price")
                or info.get("regularMarketPrice")
            )

            # MarketCap
            market_cap = fast.get("marketCap") or info.get("marketCap")
            market_cap_mio = market_cap / 1_000_000 if market_cap else None

            # Sector
            sector = info.get("sector")

            # EPS: Forward + TTM
            eps_forward = (
                fast.get("epsForward")
                or info.get("forwardEps")
            )
            eps_trailing = (
                fast.get("epsTrailingTwelveMonths")
                or info.get("trailingEps")
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
                rg = info.get("revenueGrowth")
                if rg is not None:
                    rev_growth_pct = float(rg) * 100.0
            except Exception:
                rev_growth_pct = None
            # --- Additional fundamental metrics (free Yahoo data) ---
            def _sf(x):
                try:
                    if x is None:
                        return None
                    return float(x)
                except Exception:
                    return None

            
            def _pick_row(stmt: pd.DataFrame, candidates: list[str]) -> str | None:
                if stmt is None or getattr(stmt, "empty", True):
                    return None
                for c in candidates:
                    if c in stmt.index:
                        return c
                return None

            def _latest_stmt_value(stmt: pd.DataFrame, row_candidates: list[str]) -> float | None:
                """Return latest (most recent) numeric value for a row in a statement."""
                row = _pick_row(stmt, row_candidates)
                if row is None:
                    return None
                try:
                    ser = pd.to_numeric(stmt.loc[row], errors="coerce").dropna()
                    if ser.empty:
                        return None
                    # columns are dates; yfinance typically provides newest first, but we handle both
                    ser = ser.sort_index(ascending=False)
                    return _sf(ser.iloc[0])
                except Exception:
                    return None

            def _last_n_years_op_margin_std(stmt: pd.DataFrame, years: int = 5) -> float | None:
                """Std-dev of Operating Margin (%) over last N yearly periods."""
                if stmt is None or getattr(stmt, "empty", True):
                    return None
                op_row = _pick_row(stmt, ["Operating Income", "OperatingIncome", "Operating Income or Loss"])
                rev_row = _pick_row(stmt, ["Total Revenue", "TotalRevenue", "Operating Revenue", "OperatingRevenue"])
                if op_row is None or rev_row is None:
                    return None
                try:
                    op = pd.to_numeric(stmt.loc[op_row], errors="coerce")
                    rev = pd.to_numeric(stmt.loc[rev_row], errors="coerce")
                    m = (op / rev) * 100.0
                    m = m.replace([pd.NA, pd.NaT, float("inf"), float("-inf")], pd.NA).dropna()
                    if m.empty:
                        return None
                    m = m.sort_index(ascending=False).head(years)
                    if len(m) < 3:
                        # too few points for a meaningful stability measure
                        return None
                    return float(m.std(ddof=0))
                except Exception:
                    return None

            net_income = _sf(info.get("netIncomeToCommon") or info.get("netIncome"))
            equity = _sf(info.get("totalStockholderEquity"))
            operating_income = _sf(info.get("operatingIncome"))
            revenue = _sf(info.get("totalRevenue"))
            free_cash_flow = _sf(info.get("freeCashflow"))
            total_debt = _sf(info.get("totalDebt"))

            # ROE (%)
            roe = None
            roe_info = _sf(info.get("returnOnEquity"))
            if roe_info is not None:
                roe = roe_info * 100.0
            elif net_income is not None and equity not in (None, 0):
                roe = (net_income / equity) * 100.0

            # Operating Margin (%): prefer computed from Operating Income / Revenue; fallback to operatingMargins
            op_margin = None
            if operating_income is not None and revenue not in (None, 0):
                op_margin = (operating_income / revenue) * 100.0
            else:
                om = _sf(info.get("operatingMargins"))
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
                d2e = _sf(info.get("debtToEquity"))
                if d2e is not None:
                    # Yahoo sometimes returns debtToEquity as percentage (e.g., 45.3). Convert if it looks like percent.
                    debt_to_equity = d2e / 100.0 if d2e > 10 else d2e

            # EPS Acceleration (percentage points)
            # Prefer quarterly Diluted EPS (needs >= 6 quarters for 2 YoY rates). Fallback: yearly Diluted EPS (needs >= 3 years).
            eps_acceleration = None
            try:
                def _extract_eps_series(stmt):
                    if stmt is None or getattr(stmt, "empty", True):
                        return None
                    row_name = None
                    for cand in ["Diluted EPS", "Basic EPS", "Earnings Per Share"]:
                        if cand in stmt.index:
                            row_name = cand
                            break
                    if row_name is None:
                        return None
                    s = pd.to_numeric(stmt.loc[row_name], errors="coerce").dropna()
                    if hasattr(s, "sort_index"):
                        s = s.sort_index(ascending=False)  # newest -> oldest
                    return s

                
                def _extract_revenue_series(stmt):
                    """Return yearly Total Revenue series (newest -> oldest) as numeric."""
                    if stmt is None or getattr(stmt, "empty", True):
                        return None
                    row_name = None
                    for cand in ["Total Revenue", "TotalRevenue", "Operating Revenue", "OperatingRevenue", "Revenue"]:
                        if cand in stmt.index:
                            row_name = cand
                            break
                    if row_name is None:
                        return None
                    s = pd.to_numeric(stmt.loc[row_name], errors="coerce").dropna()
                    if hasattr(s, "sort_index"):
                        s = s.sort_index(ascending=False)  # newest -> oldest
                    return s

                def _yoy_growth_stats(s, years: int = 5):
                    """
                    Compute (neg_count, std_growth_pct, window_used) for YoY growth.
                    Uses as many years as available up to `years`.
                    Expects s newest->oldest or date-indexed; handles both.
                
                    Returns (None, None, 0) if insufficient data (<3 annual points => <2 YoY).
                    """
                    if s is None or len(s) < 3:
                        return None, None, 0
                
                    try:
                        s2 = s.copy()
                        if hasattr(s2, "sort_index"):
                            s2 = s2.sort_index(ascending=True)  # oldest->newest for pct_change
                
                        g = s2.pct_change().dropna() * 100.0  # YoY growth rates
                        if g.empty:
                            return None, None, 0
                
                        window_used = int(min(years, len(g)))
                        if window_used < 2:
                            return None, None, window_used
                
                        g_last = g.tail(window_used)
                        neg_count = int((g_last < 0).sum())
                        std_growth = float(g_last.std(ddof=0))
                        return neg_count, std_growth, window_used
                    except Exception:
                        return None, None, 0
# --- Quarterly EPS Acceleration ---
                qs = getattr(tkr, "quarterly_income_stmt", None)
                if qs is None or getattr(qs, "empty", True):
                    get_stmt = getattr(tkr, "get_income_stmt", None)
                    if callable(get_stmt):
                        qs = get_stmt(freq="quarterly")

                eps_q = _extract_eps_series(qs)
                if eps_q is not None and len(eps_q) >= 6:
                    v = eps_q.values  # newest->oldest
                    g_latest = None
                    g_prev = None
                    if v[4] not in (0, None) and not pd.isna(v[4]):
                        g_latest = (v[0] / v[4] - 1.0) * 100.0
                    if v[5] not in (0, None) and not pd.isna(v[5]):
                        g_prev = (v[1] / v[5] - 1.0) * 100.0
                    if g_latest is not None and g_prev is not None:
                        eps_acceleration = g_latest - g_prev

                # --- Yearly fallback ---
                if eps_acceleration is None:
                    ys = getattr(tkr, "income_stmt", None)
                    if ys is None or getattr(ys, "empty", True):
                        get_stmt = getattr(tkr, "get_income_stmt", None)
                        if callable(get_stmt):
                            ys = get_stmt(freq="yearly")

                    eps_y = _extract_eps_series(ys)
                    if eps_y is not None and len(eps_y) >= 3:
                        v = eps_y.values  # newest->oldest
                        g_latest = None
                        g_prev = None
                        if v[1] not in (0, None) and not pd.isna(v[1]):
                            g_latest = (v[0] / v[1] - 1.0) * 100.0
                        if v[2] not in (0, None) and not pd.isna(v[2]):
                            g_prev = (v[1] / v[2] - 1.0) * 100.0
                        if g_latest is not None and g_prev is not None:
                            eps_acceleration = g_latest - g_prev
            except Exception:
                eps_acceleration = None

            # --- Quality compounder metrics (Phase 1) ---
            # Cash Conversion = FCF / Net Income
            cash_conversion = None
            if free_cash_flow is not None and net_income not in (None, 0) and not pd.isna(net_income):
                try:
                    cash_conversion = float(free_cash_flow) / float(net_income)
                except Exception:
                    cash_conversion = None

            # Yearly income statement & balance sheet (for ROIC + margin stability)
            ys = getattr(tkr, "income_stmt", None)
            if ys is None or getattr(ys, "empty", True):
                get_stmt = getattr(tkr, "get_income_stmt", None)
                if callable(get_stmt):
                    ys = get_stmt(freq="yearly")

            bs = getattr(tkr, "balance_sheet", None)
            if bs is None or getattr(bs, "empty", True):
                get_bs = getattr(tkr, "get_balance_sheet", None)
                if callable(get_bs):
                    bs = get_bs(freq="yearly")

            # Operating margin stability (std dev of operating margin % over last 5 years)
            op_margin_stability_5y = _last_n_years_op_margin_std(ys, years=5)

            # --- Quality compounder metrics (Phase 2; descriptive only) ---
            # Revenue stability (YoY growth negative count + std over last 5 years)
            rev_neg_yoy_5y = None
            rev_growth_std_5y = None
            try:
                rev_s = _extract_revenue_series(ys)
                rev_neg_yoy_5y, rev_growth_std_5y, _rev_window = _yoy_growth_stats(rev_s, years=5)
            except Exception:
                rev_neg_yoy_5y, rev_growth_std_5y = None, None

            # EPS stability (YoY EPS growth negative count + std over last 5 years)
            eps_neg_yoy_5y = None
            eps_growth_std_5y = None
            try:
                eps_s_y = _extract_eps_series(ys)
                eps_neg_yoy_5y, eps_growth_std_5y = _yoy_growth_stats(eps_s_y, years=5)
            except Exception:
                eps_neg_yoy_5y, eps_growth_std_5y = None, None



            # ROIC (%): NOPAT / (Total Assets - Current Liabilities)
            roic_pct = None
            try:
                operating_income_latest = operating_income
                if operating_income_latest is None:
                    operating_income_latest = _latest_stmt_value(ys, ["Operating Income", "OperatingIncome", "Operating Income or Loss"])

                tax_exp = _latest_stmt_value(ys, ["Tax Provision", "Income Tax Expense", "Income Tax Expense Benefit", "Tax Expense"])
                pretax = _latest_stmt_value(ys, ["Pretax Income", "PretaxIncome"])
                tax_rate = None
                if tax_exp is not None and pretax not in (None, 0) and not pd.isna(pretax):
                    try:
                        tr = float(tax_exp) / float(pretax)
                        # clamp to plausible range
                        if 0 <= tr <= 0.6:
                            tax_rate = tr
                    except Exception:
                        tax_rate = None
                if tax_rate is None:
                    tax_rate = 0.21  # fallback (US statutory-ish); good enough for cross-sectional ranking

                total_assets = _latest_stmt_value(bs, ["Total Assets", "TotalAssets"])
                curr_liab = _latest_stmt_value(bs, ["Total Current Liabilities", "TotalCurrentLiabilities", "Current Liabilities", "CurrentLiabilities"])

                if (
                    operating_income_latest is not None
                    and total_assets not in (None, 0)
                    and curr_liab is not None
                ):
                    invested_capital = float(total_assets) - float(curr_liab)
                    if invested_capital not in (0, None) and invested_capital > 0:
                        nopat = float(operating_income_latest) * (1.0 - float(tax_rate))
                        roic_pct = (nopat / invested_capital) * 100.0
            except Exception:
                roic_pct = None

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
            "ROIC": roic_pct,
            "Cash_Conversion": cash_conversion,
            "Op_Margin_Stability_5y": op_margin_stability_5y,
            "REV_Neg_YoY_Count_5y": rev_neg_yoy_5y,
            "REV_Growth_Std_5y": rev_growth_std_5y,
            "EPS_Neg_YoY_Count_5y": eps_neg_yoy_5y,
            "EPS_Growth_Std_5y": eps_growth_std_5y,
            }

        except Exception as e:
            # Retry with exponential backoff + jitter to avoid synchronized retry storms.
            msg = str(e)
            if attempt < MAX_RETRIES - 1:
                jitter = random.uniform(0.5, 1.5)
                sleep_s = (BASE_SLEEP_SECONDS * (2 ** attempt)) + jitter
                # Be extra conservative on rate limiting / throttling responses.
                if any(k in msg for k in ["Too Many Requests", "Rate limited", "429"]):
                    sleep_s = max(sleep_s, 5.0 + random.uniform(0.0, 3.0))
                print(
                    f"[WARN] fetch_quote_data_single({ticker}) Versuch {attempt+1} fehlgeschlagen ({e}). "
                    f"Retry in {sleep_s:.2f}s ..."
                )
                time.sleep(sleep_s)
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
        "ROIC": None,
        "Cash_Conversion": None,
        "Op_Margin_Stability_5y": None,
    }
