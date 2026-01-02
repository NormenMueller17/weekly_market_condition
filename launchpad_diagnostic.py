"""
Launchpad Diagnostic Tool

Analysiert die Launchpad-Ergebnisse und hilft Parameter zu tunen.
Zeigt Statistiken und identifiziert False Positives.

Usage:
    python launchpad_diagnostic.py
"""

import pandas as pd
import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt
import seaborn as sns


def analyze_launchpad_results(excel_path: str = None):
    """
    Analysiert die Launchpad-Ergebnisse aus dem Excel-Report.
    
    Parameters
    ----------
    excel_path : str
        Pfad zum Excel-Report (z.B. "artifacts/market_leaders_2025-01-02.xlsx")
        Wenn None, nimmt den neuesten Report
    """
    
    if excel_path is None:
        # Finde neuesten Report
        artifacts_dir = Path("artifacts")
        if not artifacts_dir.exists():
            print("❌ Artifacts-Verzeichnis nicht gefunden!")
            return
        
        excel_files = list(artifacts_dir.glob("market_leaders_*.xlsx"))
        if not excel_files:
            print("❌ Keine Excel-Reports gefunden!")
            return
        
        excel_path = max(excel_files, key=lambda p: p.stat().st_mtime)
        print(f"📊 Analysiere: {excel_path}")
    
    # Excel laden
    df = pd.read_excel(excel_path, sheet_name="Leaders")
    
    print("\n" + "="*70)
    print("LAUNCHPAD DIAGNOSTIC REPORT")
    print("="*70)
    
    # =========================================================================
    # 1. BASIC STATISTICS
    # =========================================================================
    print("\n📊 BASIC STATISTICS")
    print("-" * 70)
    
    total_stocks = len(df)
    launchpad_stocks = df[df["Launchpad"] == True] if "Launchpad" in df.columns else pd.DataFrame()
    launchpad_count = len(launchpad_stocks)
    
    print(f"Total Stocks:          {total_stocks}")
    print(f"Launchpad Detected:    {launchpad_count} ({launchpad_count/total_stocks*100:.1f}%)")
    
    if "VCP" in df.columns:
        vcp_count = len(df[df["VCP"] == True])
        both_count = len(df[(df["VCP"] == True) & (df["Launchpad"] == True)])
        print(f"VCP Detected:          {vcp_count} ({vcp_count/total_stocks*100:.1f}%)")
        print(f"Both Patterns:         {both_count} ({both_count/total_stocks*100:.1f}%)")
    
    if launchpad_count == 0:
        print("\n⚠️  Keine Launchpad-Setups gefunden!")
        return
    
    # =========================================================================
    # 2. PARAMETER DISTRIBUTION ANALYSIS
    # =========================================================================
    print("\n📈 PARAMETER DISTRIBUTION (Launchpad-Stocks)")
    print("-" * 70)
    
    if "Launchpad Score" in launchpad_stocks.columns:
        scores = launchpad_stocks["Launchpad Score"].dropna()
        print(f"\nLaunchpad Score:")
        print(f"  Mean:    {scores.mean():.1f}")
        print(f"  Median:  {scores.median():.1f}")
        print(f"  Min:     {scores.min():.1f}")
        print(f"  Max:     {scores.max():.1f}")
        print(f"  Std:     {scores.std():.1f}")
        
        # Score-Verteilung
        print(f"\nScore Distribution:")
        print(f"  90-100:  {len(scores[scores >= 90])} ({len(scores[scores >= 90])/len(scores)*100:.1f}%)")
        print(f"  80-89:   {len(scores[(scores >= 80) & (scores < 90)])} ({len(scores[(scores >= 80) & (scores < 90)])/len(scores)*100:.1f}%)")
        print(f"  70-79:   {len(scores[(scores >= 70) & (scores < 80)])} ({len(scores[(scores >= 70) & (scores < 80)])/len(scores)*100:.1f}%)")
        print(f"  <70:     {len(scores[scores < 70])} ({len(scores[scores < 70])/len(scores)*100:.1f}%)")
    
    if "Launchpad Weeks" in launchpad_stocks.columns:
        weeks = launchpad_stocks["Launchpad Weeks"].dropna()
        print(f"\nBase Duration (Weeks):")
        print(f"  Mean:    {weeks.mean():.1f}")
        print(f"  Median:  {weeks.median():.0f}")
        print(f"  Min:     {weeks.min():.0f}")
        print(f"  Max:     {weeks.max():.0f}")
        
        # Wochen-Verteilung
        print(f"\nWeeks Distribution:")
        for w in sorted(weeks.unique()):
            count = len(weeks[weeks == w])
            print(f"  {int(w)} weeks: {count} ({count/len(weeks)*100:.1f}%)")
    
    if "Launchpad Range (%)" in launchpad_stocks.columns:
        ranges = launchpad_stocks["Launchpad Range (%)"].dropna()
        print(f"\nRange %:")
        print(f"  Mean:    {ranges.mean():.2f}%")
        print(f"  Median:  {ranges.median():.2f}%")
        print(f"  Min:     {ranges.min():.2f}%")
        print(f"  Max:     {ranges.max():.2f}%")
        
        # Range-Verteilung
        print(f"\nRange Distribution:")
        print(f"  <8%:     {len(ranges[ranges < 8])} ({len(ranges[ranges < 8])/len(ranges)*100:.1f}%) ✓ Excellent")
        print(f"  8-10%:   {len(ranges[(ranges >= 8) & (ranges < 10)])} ({len(ranges[(ranges >= 8) & (ranges < 10)])/len(ranges)*100:.1f}%) ✓ Good")
        print(f"  10-12%:  {len(ranges[(ranges >= 10) & (ranges < 12)])} ({len(ranges[(ranges >= 10) & (ranges < 12)])/len(ranges)*100:.1f}%) ⚠️ OK")
        print(f"  >12%:    {len(ranges[ranges >= 12])} ({len(ranges[ranges >= 12])/len(ranges)*100:.1f}%) ❌ Too Wide")
    
    # =========================================================================
    # 3. QUALITY ANALYSIS
    # =========================================================================
    print("\n🎯 QUALITY ANALYSIS")
    print("-" * 70)
    
    # High-Quality Launchpads (Score >= 80, Range < 10%)
    high_quality = launchpad_stocks[
        (launchpad_stocks.get("Launchpad Score", 0) >= 80) & 
        (launchpad_stocks.get("Launchpad Range (%)", 100) < 10)
    ]
    
    print(f"\nHigh-Quality Setups (Score ≥80, Range <10%):")
    print(f"  Count: {len(high_quality)} ({len(high_quality)/launchpad_count*100:.1f}% of Launchpads)")
    
    if len(high_quality) > 0:
        print(f"\n  Top 10 High-Quality Launchpads:")
        top10 = high_quality.nlargest(10, "Launchpad Score")
        for idx, row in top10.iterrows():
            ticker = row.get("Ticker", idx)
            score = row.get("Launchpad Score", 0)
            weeks = row.get("Launchpad Weeks", 0)
            range_pct = row.get("Launchpad Range (%)", 0)
            print(f"    {ticker:6s} | Score: {score:5.1f} | {int(weeks)}W | Range: {range_pct:5.2f}%")
    
    # Low-Quality Launchpads (Likely False Positives)
    low_quality = launchpad_stocks[
        (launchpad_stocks.get("Launchpad Score", 100) < 70) | 
        (launchpad_stocks.get("Launchpad Range (%)", 0) > 12)
    ]
    
    print(f"\nLow-Quality Setups (Score <70 OR Range >12%):")
    print(f"  Count: {len(low_quality)} ({len(low_quality)/launchpad_count*100:.1f}% of Launchpads)")
    print(f"  ⚠️  These are likely FALSE POSITIVES!")
    
    if len(low_quality) > 0:
        print(f"\n  Sample Low-Quality Launchpads:")
        sample = low_quality.head(10)
        for idx, row in sample.iterrows():
            ticker = row.get("Ticker", idx)
            score = row.get("Launchpad Score", 0)
            weeks = row.get("Launchpad Weeks", 0)
            range_pct = row.get("Launchpad Range (%)", 0)
            print(f"    {ticker:6s} | Score: {score:5.1f} | {int(weeks)}W | Range: {range_pct:5.2f}%")
    
    # =========================================================================
    # 4. RECOMMENDATIONS
    # =========================================================================
    print("\n💡 RECOMMENDATIONS")
    print("-" * 70)
    
    # Zu viele Hits?
    hit_rate = launchpad_count / total_stocks
    if hit_rate > 0.20:  # >20%
        print("\n⚠️  TOO MANY LAUNCHPAD HITS (>20% of universe)")
        print("\n  Recommended Actions:")
        print("  1. ✅ Increase quality threshold:")
        print("     → Filter: Launchpad Score >= 80")
        print("     → This would reduce hits to ~{} stocks".format(len(high_quality)))
        
        print("\n  2. ✅ Tighten parameters in launchpad_detection.py:")
        
        # Range zu breit?
        if "Launchpad Range (%)" in launchpad_stocks.columns:
            avg_range = launchpad_stocks["Launchpad Range (%)"].mean()
            if avg_range > 10:
                print(f"     → max_range_pct = 0.10  (currently avg: {avg_range:.1f}%)")
        
        # Base zu lang?
        if "Launchpad Weeks" in launchpad_stocks.columns:
            avg_weeks = launchpad_stocks["Launchpad Weeks"].mean()
            if avg_weeks > 5:
                print(f"     → base_weeks_max = 5  (currently avg: {avg_weeks:.1f} weeks)")
        
        print("\n  3. ✅ Add MA50 requirement:")
        print("     → require_above_ma50 = True")
        
    elif hit_rate < 0.02:  # <2%
        print("\n⚠️  TOO FEW LAUNCHPAD HITS (<2% of universe)")
        print("\n  Recommended Actions:")
        print("  1. ✅ Relax parameters slightly")
        print("  2. ✅ Check if market conditions are suitable")
    
    else:
        print("\n✅ Hit rate looks reasonable (2-20% of universe)")
        print(f"\n  Quality breakdown:")
        print(f"    Excellent (Score ≥90): {len(launchpad_stocks[launchpad_stocks.get('Launchpad Score', 0) >= 90])}")
        print(f"    Good (Score 80-89):    {len(launchpad_stocks[(launchpad_stocks.get('Launchpad Score', 0) >= 80) & (launchpad_stocks.get('Launchpad Score', 0) < 90)])}")
        print(f"    OK (Score 70-79):      {len(launchpad_stocks[(launchpad_stocks.get('Launchpad Score', 0) >= 70) & (launchpad_stocks.get('Launchpad Score', 0) < 80)])}")
    
    # =========================================================================
    # 5. SUGGESTED FILTER
    # =========================================================================
    print("\n🔧 SUGGESTED FILTER FOR SCREENING")
    print("-" * 70)
    
    print("\nOption A: Quality Filter (Recommended)")
    print("  Filter: (Launchpad Score >= 80) & (Launchpad Range % < 10%)")
    quality_filtered = len(high_quality)
    print(f"  Result: {quality_filtered} stocks ({quality_filtered/total_stocks*100:.1f}% of universe)")
    
    print("\nOption B: Strict Filter")
    print("  Filter: (Launchpad Score >= 85) & (Launchpad Range % < 8%)")
    strict_filtered = len(launchpad_stocks[
        (launchpad_stocks.get("Launchpad Score", 0) >= 85) & 
        (launchpad_stocks.get("Launchpad Range (%)", 100) < 8)
    ])
    print(f"  Result: {strict_filtered} stocks ({strict_filtered/total_stocks*100:.1f}% of universe)")
    
    print("\nOption C: VCP + Launchpad Combination")
    if "VCP" in df.columns:
        both_patterns = len(df[(df["VCP"] == True) & (df["Launchpad"] == True)])
        print(f"  Filter: VCP == True AND Launchpad == True")
        print(f"  Result: {both_patterns} stocks ({both_patterns/total_stocks*100:.1f}% of universe)")
    
    print("\n" + "="*70)
    print("END OF DIAGNOSTIC REPORT")
    print("="*70)


