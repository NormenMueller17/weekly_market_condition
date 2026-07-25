"""Diagnose: feuert die VCP-/Launchpad-Erkennung überhaupt?

Hintergrund: 0/72 je erzeugte Signale hatten ein Pattern (breakout_level=None),
weil require_pattern=False. Offene Frage: Liegt das nur an require_pattern, oder
erkennt detect_vcp/detect_launchpad grundsätzlich keine Muster? Diese Diagnose
läuft die Erkennung über eine Stichprobe und trennt:
  - Basis erkannt:  VCP=True / Launchpad=True
  - Entry-Trigger:  Entry_Signal=True / Launchpad_Entry=True

Nur lesend (yfinance), kein Alpaca.
"""
import sys
import pandas as pd
import yfinance as yf
from detect_vcp import detect_vcp
from launchpad_detection import detect_launchpad

# Stichprobe: liquide Large/Mid-Caps quer über Sektoren + unsere gehandelten Namen
SAMPLE = [
    # gehandelt (Journal)
    "ON","STX","VSH","GLW","NUE","TTMI","SANM","MU","KALU","HLIT","RVMD","CYTK",
    "VICR","ASTH","AAON","SKYT","CRDO","AVNS","QCRH","NTRA","SUN",
    # breiter Markt (liquide Trendtitel)
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","AVGO","AMD","NFLX","TSLA","LLY",
    "JPM","V","MA","COST","WMT","UNH","XOM","CVX","HD","ORCL","CRM","ADBE","NOW",
    "PANW","SNPS","CDNS","KLAC","LRCX","ASML","TSM","MRVL","MPWR","ANET","SMCI",
    "CEG","VST","GEV","HWM","URI","PWR","PH","ETN","CAT","DE","BKNG","AXP","GS",
    "MS","SPGI","ISRG","VRTX","REGN","BSX","MDT","TMO","ABBV","MRK","AMGN","GILD",
    "PGR","CB","MMC","APH","TDG","FTNT","CRWD","ZS","DDOG","NET","SNOW","MDB",
    "TTD","APP","CVNA","DASH","ABNB","UBER","SHOP","PLTR","COIN","HOOD","RCL",
    "CCL","DAL","UAL","LULU","DECK","CMG","ORLY","AZO","POOL","WING","TXRH",
    "FICO","MSCI","MCO","NDAQ","ICE","KKR","BX","APO","ARES","WELL","PLD","AMT",
]

def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(SAMPLE)
    tickers = SAMPLE[:limit]
    print(f"Lade Tagesdaten (3J) für {len(tickers)} Titel …\n")

    rows = []
    for i, t in enumerate(tickers, 1):
        try:
            df = yf.download(t, period="3y", interval="1d",
                             auto_adjust=True, progress=False, threads=False)
            if df is None or df.empty or len(df) < 300:
                rows.append((t, "no_data", False, False, False, False, None))
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            dfw = df.resample("W-FRI").agg(
                {"Close": "last", "High": "max", "Low": "min", "Volume": "sum"}).dropna()
            v = detect_vcp(dfw, window=60)
            l = detect_launchpad(dfw)
            rows.append((
                t, "ok",
                bool(v.get("VCP", False)),
                bool(v.get("Entry_Signal", False)),
                bool(l.get("Launchpad", False)),
                bool(l.get("Launchpad_Entry", False)),
                v.get("Breakout_Level"),
            ))
        except Exception as e:
            rows.append((t, f"err:{type(e).__name__}", False, False, False, False, None))
        if i % 20 == 0:
            print(f"  … {i}/{len(tickers)}")

    ok = [r for r in rows if r[1] == "ok"]
    n = len(ok)
    vcp_base   = sum(r[2] for r in ok)
    vcp_entry  = sum(r[3] for r in ok)
    lp_base    = sum(r[4] for r in ok)
    lp_entry   = sum(r[5] for r in ok)
    any_entry  = sum((r[3] or r[5]) for r in ok)

    print("\n" + "=" * 60)
    print(f"Auswertbare Titel: {n} / {len(rows)}")
    print(f"  VCP-Basis erkannt      : {vcp_base:3d}  ({vcp_base/n*100:.0f}%)")
    print(f"  VCP-Entry (Trigger)    : {vcp_entry:3d}  ({vcp_entry/n*100:.0f}%)")
    print(f"  Launchpad-Basis erkannt: {lp_base:3d}  ({lp_base/n*100:.0f}%)")
    print(f"  Launchpad-Entry        : {lp_entry:3d}  ({lp_entry/n*100:.0f}%)")
    print(f"  IRGENDEIN Entry-Signal : {any_entry:3d}  ({any_entry/n*100:.0f}%)")
    print("=" * 60)

    bases = [r for r in ok if r[2] or r[4]]
    if bases:
        print("\nTitel mit erkannter Basis (VCP/LP), Entry-Flags:")
        for r in bases[:25]:
            print(f"  {r[0]:6} VCP={r[2]!s:5} VCPentry={r[3]!s:5} "
                  f"LP={r[4]!s:5} LPentry={r[5]!s:5} bl={r[6]}")
    else:
        print("\n⚠️  KEIN einziger Titel mit erkannter Basis — Erkennung feuert nicht.")

    noda = [r[0] for r in rows if r[1] != "ok"]
    if noda:
        print(f"\nNicht auswertbar ({len(noda)}): {', '.join(noda)}")


if __name__ == "__main__":
    main()
