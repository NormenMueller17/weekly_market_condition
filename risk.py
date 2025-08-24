import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def compute_risk_metrics() -> pd.DataFrame:
    """
    Holt aktuelle und Vorwochenwerte für VIX, CPC, TNX, UUP
    und berechnet die wöchentliche Veränderung (Delta).
    Gibt ein DataFrame mit Spalten: Aktuell, Vorwoche, Δ zurück.
    """
    symbols = ["^VIX", "^CPC", "^TNX", "UUP"]
    end_date = datetime.today()
    start_date = end_date - timedelta(days=14)

    data = yf.download(symbols, start=start_date, end=end_date, interval="1d", group_by="ticker", progress=False)

    results = []

    for symbol in symbols:
        try:
            df = data[symbol] if isinstance(data, dict) else data['Close'][symbol]
            df = df.dropna()
            last = df.iloc[-1]
            prev = df.iloc[-6] if len(df) >= 6 else None  # Letzte Woche (ca. -5 Trading Days)

            delta = last - prev if prev is not None else None

            results.append({
                "Metrik": symbol.replace("^", ""),
                "Aktuell": round(last, 2),
                "Vorwoche": round(prev, 2) if prev else None,
                "Δ": round(delta, 2) if delta else None
            })
        except Exception as e:
            results.append({
                "Metrik": symbol.replace("^", ""),
                "Aktuell": None,
                "Vorwoche": None,
                "Δ": None
            })

    return pd.DataFrame(results).set_index("Metrik")
