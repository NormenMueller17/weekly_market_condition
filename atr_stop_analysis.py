"""Schlüsselt Frühausstoppungen nach der Volatilität des Titels auf.

Anlass (2026-07-27): Im Tradetagebuch wurden 5 von 16 Positionen nach 2–6 Tagen
ausgestoppt (MU nach 2 Tagen, VICR nach 2, RVMD nach 4), und ausgerechnet diese
Titel liefen danach stark (Alpha-Median +12,8 % nach 4 Wochen). Verdacht: Der
Stop liegt innerhalb der normalen Schwankungsbreite dieser Titel, trifft also
Rauschen statt gescheiterter Trades.

`signal_generator._stop_vcp` setzt den Stop auf 2× ATR unter dem Breakout-Level
(Floor 15 % darunter), gedeckelt durch `rules.json: max_stop_pct = 20`. ATR ist
dabei ATR14 auf TAGESdaten. Bei einem Titel mit 5 % Tages-ATR sind 2× ATR die
normale Zwei-Tages-Bewegung — die Frage ist also, ob die Stop-Weite mit der
Volatilität mitwächst oder ob volatile Titel systematisch früher rausfliegen.

Dieses Tool misst auf den Backtest-Entries (ordentliches n, im Gegensatz zu den
9 auswertbaren Journal-Trades):
  * ATR14 % zum Zeitpunkt des Entries
  * den Stop nach Systemregel und wann er triggert (Woche 1 / 2–4 / 5–13)
  * was die früh Ausgestoppten danach gemacht hätten

Tages-OHLC wird nur für die betroffenen Ticker geladen (nicht fürs Universum)
und gecacht.

  python atr_stop_analysis.py --csv .cache/vcp_forward.csv
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from config import SETTINGS
from data_sources import download_ohlcv_batched
from experiment_log import log_experiment
from vcp_universe_check import CACHE_TTL_DAYS, load_history

ATR_WINDOW = 14
STOP_ATR_MULT = 2.0        # signal_generator._stop_vcp
STOP_FLOOR_PCT = 0.15      # ebenda: nie mehr als 15 % unter Breakout
STOP_CAP_PCT = 0.20        # rules.json: max_stop_pct


def load_ohlc(tickers: list[str], refresh: bool) -> dict[str, pd.DataFrame]:
    """Tages-OHLC nur für die gebrauchten Ticker — ATR braucht High/Low, die im
    universumsweiten Tages-Cache (nur Close/Volume) nicht enthalten sind.
    """
    d = Path(SETTINGS.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "atr_ohlc.pkl"

    cached: dict[str, pd.DataFrame] = {}
    if path.exists() and not refresh:
        age = (time.time() - path.stat().st_mtime) / 86400
        if age <= CACHE_TTL_DAYS:
            with path.open("rb") as fh:
                cached = pickle.load(fh)
            missing = [t for t in tickers if t not in cached]
            if not missing:
                print(f"[CACHE] {len(cached)} OHLC-Serien aus {path.name}")
                return {t: cached[t] for t in tickers if t in cached}
            print(f"[CACHE] {len(cached)} vorhanden, {len(missing)} fehlen — lade nach")
            tickers = missing

    batched = download_ohlcv_batched(tickers=tickers, period="3y", interval="1d",
                                     chunk_size=40, auto_adjust=False, threads=False)
    for t, sub in batched.items():
        if sub is None or sub.empty:
            continue
        if not {"High", "Low", "Close"}.issubset(sub.columns):
            continue
        s = sub[["High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
        if not s.empty:
            cached[t] = s

    with path.open("wb") as fh:
        pickle.dump(cached, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[CACHE] {len(cached)} OHLC-Serien in {path.name}")
    return cached


def _sizing_rules() -> dict:
    """max_risk_per_trade_pct / max_position_pct aus rules.json (als Bruch)."""
    try:
        s = json.loads((Path(__file__).parent / "rules.json")
                       .read_text(encoding="utf-8")).get("sizing", {})
    except Exception:
        s = {}
    return {"risk": s.get("max_risk_per_trade_pct", 1.5) / 100.0,
            "pos":  s.get("max_position_pct",      15.0) / 100.0}


def atr_pct_at(ohlc: pd.DataFrame, date: pd.Timestamp) -> float:
    """ATR14 in % des Schlusskurses, Stand `date` — exakt wie screener.py, und
    strikt nur mit Daten BIS zu diesem Tag.
    """
    idx = pd.to_datetime(ohlc.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    sub = ohlc[idx <= pd.Timestamp(date)]
    if len(sub) < ATR_WINDOW + 1:
        return float("nan")

    high, low, close = sub["High"], sub["Low"], sub["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(ATR_WINDOW).mean().iloc[-1]
    last = float(close.iloc[-1])
    if not np.isfinite(atr) or last <= 0:
        return float("nan")
    return float(atr) / last * 100.0


def system_stop_pct(atr_pct: float, mult: float = STOP_ATR_MULT,
                    min_pct: float = 0.0, max_pct: float = STOP_CAP_PCT) -> float:
    """Stop-Abstand in Prozent nach Systemregel (Näherung: Entry ≈ Breakout,
    der Buy-Stop-Puffer betraegt nur 0,1 %).

    `min_pct` ist die untere Schranke, die `signal_generator` als einheitlichen
    Stop-Floor ueber alle Muster-Pfade kennt; `max_pct` der Cap aus rules.json.
    """
    if not np.isfinite(atr_pct):
        return float("nan")
    pct = mult * atr_pct / 100.0
    return float(min(max(pct, min_pct), max_pct))


def analyse(entries: pd.DataFrame, hist: dict[str, pd.DataFrame],
            ohlc: dict[str, pd.DataFrame], horizon: int,
            mult: float = STOP_ATR_MULT, min_pct: float = 0.0,
            max_pct: float = STOP_CAP_PCT) -> pd.DataFrame:
    rows = []
    for _, r in entries.iterrows():
        sym = r["ticker"]
        wk, oh = hist.get(sym), ohlc.get(sym)
        if wk is None or oh is None:
            continue
        idx = pd.to_datetime(wk.index)
        pos = int(idx.searchsorted(pd.Timestamp(r["date"])))
        if pos + horizon >= len(wk):
            continue

        entry_px = float(r["entry_px"])
        atr = atr_pct_at(oh, pd.Timestamp(r["date"]))
        stop_pct = system_stop_pct(atr, mult, min_pct, max_pct)
        if not np.isfinite(stop_pct) or entry_px <= 0:
            continue

        low = wk["Low"].astype(float).iloc[pos + 1:pos + horizon + 1]
        close = wk["Close"].astype(float).iloc[pos + 1:pos + horizon + 1]
        stop_px = entry_px * (1 - stop_pct)

        below = np.where(low.to_numpy() <= stop_px)[0]
        trig_week = int(below[0]) + 1 if len(below) else None

        # Rendite OHNE Stop (nur fuer die "was waere danach passiert"-Frage)
        ret_raw = float(close.iloc[-1] / entry_px - 1) * 100

        # Rendite MIT Stop: getriggert -> Verlust in Stop-Hoehe, sonst Horizont.
        # Naeherung ohne Gap-Slippage; ueberschaetzt weite Stops leicht nicht,
        # sondern behandelt alle Kalibrierungen gleich optimistisch.
        ret_stop = -stop_pct * 100 if trig_week is not None else ret_raw

        # Entscheidende Groesse: Unter Risk-first-Sizing skaliert die Position
        # mit 1/stop_pct. Ein weiter Stop kauft also weniger Stueck. Nur das
        # R-Multiple (Rendite je Risikoeinheit) ist zwischen Kalibrierungen
        # vergleichbar — die reine Prozentrendite ist es NICHT.
        r_mult = ret_stop / (stop_pct * 100) if stop_pct > 0 else np.nan

        rows.append({
            "ticker": sym, "date": r["date"], "atr_pct": atr,
            "stop_pct": stop_pct * 100,
            "trig_week": trig_week,
            "frueh": bool(trig_week == 1),
            "ret": ret_raw,
            "ret_stop": ret_stop,
            "r_mult": r_mult,
        })
    return pd.DataFrame(rows)


def portfolio_contribution(res: pd.DataFrame, max_risk_pct: float,
                           max_pos_pct: float) -> pd.Series:
    """Beitrag eines Trades zur Depotrendite in Prozentpunkten.

    Spiegelt den echten Sizing-Pfad aus `signal_generator.generate_signals`:

        pos_pct = min(max_risk_pct / stop_pct, max_pos_pct)

    Das ist der Grund, warum das rohe R-Multiple hier in die Irre fuehrt: Es
    unterstellt, ein enger Stop liesse sich in beliebig viel Stueck umsetzen.
    Sobald `stop_pct < max_risk_pct / max_pos_pct` ist, bindet aber der
    Positions-Cap — die Position waechst nicht weiter, das reale Risiko sinkt
    unter das Ziel, und der R-Vorteil des engen Stops ist nicht abrufbar.
    """
    stop_frac = res.stop_pct / 100.0
    pos_pct = np.minimum(max_risk_pct / stop_frac.replace(0, np.nan), max_pos_pct)
    return pos_pct * res.ret_stop


def sweep(entries: pd.DataFrame, hist: dict[str, pd.DataFrame],
          ohlc: dict[str, pd.DataFrame], horizon: int,
          mults: list[float], mins: list[float], max_pct: float,
          max_risk_pct: float, max_pos_pct: float) -> pd.DataFrame:
    """Gitter ueber (ATR-Multiplikator, Stop-Floor), bewertet nach Depotbeitrag."""
    rows = []
    for m in mults:
        for lo in mins:
            res = analyse(entries, hist, ohlc, horizon, m, lo, max_pct)
            if res.empty:
                continue
            contrib = portfolio_contribution(res, max_risk_pct, max_pos_pct)
            stop_frac = res.stop_pct / 100.0
            pos_pct = np.minimum(max_risk_pct / stop_frac.replace(0, np.nan),
                                 max_pos_pct)
            rows.append({
                "mult": m, "min_pct": lo * 100,
                "n": len(res),
                "stop_med": res.stop_pct.median(),
                "pos_med_%": pos_pct.median() * 100,
                "cap_bindet_%": (pos_pct >= max_pos_pct).mean() * 100,
                "wk1_%": res.frueh.mean() * 100,
                "gestoppt_%": res.trig_week.notna().mean() * 100,
                "ret_mean": res.ret_stop.mean(),
                "depot_pp": contrib.mean(),
                "R_mean": res.r_mult.mean(),
            })
    return pd.DataFrame(rows)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=".cache/vcp_forward.csv")
    ap.add_argument("--horizon", type=int, default=13)
    ap.add_argument("--journal", default="docs/data/trades.json")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--atr-mult", type=float, default=STOP_ATR_MULT,
                    help="ATR-Multiplikator fuer den Stop-Abstand")
    ap.add_argument("--min-stop-pct", type=float, default=0.0,
                    help="einheitlicher Stop-Floor in %% (0 = keiner)")
    ap.add_argument("--max-stop-pct", type=float, default=STOP_CAP_PCT * 100,
                    help="Cap in %% (rules.json: max_stop_pct)")
    ap.add_argument("--sweep", action="store_true",
                    help="Gitter ueber Multiplikator x Floor, bewertet nach R-Multiple")
    args = ap.parse_args()

    mult    = args.atr_mult
    min_pct = args.min_stop_pct / 100.0
    max_pct = args.max_stop_pct / 100.0

    entries = pd.read_csv(args.csv).drop_duplicates(subset=["ticker", "date"])
    hist = load_history(156, None, False)
    ohlc = load_ohlc(sorted(entries["ticker"].unique()), args.refresh)

    if args.sweep:
        _sz = _sizing_rules()
        sw = sweep(entries, hist, ohlc, args.horizon,
                   mults=[1.5, 2.0, 2.5, 3.0, 4.0],
                   mins=[0.0, 0.08, 0.10, 0.12, 0.15],
                   max_pct=max_pct,
                   max_risk_pct=_sz["risk"], max_pos_pct=_sz["pos"])
        print(f"\nSweep ueber {args.horizon}W  (Cap {args.max_stop_pct:.0f} %, "
              f"Risiko {_sz['risk'] * 100:.1f} %, Positions-Cap {_sz['pos'] * 100:.0f} %)")
        print("depot_pp = Beitrag zur Depotrendite in Prozentpunkten je Trade —\n"
              "die einzige Groesse, die den Positions-Cap mit einrechnet.\n")
        print(sw.sort_values("depot_pp", ascending=False).round(2).to_string(index=False))
        best = sw.loc[sw.depot_pp.idxmax()]
        print(f"\nBeste Kalibrierung nach Depotbeitrag: mult={best['mult']} "
              f"floor={best['min_pct']:.0f} %  ->  {best['depot_pp']:.3f} pp/Trade "
              f"(Stop-Median {best['stop_med']:.1f} %, gestoppt {best['gestoppt_%']:.0f} %)")
        return 0

    res = analyse(entries, hist, ohlc, args.horizon, mult, min_pct, max_pct)
    if res.empty:
        print("[ERROR] Keine auswertbaren Entries.")
        return 1

    print(f"\n{len(res)} Entries mit ATR und {args.horizon}W Zukunft")
    print(f"ATR14: Median {res.atr_pct.median():.2f} %   "
          f"Q25 {res.atr_pct.quantile(.25):.2f} %   Q75 {res.atr_pct.quantile(.75):.2f} %")
    print(f"Stop nach Systemregel: Median {res.stop_pct.median():.1f} % unter Entry "
          f"(mult={mult}, floor={args.min_stop_pct:.0f} %, cap={args.max_stop_pct:.0f} %)")
    print(f"gestoppt {res.trig_week.notna().mean() * 100:.0f} %  |  "
          f"Rendite mit Stop: Median {res.ret_stop.median():.2f} %  "
          f"Ø {res.ret_stop.mean():.2f} %  |  R-Multiple Ø {res.r_mult.mean():.3f}")

    print("\n" + "=" * 76)
    print("Frueh-Ausstoppung (Woche 1) nach ATR-Quartil")
    print("=" * 76)
    res["atr_q"] = pd.qcut(res["atr_pct"], 4,
                           labels=["Q1 ruhig", "Q2", "Q3", "Q4 volatil"])
    print(f"{'ATR-Quartil':<12} {'n':>4} {'ATR-Ø':>7} {'Stop-Ø':>8} "
          f"{'Woche-1-Stop':>13} {'je gestoppt':>12} {'Ø Rendite':>10}")
    print("-" * 76)
    for q, g in res.groupby("atr_q", observed=True):
        gestoppt = g["trig_week"].notna()
        print(f"{str(q):<12} {len(g):>4} {g.atr_pct.mean():>6.2f}% "
              f"{g.stop_pct.mean():>7.1f}% {g.frueh.mean() * 100:>12.0f}% "
              f"{gestoppt.mean() * 100:>11.0f}% {g.ret.mean():>9.2f}%")

    print("\n" + "=" * 76)
    print("Was haetten die Frueh-Ausgestoppten (Woche 1) danach gemacht?")
    print("=" * 76)
    fr, rest = res[res.frueh], res[~res.frueh]
    if not fr.empty:
        print(f"  frueh gestoppt (n={len(fr):3d}): {args.horizon}W-Rendite "
              f"Median {fr.ret.median():6.2f} %  Ø {fr.ret.mean():6.2f} %  "
              f"positiv {(fr.ret > 0).mean() * 100:3.0f} %")
    print(f"  uebrige       (n={len(rest):3d}): {args.horizon}W-Rendite "
          f"Median {rest.ret.median():6.2f} %  Ø {rest.ret.mean():6.2f} %  "
          f"positiv {(rest.ret > 0).mean() * 100:3.0f} %")

    # Gegenprobe am Journal: dieselbe Rechnung auf den echten Trades
    try:
        with open(args.journal, encoding="utf-8") as fh:
            data = json.load(fh)
        jt = pd.DataFrame([t for t in data.get("closed", [])
                           if t.get("exit_reason") == "stop_hit"])
        if not jt.empty:
            jt["entry_date"] = pd.to_datetime(jt["entry_date"])
            jo = load_ohlc(sorted(jt["symbol"].unique()), False)
            jt["atr_pct"] = [atr_pct_at(jo[s], d) if s in jo else np.nan
                             for s, d in zip(jt.symbol, jt.entry_date)]
            jt["haltetage"] = (pd.to_datetime(jt.exit_date) - jt.entry_date).dt.days
            print("\n" + "=" * 76)
            print("Gegenprobe: die realen Trades aus dem Journal")
            print("=" * 76)
            print(jt[["symbol", "atr_pct", "haltetage", "realized_plpc"]]
                  .sort_values("haltetage").round(2).to_string(index=False))
            fast = jt[jt.haltetage <= 7]
            if not fast.empty:
                print(f"\n  <= 7 Tage gehalten (n={len(fast)}): ATR-Median "
                      f"{fast.atr_pct.median():.2f} %  |  laenger gehalten: "
                      f"{jt[jt.haltetage > 7].atr_pct.median():.2f} %")
    except Exception as e:
        print(f"\n[WARN] Journal-Gegenprobe uebersprungen: {e}")

    log_experiment(
        tool="atr_stop_analysis",
        params={"horizon": args.horizon, "stop_atr_mult": mult,
                "min_stop_pct": args.min_stop_pct,
                "max_stop_pct": args.max_stop_pct},
        metrics={"n": len(res), "atr_median": float(res.atr_pct.median()),
                 "stop_pct_median": float(res.stop_pct.median()),
                 "week1_stop_pct": float(res.frueh.mean() * 100),
                 "ret_stop_mean": float(res.ret_stop.mean()),
                 "r_mult_mean": float(res.r_mult.mean()),
                 "frueh_ret_median": float(fr.ret.median()) if not fr.empty else np.nan,
                 "rest_ret_median": float(rest.ret.median())},
        context={"csv": args.csv},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
