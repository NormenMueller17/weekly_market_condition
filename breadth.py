import pandas as pd
from typing import Dict, List

from indicators import rsi, macd, ema

# Breadth metrics on a per-universe weekly dict[ticker]->DF with Close, High, Low, Volume

def compute_breadth(weekly_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for t, df in weekly_data.items():
        if df is None or df.empty or "Close" not in df:
            continue
        s = pd.to_numeric(df["Close"], errors="coerce")
        frame = pd.DataFrame({
            "close": s,
            "ma50": s.rolling(50).mean(),
            "ma200": s.rolling(200).mean(),
            "hh_52w": s.rolling(52).max(),
            "ll_52w": s.rolling(52).min(),
        })
        frame["ticker"] = t
        rows.append(frame)

    if not rows:
        return pd.DataFrame({
            "%>50w": [0.0], "%>200w": [0.0], "advancers_wow_%": [0.0],
            "new_highs_52w": [0], "new_lows_52w": [0], "universe_size": [0]
        })

    panel = pd.concat(rows, axis=0)
    panel.index.name = "date"
    panel = panel.reset_index().sort_values(["ticker", "date"])
    grp = panel.groupby("ticker", as_index=True)

    def _prev(x: pd.Series):
        return x.iloc[-2] if len(x) > 1 else pd.NA

    agg = grp.agg(
        last_close=("close", "last"),
        prev_close=("close", _prev),
        last_ma50=("ma50", "last"),
        last_ma200=("ma200", "last"),
        last_hh52=("hh_52w", "last"),
        last_ll52=("ll_52w", "last"),
    )

    # NA-sichere Vergleiche
    pct_gt_50  = agg["last_close"].gt(agg["last_ma50"], fill_value=False).mean() * 100
    pct_gt_200 = agg["last_close"].gt(agg["last_ma200"], fill_value=False).mean() * 100
    advancers  = agg["last_close"].gt(agg["prev_close"], fill_value=False).mean() * 100
    new_highs  = agg["last_close"].ge(agg["last_hh52"], fill_value=False).sum()
    new_lows   = agg["last_close"].le(agg["last_ll52"], fill_value=False).sum()
    uni_size   = len(agg)

    return pd.DataFrame({
        "%>50w": [float(pct_gt_50)],
        "%>200w": [float(pct_gt_200)],
        "advancers_wow_%": [float(advancers)],
        "new_highs_52w": [int(new_highs)],
        "new_lows_52w": [int(new_lows)],
        "universe_size": [int(uni_size)],
    })


def compute_breadth_snapshots_with_advancers(weekly_data: Dict[str, pd.DataFrame],
                                              offsets: List[int] = [0, 1, 4]) -> pd.DataFrame:
    """
    Liefert eine Tabelle mit Breadth-Metriken (Zeilen) und Spalten
    für die gewünschten Rücksprungpunkte (0=aktuell, 1=Vorwoche, 4=vor vier Wochen).
    Rechnet alles neu aus den Weekly-Daten (keine Persistenz nötig).
    """
    rows = []
    for t, df in weekly_data.items():
        if df is None or df.empty or "Close" not in df:
            continue
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if s.empty:
            continue

        frame = pd.DataFrame(index=s.index)
        frame["close"]  = s
        frame["ema10"]  = ema(s, span=10)
        frame["ema21"]  = ema(s, span=21)
        frame["ma50"]   = s.rolling(50).mean()
        frame["hh52"]   = s.rolling(52).max()
        frame["ll52"]   = s.rolling(52).min()
        frame["ret1w"]  = s.pct_change(1)
        frame["ticker"] = t
        rows.append(frame)

    if not rows:
        cols = ["Aktuelle Woche", "Woche −1", "Woche −4"]
        idx = [
            "% über 10‑Wochen‑EMA", "% über 21‑Wochen‑EMA", "% über 50‑Wochen‑MA",
            "Neue 52W‑Hochs (Anzahl)", "Neue 52W‑Tiefs (Anzahl)", "1W-Kursgewinner (%)"
        ]
        return pd.DataFrame(0, index=idx, columns=cols, dtype=float)

    panel = pd.concat(rows, axis=0)
    panel.index.name = "date"
    panel = panel.reset_index().sort_values(["ticker", "date"])

    def take_nth(group: pd.DataFrame, n: int) -> pd.DataFrame:
        if len(group) <= n:
            return pd.DataFrame(columns=group.columns)
        return group.iloc[[-(n+1)]]

    snapshots = {}
    for off in offsets:
        snaps = (
            panel.groupby("ticker", as_index=False, group_keys=False)
            .apply(lambda g: take_nth(g, off))
        )
        snapshots[off] = snaps

    def pct_true(series: pd.Series) -> float:
        s = series.dropna()
        return float((s.astype(bool)).mean() * 100) if len(s) else 0.0

    result = {}
    col_names = {0: "Aktuelle Woche", 1: "Woche −1", 4: "Woche −4"}
    for off, snap in snapshots.items():
        if snap.empty:
            result[col_names.get(off, f"−{off}")] = {
                "% über 10‑Wochen‑EMA": 0.0,
                "% über 21‑Wochen‑EMA": 0.0,
                "% über 50‑Wochen‑MA": 0.0,
                "Neue 52W‑Hochs (Anzahl)": 0,
                "Neue 52W‑Tiefs (Anzahl)": 0,
                "1W-Kursgewinner (%)": 0.0,
            }
            continue

        s_close = pd.to_numeric(snap["close"], errors="coerce")
        m = {
            "% über 10‑Wochen‑EMA": pct_true(s_close > snap["ema10"]),
            "% über 21‑Wochen‑EMA": pct_true(s_close > snap["ema21"]),
            "% über 50‑Wochen‑MA":  pct_true(s_close > snap["ma50"]),
            "Neue 52W‑Hochs (Anzahl)": int(((s_close >= snap["hh52"]).fillna(False)).sum()),
            "Neue 52W‑Tiefs (Anzahl)": int(((s_close <= snap["ll52"]).fillna(False)).sum()),
            "1W-Kursgewinner (%)": pct_true(snap["ret1w"] > 0),
        }
        result[col_names.get(off, f"−{off}")] = m

    order_rows = [
        "% über 10‑Wochen‑EMA", "% über 21‑Wochen‑EMA", "% über 50‑Wochen‑MA",
        "Neue 52W‑Hochs (Anzahl)", "Neue 52W‑Tiefs (Anzahl)", "1W-Kursgewinner (%)"
    ]
    out = pd.DataFrame(result).reindex(order_rows)
    return out