def export_launchpad_samples(excel_path: str = None, n_samples: int = 20):
    """
    Exportiert Sample-Charts für manuelle Review.
    """
    import yfinance as yf
    
    if excel_path is None:
        artifacts_dir = Path("artifacts")
        excel_files = list(artifacts_dir.glob("market_leaders_*.xlsx"))
        if not excel_files:
            print("❌ Keine Excel-Reports gefunden!")
            return
        excel_path = max(excel_files, key=lambda p: p.stat().st_mtime)
    
    df = pd.read_excel(excel_path, sheet_name="Leaders")
    launchpad_stocks = df[df["Launchpad"] == True]
    
    if len(launchpad_stocks) == 0:
        print("❌ Keine Launchpad-Stocks gefunden!")
        return
    
    # Sample: Top Score + Random Mix
    top_stocks = launchpad_stocks.nlargest(n_samples // 2, "Launchpad Score")
    random_stocks = launchpad_stocks.sample(n=min(n_samples // 2, len(launchpad_stocks)))
    samples = pd.concat([top_stocks, random_stocks]).drop_duplicates()
    
    print(f"\n📊 Exportiere {len(samples)} Sample-Charts...")
    print("Ticker | Score | Weeks | Range %")
    print("-" * 40)
    
    for idx, row in samples.iterrows():
        ticker = row.get("Ticker", idx)
        score = row.get("Launchpad Score", 0)
        weeks = row.get("Launchpad Weeks", 0)
        range_pct = row.get("Launchpad Range (%)", 0)
        
        print(f"{ticker:6s} | {score:5.1f} | {int(weeks):2d}W  | {range_pct:6.2f}%")
    
    print("\n💡 Manuelle Review:")
    print("   1. Öffne TradingView / Chart-Software")
    print("   2. Prüfe jeden Ticker visuell")
    print("   3. Identifiziere Patterns die NICHT tight sind")
    print("   4. Notiere False Positives")


if __name__ == "__main__":
    print("\n🔍 LAUNCHPAD DIAGNOSTIC TOOL\n")
    
    # Option 1: Analyse
    analyze_launchpad_results()
