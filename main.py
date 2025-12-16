import os
import requests
import pandas as pd
from datetime import datetime, timezone

OKX_BASE = "https://www.okx.com"

TRADE_SYMBOLS = [
    "BTC-USDT", "ETH-USDT",
    "BNB-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT", "DOGE-USDT"
]

MAJORS = ["BTC-USDT", "ETH-USDT"]
ALTCOINS = [s for s in TRADE_SYMBOLS if s not in MAJORS]

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BUFFER_LONG = 0.995   # -%0.5
BUFFER_SHORT = 1.005  # +%0.5

# ----------------- Helpers -----------------
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text)
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text}
    )

def jget(path, params=None):
    try:
        r = requests.get(OKX_BASE + path, params=params, timeout=10)
        j = r.json()
        if j.get("code") == "0":
            return j["data"]
    except:
        pass
    return []

# ----------------- Data -----------------
def get_candles(inst, bar="4H", limit=200):
    raw = jget("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": limit})
    if not raw:
        return None
    raw = list(reversed(raw))
    return pd.DataFrame([{
        "open": float(r[1]),
        "high": float(r[2]),
        "low": float(r[3]),
        "close": float(r[4]),
        "volume": float(r[5])
    } for r in raw])

# ----------------- Indicators -----------------
def add_indicators(df):
    c = df["close"]
    df["ema_fast"] = c.ewm(span=14).mean()
    df["ema_slow"] = c.ewm(span=28).mean()
    return df

def detect_swings(df):
    df["sh"] = False
    df["sl"] = False
    for i in range(2, len(df)-2):
        if df.high[i] > df.high[i-1] and df.high[i] > df.high[i+1]:
            df.at[i, "sh"] = True
        if df.low[i] < df.low[i-1] and df.low[i] < df.low[i+1]:
            df.at[i, "sl"] = True
    return df

# ----------------- Structure -----------------
def get_structure(df):
    highs = df[df.sh].index.tolist()
    lows  = df[df.sl].index.tolist()

    ht = lt = None
    if len(highs) >= 2:
        ht = "HH" if df.high[highs[-1]] > df.high[highs[-2]] else "LH"
    if len(lows) >= 2:
        lt = "HL" if df.low[lows[-1]] > df.low[lows[-2]] else "LL"
    return ht, lt

# ----------------- SL (SAFE) -----------------
def calc_safe_sl(df, direction):
    ema = df.ema_slow.iloc[-1]

    if direction == "UP":
        lows = df[df.sl].index.tolist()
        swing = df.low[lows[-1]] if lows else ema
        return min(swing, ema * BUFFER_LONG)

    else:
        highs = df[df.sh].index.tolist()
        swing = df.high[highs[-1]] if highs else ema
        return max(swing, ema * BUFFER_SHORT)

# ----------------- Analysis -----------------
def analyze(inst):
    df = get_candles(inst)
    if df is None or len(df) < 60:
        return None

    df = add_indicators(df)
    df = detect_swings(df)

    ht, lt = get_structure(df)
    ema_dir = "UP" if df.ema_fast.iloc[-1] > df.ema_slow.iloc[-1] else "DOWN"

    direction = None
    if ema_dir == "UP" and (ht == "HH" or lt == "HL"):
        direction = "UP"
    if ema_dir == "DOWN" and lt == "LL":   # LL gelmeden SHORT yok
        direction = "DOWN"

    if direction is None:
        return None

    close = df.close.iloc[-1]
    sl = calc_safe_sl(df, direction)
    risk = abs(close - sl)

    tp1 = close + risk * 1 if direction == "UP" else close - risk * 1
    tp2 = close + risk * 2 if direction == "UP" else close - risk * 2

    return inst, direction, close, sl, tp1, tp2, ht, lt

# ----------------- MAIN -----------------
def main():
    results = {}
    for s in TRADE_SYMBOLS:
        r = analyze(s)
        if r:
            results[s] = r

    block_alt_long = any(
        results.get(m, [None,None])[1] == "DOWN" for m in MAJORS
    )

    msgs = []
    for s,(inst,dir,entry,sl,tp1,tp2,ht,lt) in results.items():
        if s in ALTCOINS and block_alt_long and dir == "UP":
            continue

        arrow = "🟢" if dir=="UP" else "🔴"
        side  = "LONG" if dir=="UP" else "SHORT"

        msgs.append(
            f"{arrow} {s.split('-')[0]} {side}\n"
            f"Yapı: {ht} / {lt}\n"
            f"Giriş: {entry:.2f}\n"
            f"SL: {sl:.2f}\n"
            f"TP1: {tp1:.2f}\n"
            f"TP2: {tp2:.2f}\n"
        )

    if msgs:
        send_telegram("⚠️ ONAYLI TREND SİNYALİ (GÜVENLİ SL)\n\n" + "\n".join(msgs))
    else:
        print("Sinyal yok.")

if __name__ == "__main__":
    main()
