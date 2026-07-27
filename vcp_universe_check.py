"""Full-Universe-Check: misst die echte VCP-Rate über das gesamte Universum.

Hintergrund: Nach dem Umbau von `detect_vcp` (Commit a7b625a) ist offen, wie
streng die Kalibrierung im vollen Universum (~2000 Titel) wirkt. Die Stichprobe
aus der Diagnose-Session (126 Titel) ergab 1,1 % Basen und 0,05 % Entries pro
Woche — zu wenig Datenpunkte, um `max_pullback`/`max_final_range` final
einzustellen.

Dieses Tool
  1. lädt das Universum + Wochenhistorie EINMAL und cacht sie lokal
     (Parameter-Sweeps laufen danach ohne Download),
  2. läuft walk-forward über die letzten N Wochen: für jede Woche wird der
     damalige Datenstand (`df.iloc[:len-k]`) durch `detect_vcp` geschickt,
  3. berichtet Basen/Woche, Entries/Woche, Basislängen-Verteilung und
     Beispiel-Entries,
  4. kann per `--sweep` ein Parameterraster durchmessen, um die Strenge
     einzustellen.

Nur lesend (yfinance über data_sources), kein Alpaca.

Beispiele
---------
  python vcp_universe_check.py                        # voller Lauf, 26 Wochen
  python vcp_universe_check.py --limit 300            # schneller Testlauf
  python vcp_universe_check.py --weeks-back 52        # 1 Jahr walk-forward
  python vcp_universe_check.py --sweep                # Parameterraster
  python vcp_universe_check.py --daily-volume         # Vol am Ausbruchstag
  python vcp_universe_check.py --max-pullback 0.25 --max-final-range 0.10
"""
from __future__ import annotations

import argparse
import itertools
import os
import pickle
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import SETTINGS
from data_sources import get_universe, load_daily_history, load_weekly_history
from detect_vcp import detect_vcp

# detect_vcp braucht ≥ 55 Wochenbars; Puffer für die walk-forward-Slices
MIN_BARS = 55
CACHE_TTL_DAYS = 3


