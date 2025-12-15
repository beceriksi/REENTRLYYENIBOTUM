import os
import time
import math
import requests
from datetime import datetime, timezone
import pandas as pd

OKX_BASE = "https://www.okx.com"

# -------------------- Semboller -------------------- #
TRADE_SYMBOLS = [
    "BTC-USDT", "ETH-USDT",
    "BNB-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT", "DOGE-USDT"
]

MAJORS = ["BTC-USDT", "ETH-USDT"]
ALTCOINS = [s for s in TRADE_SYMBOLS if s not in MAJORS]

# -------------------- Telegram -------------------- #
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# -------------------- Helpers -------------------- #
def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text})

# -------------------- OKX -------------------- #
def jget(path, params=None):
    try:
        r = requests.get(OKX_BASE + path, params=params, timeout=10)
        j = r.json()
        if j.get("code") == "0":
            return j["data"]
    except:
        pass
    return []

def get_candles(inst, bar, limit=200):
    raw = jget("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": limit})
    if not raw:
        return None
    raw = list(reversed(raw))
    rows = []
    for r in raw:
        rows.append({
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5])
        })
    return pd.DataFrame(rows)

# -------------------- Indicators -------------------- #
def add_indicators(df):
    c = df["close"]
    df["ema_fast"] = c.ewm(span=14).mean()
    df["ema_slow"] = c.ewm(span=28).mean()
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["v_ratio"] = df["volume"] / df["vol_sma"]
    return df

# -------------------- Swings -------------------- #
def detect_swings(df):
    df["sh"] = False
    df["sl"] = False
    for i in range(2, len(df)-2):
        if df.high[i] > df.high[i-1] and df.high[i] > df.high[i+1]:
            df.at[i,"sh"] = True
        if df.low[i] < df.low[i-1] and df.low[i] < df.low[i+1]:
            df.at[i,"sl"] = True
    return df

def structure(df):
    highs = df[df.sh].index.tolist()
    lows = df[df.sl].index.tolist()

    ht = lt = None
    if len(highs) >= 2:
        ht = "HH" if df.high[highs[-1]] > df.high[highs[-2]] else "LH"
    if len(lows) >= 2:
        lt = "HL" if df.low[lows[-1]] > df.low[lows[-2]] else "LL"

    return ht, lt

# -------------------- Analysis -------------------- #
def analyze(inst):
    df = get_candles(inst, "4H")
    if df is None: return None

    df = add_indicators(df)
    df = detect_swings(df)

    ht, lt = structure(df)

    ema_dir = "UP" if df.ema_fast.iloc[-1] > df.ema_slow.iloc[-1] else "DOWN"

    confirmed = None

    # ✅ LONG: HH veya HL + EMA UP
    if ema_dir == "UP" and (ht == "HH" or lt == "HL"):
        confirmed = "UP"

    # 🔒 SHORT: SADECE LL + EMA DOWN
    if ema_dir == "DOWN" and lt == "LL":
        confirmed = "DOWN"

    return {
        "inst": inst,
        "dir": confirmed,
        "ht": ht,
        "lt": lt,
        "close": df.close.iloc[-1],
        "hi": df.high.max(),
        "lo": df.low.min()
    }

# -------------------- MAIN -------------------- #
def main():
    results = {}
    for s in TRADE_SYMBOLS:
        r = analyze(s)
        if r:
            results[s] = r

    # BTC/ETH bearish ise alt LONG kapalı
    block_alt_long = any(
        results.get(m,{}).get("dir") == "DOWN" for m in MAJORS
    )

    msgs = []

    for s,d in results.items():
        if d["dir"] is None:
            continue

        if s in ALTCOINS and block_alt_long and d["dir"] == "UP":
            continue

        side = "LONG" if d["dir"] == "UP" else "SHORT"
        arrow = "🟢" if d["dir"] == "UP" else "🔴"

        sl = d["lo"] if d["dir"]=="UP" else d["hi"]
        tp1 = d["close"] + (d["close"]-sl)*1 if d["dir"]=="UP" else d["close"]-(sl-d["close"])*1
        tp2 = d["close"] + (d["close"]-sl)*2 if d["dir"]=="UP" else d["close"]-(sl-d["close"])*2

        msgs.append(
            f"{arrow} {s.split('-')[0]} {side}\n"
            f"Yapı: {d['ht']} / {d['lt']}\n"
            f"Giriş: {d['close']:.2f}\n"
            f"SL: {sl:.2f}\n"
            f"TP1: {tp1:.2f}\n"
            f"TP2: {tp2:.2f}\n"
        )

    if msgs:
        send_telegram("⚠️ ONAYLI TREND SİNYALİ\n\n" + "\n".join(msgs))
    else:
        print("Sinyal yok.")

if __name__ == "__main__":
    main()
