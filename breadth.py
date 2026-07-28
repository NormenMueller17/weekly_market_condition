import io

import pandas as pd
import requests
import yfinance as yf
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
        gb = panel.groupby("ticker", as_index=False, group_keys=False)
        try:
            snaps = gb.apply(lambda g: take_nth(g, off), include_groups=False)
        except TypeError:
            snaps = gb.apply(lambda g: take_nth(g, off))
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


def compute_sp500_breadth_200d() -> float | None:
    """Anteil der S&P-500-Titel über ihrer 200-Tage-Linie, in Prozent.

    Gibt **None** zurück, wenn der Wert nicht ermittelbar ist — nicht 100.0.

    Vorher lieferte jeder Fehlschlag 100.0, und der Aufrufer stellte das als
    bestandenen Filter mit grünem Haken dar. Im Lauf vom 2026-07-28 sah das so
    aus:

        [BREADTH] Fehler ... HTTP Error 403: Forbidden — Filter deaktiviert
        [SIGNALS] S&P 500 Marktbreite: 100.0% über 200d ✅

    Der Kaufstopp unter 40 % war damit wirkungslos, und der Report behauptete
    das Gegenteil. Ein fehlgeschlagener Messwert darf nicht wie ein bestandener
    Filter aussehen; ob daraufhin trotzdem gekauft wird, entscheidet der
    Aufrufer — aber er muss den Unterschied kennen.

    Datenquelle: Wikipedia-Liste der S&P-500-Titel + yfinance-Tageskurse.
    """
    try:
        # Wikipedia blockt den Standard-User-Agent von pandas/urllib mit
        # HTTP 403. Deshalb selbst holen und den Text an read_html geben.
        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/125.0.0.0 Safari/537.36"},
        )
        resp.raise_for_status()
        tickers = pd.read_html(
            io.StringIO(resp.text), attrs={"id": "constituents"},
        )[0]["Symbol"].tolist()
        tickers = [t.replace(".", "-") for t in tickers]

        raw = yf.download(
            tickers, period="1y", interval="1d",
            auto_adjust=True, progress=False, threads=True,
        )
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw

        ma200  = close.rolling(200).mean()
        last_c = close.iloc[-1]
        last_ma = ma200.iloc[-1]
        valid   = last_c.notna() & last_ma.notna()
        if valid.sum() == 0:
            print("[BREADTH] Keine gültigen 200d-MA-Werte — Marktbreite NICHT "
                  "ermittelbar.")
            return None
        above = (last_c[valid] > last_ma[valid]).sum()
        anteil = round(float(above / valid.sum() * 100), 1)
        print(f"[BREADTH] {int(above)} von {int(valid.sum())} S&P-500-Titeln "
              f"über ihrer 200d-Linie ({anteil:.1f} %).")
        return anteil
    except Exception as e:
        print(f"[BREADTH] Abruf fehlgeschlagen ({e}) — Marktbreite NICHT "
              f"ermittelbar.")
        return None