# ─────────────────────────────────────────────────────────────────────────────
# Datenbeschaffung (mit lokalem Cache, damit Sweeps nicht neu downloaden)
# ─────────────────────────────────────────────────────────────────────────────
def _cache_path(weeks: int, top_n: int) -> Path:
    d = Path(SETTINGS.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"vcp_check_weekly_{top_n}_{weeks}w.pkl"


def load_history(weeks: int, limit: int | None, refresh: bool) -> dict[str, pd.DataFrame]:
    """Wochenhistorie fürs Universum — aus dem Cache, sonst frisch geladen."""
    path = _cache_path(weeks, SETTINGS.universe_top_n)

    if path.exists() and not refresh:
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days <= CACHE_TTL_DAYS:
            with path.open("rb") as fh:
                hist = pickle.load(fh)
            print(f"[CACHE] {len(hist)} Serien aus {path.name} "
                  f"(Alter {age_days:.1f} Tage)")
            return _apply_limit(hist, limit)
        print(f"[CACHE] {path.name} ist {age_days:.1f} Tage alt — lade neu.")

    universe = get_universe()
    print(f"[UNIVERSE] {len(universe)} Ticker")
    hist = load_weekly_history(universe, weeks=weeks)
    with path.open("wb") as fh:
        pickle.dump(hist, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[CACHE] {len(hist)} Serien nach {path.name} geschrieben")
    return _apply_limit(hist, limit)


def load_daily(tickers: list[str], refresh: bool) -> dict[str, pd.DataFrame]:
    """Tagesserien fürs Ausbruchsvolumen — eigener Cache, gleiche TTL.

    Nur Close/Volume, aber ~5× so groß wie die Wochenserien; deshalb getrennt
    gecacht, damit ein Lauf ohne `--daily-volume` sie gar nicht erst braucht.
    """
    d = Path(SETTINGS.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"vcp_check_daily_{SETTINGS.universe_top_n}.pkl"

    if path.exists() and not refresh:
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days <= CACHE_TTL_DAYS:
            with path.open("rb") as fh:
                daily = pickle.load(fh)
            print(f"[CACHE] {len(daily)} Tagesserien aus {path.name} "
                  f"(Alter {age_days:.1f} Tage)")
            return {t: daily[t] for t in tickers if t in daily}
        print(f"[CACHE] {path.name} ist {age_days:.1f} Tage alt — lade neu.")

    daily = load_daily_history(get_universe())
    with path.open("wb") as fh:
        pickle.dump(daily, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[CACHE] {len(daily)} Tagesserien nach {path.name} geschrieben")
    return {t: daily[t] for t in tickers if t in daily}


def _apply_limit(hist: dict[str, pd.DataFrame], limit: int | None) -> dict[str, pd.DataFrame]:
    """Zufallsstichprobe mit festem Seed — die CSV ist nach Marktkapitalisierung
    sortiert, ein einfaches Abschneiden würde nur Large Caps messen. Fester Seed,
    damit Sweep-Kombinationen dieselben Titel sehen.
    """
    if limit is None or limit >= len(hist):
        return hist
    keys = sorted(hist)
    picked = random.Random(42).sample(keys, limit)
    return {k: hist[k] for k in picked}


# ─────────────────────────────────────────────────────────────────────────────
# Messung
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Params:
    window: int = 60
    base_min: int = 7
    base_max: int = 30
    min_contraction: float = 0.70
    max_pullback: float = 0.20
    max_final_range: float = 0.08
    max_close_to_resistance: float = 0.05
    min_breakout_vol_ratio: float = 1.40
    waves: tuple[int, ...] = (4, 3)

    def as_kwargs(self) -> dict:
        return {
            "window": self.window,
            "base_min": self.base_min,
            "base_max": self.base_max,
            "min_contraction": self.min_contraction,
            "max_pullback": self.max_pullback,
            "max_final_range": self.max_final_range,
            "max_close_to_resistance": self.max_close_to_resistance,
            "min_breakout_vol_ratio": self.min_breakout_vol_ratio,
            "waves_to_try": tuple(self.waves),
        }

    def label(self) -> str:
        return (f"pb={self.max_pullback:.2f} fr={self.max_final_range:.2f} "
                f"con={self.min_contraction:.2f} vol={self.min_breakout_vol_ratio:.2f} "
                f"w={'/'.join(map(str, self.waves))}")


# Globals für die Worker-Prozesse (werden per initializer gesetzt)
_W_HIST: dict[str, pd.DataFrame] = {}
_W_DAILY: dict[str, pd.DataFrame] = {}
_W_KWARGS: dict = {}
_W_WEEKS_BACK: int = 0


def _worker_init(hist: dict[str, pd.DataFrame], daily: dict[str, pd.DataFrame],
                 kwargs: dict, weeks_back: int) -> None:
    global _W_HIST, _W_DAILY, _W_KWARGS, _W_WEEKS_BACK
    _W_HIST, _W_DAILY, _W_KWARGS, _W_WEEKS_BACK = hist, daily, kwargs, weeks_back


def _scan_ticker(ticker: str) -> list[tuple]:
    """Walk-forward über die letzten `weeks_back` Wochen eines Titels.

    Rückgabe: Liste von (week_offset, date, ticker, vcp, entry, base_weeks,
    waves, breakout_level) — nur Zeilen mit erkannter Basis.
    """
    return scan_ticker(ticker, _W_HIST[ticker], _W_KWARGS, _W_WEEKS_BACK,
                       _W_DAILY.get(ticker))


def trim_daily(daily: dict[str, pd.DataFrame], weeks_back: int,
               lookback: int = 50) -> dict[str, pd.DataFrame]:
    """Tagesserien auf das für den Walk-forward nötige Fenster kürzen.

    Gebraucht werden nur die gescannten Wochen plus der Volumen-Lookback davor.
    Ungekürzt würde jeder Worker-Prozess die volle 3-Jahres-Historie kopieren.
    """
    keep = weeks_back * 5 + lookback + 10
    return {t: d.tail(keep) for t, d in daily.items()}


def scan_ticker(ticker: str, df: pd.DataFrame, kwargs: dict, weeks_back: int,
                daily: pd.DataFrame | None = None) -> list[tuple]:
    hits: list[tuple] = []
    n = len(df)
    for k in range(weeks_back):
        end = n - k
        if end < MIN_BARS:
            break
        sl = df.iloc[:end]
        try:
            # detect_vcp schneidet `daily` selbst auf das Slice-Ende zu — die
            # volle Serie zu übergeben erzeugt kein Look-ahead.
            res = detect_vcp(sl, daily_df=daily, **kwargs)
        except Exception:
            continue
        if not res.get("VCP"):
            continue
        pivot = res.get("Breakout_Level")
        close = float(sl["Close"].iloc[-1])
        # Teilbedingungen des Entries getrennt protokollieren: hakt es am Preis
        # oder am Volumen?
        price_ok = bool(pivot and close > pivot * 1.005)

        # Volumenkriterium: Volumen der AUSBRUCHSWOCHE gegen den Basisdurchschnitt
        # OHNE diese Woche (seit dem Fix in detect_vcp die einzige Definition).
        # Das rohe Verhältnis wird mitgeschrieben, damit Schwellen ohne Neuscan
        # ausgewertet werden können.
        vol_ratio = float(res.get("Breakout_Vol_Ratio") or 0.0)
        vol_basis = str(res.get("Breakout_Vol_Basis") or "weekly")
        hits.append((
            k,
            sl.index[-1],
            ticker,
            True,
            bool(res.get("Entry_Signal")),
            int(res.get("Base_Weeks") or 0),
            int(res.get("Waves") or 0),
            pivot,
            close,
            price_ok,
            bool(res.get("Breakout_Volume")),
            vol_ratio,
            vol_basis,
        ))
    return hits


def run_scan(hist: dict[str, pd.DataFrame], params: Params, weeks_back: int,
             workers: int, quiet: bool = False,
             daily: dict[str, pd.DataFrame] | None = None) -> tuple[pd.DataFrame, int]:
    """Scannt alle Titel. Rückgabe: (Treffer-DataFrame, Anzahl gescannter Slices)."""
    kwargs = params.as_kwargs()
    daily = daily or {}
    tickers = [t for t, df in hist.items() if len(df) >= MIN_BARS]

    # Anzahl tatsächlich auswertbarer (Titel × Woche)-Slices
    slices = sum(min(weeks_back, max(0, len(hist[t]) - MIN_BARS + 1)) for t in tickers)

    rows: list[tuple] = []
    t0 = time.time()

    if workers <= 1:
        for i, t in enumerate(tickers, 1):
            rows.extend(scan_ticker(t, hist[t], kwargs, weeks_back, daily.get(t)))
            if not quiet and i % 200 == 0:
                print(f"  … {i}/{len(tickers)} ({time.time() - t0:.0f}s)")
    else:
        sub_hist = {t: hist[t] for t in tickers}
        sub_daily = {t: daily[t] for t in tickers if t in daily}
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(sub_hist, sub_daily, kwargs, weeks_back),
        ) as pool:
            for i, res in enumerate(pool.map(_scan_ticker, tickers, chunksize=25), 1):
                rows.extend(res)
                if not quiet and i % 400 == 0:
                    print(f"  … {i}/{len(tickers)} ({time.time() - t0:.0f}s)")

    cols = ["week_offset", "date", "ticker", "vcp", "entry",
            "base_weeks", "waves", "breakout_level", "close",
            "price_breakout", "vol_surge", "vol_ratio", "vol_basis"]
    df = pd.DataFrame(rows, columns=cols)
    if not quiet:
        print(f"[SCAN] {len(tickers)} Titel × ~{weeks_back} Wochen = {slices} Slices "
              f"in {time.time() - t0:.0f}s")
    return df, slices


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────
def summarize(df: pd.DataFrame, slices: int, n_tickers: int, weeks_back: int,
              params: Params) -> dict:
    bases = len(df)
    entries = int(df["entry"].sum()) if bases else 0
    weeks_measured = max(1, min(weeks_back, slices // max(1, n_tickers)))
    out = {
        "label": params.label(),
        "bases": bases,
        "entries": entries,
        "base_rate_pct": bases / slices * 100 if slices else 0.0,
        "entry_rate_pct": entries / slices * 100 if slices else 0.0,
        "bases_per_week": bases / weeks_measured,
        "entries_per_week": entries / weeks_measured,
    }
    # Entries/Woche bei alternativen Volumenschwellen (Ausbruchswoche vs. Basis-Ø)
    pb = df[df["price_breakout"]] if bases else df
    for thr in (1.0, 1.2, 1.4):
        n_thr = int((pb["vol_ratio"] >= thr).sum()) if bases else 0
        out[f"entries_w_vol{thr:.1f}"] = n_thr / weeks_measured
    return out


def print_report(df: pd.DataFrame, slices: int, n_tickers: int, weeks_back: int,
                 params: Params) -> None:
    s = summarize(df, slices, n_tickers, weeks_back, params)

    print("\n" + "=" * 72)
    print(f"VCP-Full-Universe-Check   {datetime.now():%Y-%m-%d %H:%M}")
    print(f"Parameter: {params.label()}")
    print(f"Titel: {n_tickers}   Wochen rückwärts: {weeks_back}   Slices: {slices}")
    print("-" * 72)
    print(f"  Basen erkannt        : {s['bases']:6d}  ({s['base_rate_pct']:.2f} % der Slices)")
    print(f"  Entry-Signale        : {s['entries']:6d}  ({s['entry_rate_pct']:.3f} % der Slices)")
    print(f"  ⇒ Basen  / Woche     : {s['bases_per_week']:8.1f}")
    print(f"  ⇒ Entries/ Woche     : {s['entries_per_week']:8.1f}")
    print("=" * 72)

    if df.empty:
        print("\n⚠️  Keine einzige Basis erkannt — Kalibrierung zu streng.")
        return

    # Wo bricht der Trichter Basis → Entry ab?
    p_ok = int(df["price_breakout"].sum())
    v_ok = int(df["vol_surge"].sum())
    print("\nTrichter Basis → Entry:")
    print(f"  Basen                        : {len(df):5d}")
    print(f"  davon Preis > Pivot·1.005    : {p_ok:5d}  ({p_ok / len(df) * 100:.1f} %)")
    print(f"  davon Volumen-Surge ≥{params.min_breakout_vol_ratio:.2f}×   : "
          f"{v_ok:5d}  ({v_ok / len(df) * 100:.1f} %)")
    print(f"  davon BEIDES (= Entry)       : {s['entries']:5d}  "
          f"({s['entries'] / len(df) * 100:.1f} %)")
    near = df[(~df["price_breakout"]) &
              (df["close"] >= df["breakout_level"] * 0.97)]
    print(f"  Watchlist (≤3 % unter Pivot) : {len(near):5d}  "
          f"({len(near) / max(1, weeks_back):.1f} / Woche)")

    # Sensitivität der Volumenschwelle (ohne Neuscan, aus dem rohen Verhältnis)
    pb = df[df["price_breakout"]]
    n_daily = int((pb["vol_basis"] == "daily").sum()) if not pb.empty else 0
    print("\nVolumenschwelle — Messbasis bei Preis-Ausbruch: "
          f"{n_daily} Tages-, {len(pb) - n_daily} Wochenmessung")
    if not pb.empty:
        print(f"  Vol-Ratio bei Preis-Ausbruch : Median {pb['vol_ratio'].median():.2f}×, "
              f"Q25 {pb['vol_ratio'].quantile(.25):.2f}×, "
              f"Q75 {pb['vol_ratio'].quantile(.75):.2f}×")
        for thr in (1.0, 1.2, 1.4, 1.6):
            n_thr = int((pb["vol_ratio"] >= thr).sum())
            print(f"    Schwelle {thr:.1f}× → {n_thr:4d} Entries "
                  f"({n_thr / max(1, weeks_back):.1f} / Woche)")

    print("\nBasislängen (Wochen) — Verteilung:")
    dist = df["base_weeks"].value_counts().sort_index()
    for weeks, cnt in dist.items():
        bar = "█" * max(1, int(cnt / max(1, dist.max()) * 40))
        print(f"  {weeks:2d}W  {cnt:5d}  {bar}")
    print(f"  Median {df['base_weeks'].median():.0f}W, "
          f"Mittel {df['base_weeks'].mean():.1f}W")

    print("\nWellenzahl:")
    for w, cnt in df["waves"].value_counts().sort_index().items():
        print(f"  {w} Wellen: {cnt}")

    print("\nSignale je Woche (0 = aktuelle Woche):")
    per_week = df.groupby("week_offset").agg(
        datum=("date", "max"), basen=("vcp", "sum"), entries=("entry", "sum"))
    for off, row in per_week.head(weeks_back).iterrows():
        d = pd.Timestamp(row["datum"]).date()
        print(f"  -{off:2d}W  {d}  Basen {int(row['basen']):4d}  Entries {int(row['entries']):3d}")

    ent = df[df["entry"]].sort_values("week_offset")
    if not ent.empty:
        print(f"\nEntry-Signale ({len(ent)}), neueste zuerst:")
        for _, r in ent.head(40).iterrows():
            print(f"  -{int(r['week_offset']):2d}W  {pd.Timestamp(r['date']).date()}  "
                  f"{r['ticker']:6}  Basis {int(r['base_weeks'])}W  "
                  f"{int(r['waves'])} Wellen  Pivot {r['breakout_level']:.2f}")
        if len(ent) > 40:
            print(f"  … {len(ent) - 40} weitere")

    cur = df[df["week_offset"] == 0]
    print(f"\nAktuelle Woche: {len(cur)} Basen, {int(cur['entry'].sum())} Entries")
    if not cur.empty:
        print("  " + ", ".join(sorted(cur["ticker"].tolist())[:40]))


# ─────────────────────────────────────────────────────────────────────────────
# Sweep
# ─────────────────────────────────────────────────────────────────────────────
SWEEP_GRID = {
    "max_pullback": [0.20, 0.25, 0.30],
    "max_final_range": [0.08, 0.10, 0.12],
    "min_contraction": [0.70, 0.80],
}


def run_sweep(hist: dict[str, pd.DataFrame], base: Params, weeks_back: int,
              workers: int, daily: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    combos = list(itertools.product(*SWEEP_GRID.values()))
    keys = list(SWEEP_GRID.keys())
    print(f"[SWEEP] {len(combos)} Kombinationen × {len(hist)} Titel × {weeks_back} Wochen\n")

    out = []
    for i, values in enumerate(combos, 1):
        p = Params(**{**base.__dict__, **dict(zip(keys, values))})
        df, slices = run_scan(hist, p, weeks_back, workers, quiet=True, daily=daily)
        n_tickers = sum(1 for d in hist.values() if len(d) >= MIN_BARS)
        s = summarize(df, slices, n_tickers, weeks_back, p)
        s.update(dict(zip(keys, values)))
        out.append(s)
        print(f"  [{i:2d}/{len(combos)}] {p.label():44}  "
              f"Basen/W {s['bases_per_week']:7.1f}   Entries/W {s['entries_per_week']:6.2f}   "
              f"(Vol1.2× {s['entries_w_vol1.2']:5.2f}  Vol1.0× {s['entries_w_vol1.0']:5.2f})")

    res = pd.DataFrame(out)
    print("\n" + "=" * 72)
    print("SWEEP-ERGEBNIS (sortiert nach Entries/Woche)")
    print("Entries/W = Preis-Ausbruch + Volumenschwelle aus --min-breakout-vol-ratio;")
    print("entries_w_volX = dieselbe Basis-Menge bei Schwelle X (ohne Neuscan)")
    print("=" * 72)
    cols = keys + ["bases_per_week", "entries_per_week",
                   "entries_w_vol1.4", "entries_w_vol1.2", "entries_w_vol1.0"]
    print(res.sort_values("entries_per_week", ascending=False)[cols].to_string(index=False))
    return res


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    # Windows-Konsole ist cp1252 — Report enthält ≥, ⇒, █
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weeks-back", type=int, default=26,
                    help="Anzahl Wochen walk-forward (Default 26)")
    ap.add_argument("--history-weeks", type=int, default=156,
                    help="Länge der geladenen Wochenhistorie (Default 156 = 3J)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Nur die ersten N Titel (Testlauf)")
    ap.add_argument("--refresh", action="store_true", help="Datencache verwerfen")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--sweep", action="store_true", help="Parameterraster durchmessen")
    ap.add_argument("--csv", type=str, default=None, help="Treffer als CSV speichern")

    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--base-min", type=int, default=7)
    ap.add_argument("--base-max", type=int, default=30)
    ap.add_argument("--min-contraction", type=float, default=0.70)
    ap.add_argument("--max-pullback", type=float, default=0.20)
    ap.add_argument("--max-final-range", type=float, default=0.08)
    ap.add_argument("--max-close-to-resistance", type=float, default=0.05)
    ap.add_argument("--min-breakout-vol-ratio", type=float, default=1.40,
                    help="Volumen des Ausbruchs / Vergleichs-Ø (Default 1.40)")
    ap.add_argument("--daily-volume", action="store_true",
                    help="Ausbruchsvolumen am Ausbruchstag gegen den 50-Tage-Ø "
                         "messen statt an der ganzen Woche (laedt Tagesserien)")
    ap.add_argument("--waves", type=str, default="4,3",
                    help="Wellenzahlen in Testreihenfolge, z. B. '4,3' oder '4,3,2'")
    args = ap.parse_args()

    params = Params(
        window=args.window,
        base_min=args.base_min,
        base_max=args.base_max,
        min_contraction=args.min_contraction,
        max_pullback=args.max_pullback,
        max_final_range=args.max_final_range,
        max_close_to_resistance=args.max_close_to_resistance,
        min_breakout_vol_ratio=args.min_breakout_vol_ratio,
        waves=tuple(int(x) for x in args.waves.split(",") if x.strip()),
    )

    hist = load_history(args.history_weeks, args.limit, args.refresh)
    if not hist:
        print("[ERROR] Keine Historie geladen.")
        return 1

    usable = {t: d for t, d in hist.items() if len(d) >= MIN_BARS}
    print(f"[DATA] {len(usable)} von {len(hist)} Titeln mit ≥{MIN_BARS} Wochenbars")

    daily: dict[str, pd.DataFrame] = {}
    if args.daily_volume:
        daily = trim_daily(load_daily(sorted(usable), args.refresh), args.weeks_back)
        print(f"[DATA] {len(daily)} Tagesserien fuers Ausbruchsvolumen")
    print()

    if args.sweep:
        run_sweep(usable, params, args.weeks_back, args.workers, daily=daily)
        return 0

    df, slices = run_scan(usable, params, args.weeks_back, args.workers, daily=daily)
    print_report(df, slices, len(usable), args.weeks_back, params)

    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\n[CSV] {len(df)} Zeilen → {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
