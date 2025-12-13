import pandas as pd
import numpy as np

def detect_vcp(df: pd.DataFrame, window: int = 120) -> dict:
    """
    Verbesserter VCP-Detektor nach Minervini.
    Gibt IMMER ein Dictionary zurück, das von screener.py erwartet wird.
    """
    result = {
        "VCP": False,
        "Waves": 0,
        "Entry_Signal": False,
        "Breakout_Level": None,
    }

    if df is None or df.empty or "Close" not in df:
        return result

    # --- Nur letzten 'window' Bars betrachten ---
    data = df.tail(window).copy()
    closes = data["Close"].astype(float)
    vols = data["Volume"].astype(float) if "Volume" in data.columns else None

    if len(closes) < 60:
        return result

    # === 1) Uptrend erforderlich ===
    hh = closes.max()
    last_close = closes.iloc[-1]

    if last_close < 0.80 * hh:
        return result

    ma20 = closes.rolling(20).mean()
    ma50 = closes.rolling(50).mean()

    if not (last_close > ma20.iloc[-1] > ma50.iloc[-1]):
        return result

    if not (ma20.iloc[-1] > ma20.iloc[-5] and ma50.iloc[-1] > ma50.iloc[-5]):
        return result

    # === 2) Volatility Contraction ===
    ret_abs = closes.pct_change().abs().dropna()
    n = len(ret_abs)
    k = n // 3
    early, mid, late = ret_abs[:k], ret_abs[k:2*k], ret_abs[2*k:]

    def _drop(a, b, min_rel=0.15):
        return (a > b) and ((a - b) / max(a, 1e-9) >= min_rel)

    if not (_drop(early.mean(), mid.mean()) and _drop(mid.mean(), late.mean())):
        return result

    # === 3) Volumenkontraktion (optional) ===
    if vols is not None and not vols.isna().all():
        v = vols.tail(window).replace(0, np.nan).dropna()
        if len(v) > 20:
            vk = len(v) // 3
            ve, vm, vl = v[:vk], v[vk:2*vk], v[2*vk:]
            if not (_drop(ve.mean(), vm.mean(), 0.10) and _drop(vm.mean(), vl.mean(), 0.10)):
                return result

    # === 4) Tightness der letzten 10 Bars ===
    last10 = closes.tail(10)
    if (last10.max() - last10.min()) / last_close > 0.10:
        return result

    # === Breakout-Level ===
    breakout_level = closes.max()

    # === Entry-Signal: aktueller Close > Pivot * 1.01 ===
    #entry_signal = last_close > breakout_level * 1.01  
    still_vcp = last_close < breakout_level * 1.03   # 3% Puffer
    
    if entry_signal and still_vcp:
        # Early-Breakout: immer noch VCP-Kandidat
        result["VCP"] = True
    else:
        result["VCP"] = (alle anderen Checks)

    # === Ergebnis zusammenstellen ===
    result["VCP"] = True
    result["Waves"] = 3   # wir haben keine echte Wave-Count-Logik, daher Dummy
    result["Entry_Signal"] = entry_signal
    result["Breakout_Level"] = breakout_level

    return result



