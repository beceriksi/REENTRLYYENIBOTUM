import os
import time
import math
import requests
from datetime import datetime, timezone
import pandas as pd

OKX_BASE = "https://www.okx.com"

# -------------------- Semboller -------------------- #
TRADE_SYMBOLS = [
    "BTC-USDT",
    "ETH-USDT",
    "BNB-USDT",
    "SOL-USDT",
    "XRP-USDT",
    "ADA-USDT",
    "DOGE-USDT",
]

MAJORS = ["BTC-USDT", "ETH-USDT"]
ALTCOINS = [s for s in TRADE_SYMBOLS if s not in MAJORS]

# -------------------- Telegram -------------------- #
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# MODE:
# - "TREND": Trend değişimi + re-entry taraması (default)
# - "SUMMARY": Sadece BTC/ETH günlük özet
MODE = os.getenv("MODE", "TREND").upper()


# ---------------------- Genel Yardımcılar ---------------------- #
def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("\n[UYARI] Telegram TOKEN veya CHAT_ID yok. Mesaj gösteriliyor:\n")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}

    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print("[HATA] Telegram gönderilemedi:", r.text)
    except Exception as e:
        print("[HATA] Telegram hatası:", e)


# ---------------------- OKX GET Wrapper ---------------------- #
def jget_okx(path, params=None, retries=5, timeout=10):
    url = f"{OKX_BASE}{path}"
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                time.sleep(1)
                continue

            data = r.json()
            if "code" not in data:
                time.sleep(1)
                continue

            if data["code"] != "0":
                time.sleep(1)
                continue

            return data.get("data", [])
        except Exception:
            time.sleep(1)

    return []


# ---------------------- Mum Verisi ---------------------- #
def get_candles(inst, bar, limit=200):
    raw = jget_okx("/api/v5/market/candles", {"instId": inst, "bar": bar, "limit": limit})
    if not raw or len(raw) < 5:
        return None

    raw = list(reversed(raw))
    rows = []
    for r in raw:
        try:
            rows.append(
                {
                    "ts": datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
            )
        except Exception:
            continue

    if len(rows) < 60:
        return None

    return pd.DataFrame(rows)


# ---------------------- Whale / Net Flow ---------------------- #
def get_trade_flow(inst):
    data = jget_okx("/api/v5/market/trades", {"instId": inst, "limit": 200})
    if not data or not isinstance(data, list):
        return {"net": 0, "cat": "-", "dir": None}

    buy_usd, sell_usd = 0.0, 0.0
    max_size, max_side = 0.0, None

    for t in data:
        try:
            px = float(t["px"])
            sz = float(t["sz"])
            usd = px * sz
            side = t["side"]

            if side == "buy":
                buy_usd += usd
            else:
                sell_usd += usd

            if usd > max_size:
                max_size = usd
                max_side = side
        except Exception:
            continue

    if max_size >= 1_000_000:
        cat = "XXL"
    elif max_size >= 500_000:
        cat = "XL"
    elif max_size >= 150_000:
        cat = "L"
    elif max_size >= 50_000:
        cat = "M"
    else:
        cat = "-"

    return {
        "net": buy_usd - sell_usd,
        "cat": cat,
        "dir": "UP" if max_side == "buy" else "DOWN" if max_side == "sell" else None,
    }


# ---------------------- İndikatörler ---------------------- #
def add_indicators(df):
    close = df["close"]

    # 4H için biraz hızlı/orta EMA
    df["ema_fast"] = close.ewm(span=14, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=28, adjust=False).mean()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    df["vol_sma20"] = df["volume"].rolling(20).mean()
    df["v_ratio"] = df["volume"] / df["vol_sma20"]

    return df


# ---------------------- Swing High/Low ---------------------- #
def detect_swings(df, look=2):
    df["swing_high"] = False
    df["swing_low"] = False

    for i in range(look, len(df) - look):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]

        if all(h > df["high"].iloc[i - k] for k in range(1, look + 1)) and all(
            h > df["high"].iloc[i + k] for k in range(1, look + 1)
        ):
            df.at[i, "swing_high"] = True

        if all(l < df["low"].iloc[i - k] for k in range(1, look + 1)) and all(
            l < df["low"].iloc[i + k] for k in range(1, look + 1)
        ):
            df.at[i, "swing_low"] = True

    return df


# ---------------------- HH/HL/LH/LL ---------------------- #
def get_structure(df, idx):
    highs = [i for i in range(idx + 1) if df.at[i, "swing_high"]]
    lows = [i for i in range(idx + 1) if df.at[i, "swing_low"]]

    ht = lt = None
    last_hi = last_lo = None

    if len(highs) >= 2:
        last_hi = highs[-1]
        prev_hi = highs[-2]
        ht = "HH" if df.at[last_hi, "high"] > df.at[prev_hi, "high"] else "LH"

    if len(lows) >= 2:
        last_lo = lows[-1]
        prev_lo = lows[-2]
        lt = "HL" if df.at[last_lo, "low"] > df.at[prev_lo, "low"] else "LL"

    struct_dir = "NEUTRAL"
    if ht == "HH" or lt == "HL":
        struct_dir = "UP"
    if ht == "LH" or lt == "LL":
        struct_dir = "DOWN"

    return {"dir": struct_dir, "high": ht, "low": lt, "hi_idx": last_hi, "lo_idx": last_lo}


# ---------------------- Trend Onay (C modeli) ---------------------- #
def trend_decision(df, idx, whale_dir):
    st = get_structure(df, idx)
    struct_dir = st["dir"]

    ema_dir = "UP" if df.at[idx, "ema_fast"] > df.at[idx, "ema_slow"] else "DOWN"
    macd_dir = "UP" if df.at[idx, "macd"] > df.at[idx, "macd_signal"] else "DOWN"

    confirmed = None

    # Structure + EMA aynı yönde ise aday
    if struct_dir != "NEUTRAL" and struct_dir == ema_dir:
        match = 2  # structure + EMA

        if macd_dir == struct_dir:
            match += 1
        if whale_dir == struct_dir:
            match += 1

        # en az 3/4 onay
        if match >= 3:
            confirmed = struct_dir

    return {"raw": ema_dir, "confirmed": confirmed, "structure": st}


# ---------------------- Yardımcı Metinler ---------------------- #
def side_text(d):
    return "LONG" if d == "UP" else "SHORT"


def side_arrow(d):
    return "🟢" if d == "UP" else "🔴"


def strength(now, day):
    if day == "NEUTRAL":
        return "Nötr Sinyal"
    return "Güçlü Sinyal" if now == day else "Zayıf Sinyal (Karşı Trend)"


def px(x):
    return f"{x:,.2f}"


def fmt_usd(x):
    return f"{x:,.0f} USDT"


# ---------------------- Ana Analiz ---------------------- #
def analyze(inst):
    df4 = get_candles(inst, "4H", 220)
    if df4 is None:
        raise RuntimeError("4H veri yok")

    df4 = add_indicators(df4)
    df4 = detect_swings(df4)

    df1 = get_candles(inst, "1D", 160)
    if df1 is None:
        raise RuntimeError("1D veri yok")

    df1 = add_indicators(df1)
    df1 = detect_swings(df1)

    trade = get_trade_flow(inst)
    net = trade["net"]
    whale_cat = trade["cat"]
    whale_side = trade["dir"]

    whale_dir = None
    # büyük net akış varsa whale yönünü trend onayında kullan
    if abs(net) > 80_000 and whale_side is not None:
        whale_dir = whale_side

    i4 = len(df4) - 1
    p4 = len(df4) - 2

    now = trend_decision(df4, i4, whale_dir)
    prev = trend_decision(df4, p4, None)

    # 1D trend: structure + EMA aynıysa UP/DOWN, değilse NEUTRAL
    s1 = get_structure(df1, len(df1) - 1)
    ema1 = "UP" if df1["ema_fast"].iloc[-1] > df1["ema_slow"].iloc[-1] else "DOWN"

    if s1["dir"] == "UP" and ema1 == "UP":
        day = "UP"
    elif s1["dir"] == "DOWN" and ema1 == "DOWN":
        day = "DOWN"
    else:
        day = "NEUTRAL"

    close = df4["close"].iloc[-1]
    hi_idx = now["structure"]["hi_idx"]
    lo_idx = now["structure"]["lo_idx"]

    # swing range: son belirgin hi/lo varsa oradan; yoksa son 20 bar range
    if hi_idx is not None and lo_idx is not None:
        swing_range = abs(df4.at[hi_idx, "high"] - df4.at[lo_idx, "low"])
    else:
        swing_range = df4["high"].tail(20).max() - df4["low"].tail(20).min()

    return {
        "inst": inst,
        "df4": df4,
        "day": day,
        "now": now,
        "prev": prev,
        "close": close,
        "swing": swing_range,
        "hi": hi_idx,
        "lo": lo_idx,
        "net": net,
        "whale_cat": whale_cat,
        "whale_dir": whale_dir,
        "v_ratio": float(df4["v_ratio"].iloc[-1]) if not math.isnan(df4["v_ratio"].iloc[-1]) else 0.0,
        "high_type": now["structure"]["high"],
        "low_type": now["structure"]["low"],
        "ema_fast": float(df4["ema_fast"].iloc[-1]),
        "ema_slow": float(df4["ema_slow"].iloc[-1]),
    }


# ---------------------- SL/TP Hesabı ---------------------- #
def calc_levels(d, direction):
    close = d["close"]
    swing = d["swing"]
    df4 = d["df4"]
    hi_idx = d["hi"]
    lo_idx = d["lo"]

    # SL: son swing'e mümkünse dayan
    if direction == "UP":
        sl = df4["low"].iloc[lo_idx] if lo_idx is not None else close * 0.97
        tp1 = close + swing * 0.5
        tp2 = close + swing * 1.0
        tp3 = close + swing * 1.5
    else:
        sl = df4["high"].iloc[hi_idx] if hi_idx is not None else close * 1.03
        tp1 = close - swing * 0.5
        tp2 = close - swing * 1.0
        tp3 = close - swing * 1.5

    return sl, tp1, tp2, tp3


# ---------------------- Re-entry (Pullback) ---------------------- #
def is_reentry_signal(d):
    """
    Trend devam ederken (confirmed UP/DOWN) EMA'ya dönüş/pullback sonrası tekrar devam sinyali.
    Basit ve net:
    - confirmed UP ise: önce close <= ema_fast (pullback), sonra tekrar close > ema_fast
    - confirmed DOWN ise: önce close >= ema_fast, sonra tekrar close < ema_fast
    Bu kontrolü son 3 bar içinde arıyoruz.
    """
    df = d["df4"]
    conf = d["now"]["confirmed"]
    if conf is None:
        return False

    # son 3 kapanış
    c2 = float(df["close"].iloc[-3])
    c1 = float(df["close"].iloc[-2])
    c0 = float(df["close"].iloc[-1])

    e2 = float(df["ema_fast"].iloc[-3])
    e1 = float(df["ema_fast"].iloc[-2])
    e0 = float(df["ema_fast"].iloc[-1])

    if conf == "UP":
        pulled = (c2 <= e2) or (c1 <= e1)
        resumed = c0 > e0
        return pulled and resumed

    if conf == "DOWN":
        pulled = (c2 >= e2) or (c1 >= e1)
        resumed = c0 < e0
        return pulled and resumed

    return False


# ---------------------- Mesaj Format ---------------------- #
def build_signal_block(d, tag):
    """
    tag: "TREND" veya "RE-ENTRY"
    """
    now = d["now"]["confirmed"]
    day = d["day"]
    base = d["inst"].split("-")[0]

    h = d["high_type"]
    l = d["low_type"]

    hh = []
    if h:
        hh.append(("🟢 " if h == "HH" else "🔴 ") + h)
    if l:
        hh.append(("🟢 " if l == "HL" else "🔴 ") + l)
    structure_line = " | ".join(hh) if hh else "-"

    whale_line = f"{d['whale_cat']} / {fmt_usd(d['net'])}"
    if d["whale_dir"] == "UP":
        whale_line += " (Alım)"
    elif d["whale_dir"] == "DOWN":
        whale_line += " (Satış)"

    sl, tp1, tp2, tp3 = calc_levels(d, now)

    block = (
        f"\n{base} ({tag}):\n"
        f"- Yön: {side_arrow(now)} {side_text(now)} ({strength(now, day)})\n"
        f"- Yapı: {structure_line}\n"
        f"- Whale: {whale_line}\n"
        f"- vRatio: {d['v_ratio']:.2f}\n"
        f"- 1D: {day}\n"
        f"- Giriş (Close): {px(d['close'])}\n"
        f"- SL: {px(sl)}\n"
        f"- TP1: {px(tp1)}\n"
        f"- TP2: {px(tp2)}\n"
        f"- TP3: {px(tp3)}\n"
    )
    return block


# ---------------------- SUMMARY (BTC/ETH) ---------------------- #
def daily_summary():
    lines = []
    header = f"📌 BTC/ETH Günlük Özet (1D + 4H) — {ts()}\n"

    for s in MAJORS:
        try:
            d = analyze(s)
            base = s.split("-")[0]
            conf = d["now"]["confirmed"]
            raw = d["now"]["raw"]
            day = d["day"]
            w = f"{d['whale_cat']} / {fmt_usd(d['net'])}"
            if d["whale_dir"] == "UP":
                w += " (Alım)"
            elif d["whale_dir"] == "DOWN":
                w += " (Satış)"

            conf_txt = "YOK (onay yok)" if conf is None else f"{side_arrow(conf)} {side_text(conf)}"
            lines.append(
                f"\n{base}:\n"
                f"- 4H Confirmed: {conf_txt}\n"
                f"- 4H EMA Raw: {raw}\n"
                f"- 1D: {day}\n"
                f"- Close(4H): {px(d['close'])}\n"
                f"- Whale: {w}\n"
                f"- vRatio: {d['v_ratio']:.2f}\n"
            )
        except Exception as e:
            lines.append(f"\n{s}: HATA ({e})\n")

    send_telegram(header + "".join(lines))


# ---------------------- MAIN ---------------------- #
def main():
    print("[INFO] Başladı:", ts(), "| MODE =", MODE)

    if MODE == "SUMMARY":
        daily_summary()
        print("[INFO] Summary gönderildi.")
        return

    # MODE == TREND
    A = {}
    for s in TRADE_SYMBOLS:
        try:
            A[s] = analyze(s)
        except Exception as e:
            print("[HATA]", s, e)

    if not A:
        print("[HATA] Analiz yok.")
        return

    # ---- Majör rejimi: BTC veya ETH confirmed DOWN ise altcoin LONG blok ----
    block_alt_long = False
    for m in MAJORS:
        md = A.get(m)
        if md and md["now"]["confirmed"] == "DOWN":
            block_alt_long = True
            break

    # ---------------- 1) Trend değişimi mesajı ---------------- #
    blocks = []
    changed_any = False

    for s, d in A.items():
        now = d["now"]["confirmed"]
        prev = d["prev"]["confirmed"]

        if now is None:
            continue

        # ALTCOIN LONG BLOK (BTC veya ETH bearish)
        if s in ALTCOINS and block_alt_long and now == "UP":
            continue

        if prev is None or prev != now:
            changed_any = True
            blocks.append(build_signal_block(d, "TREND DEĞİŞİMİ"))

    if changed_any:
        title = "⚠️ TREND / YÖN DEĞİŞİMİ — 4H KAPANIŞ\n"
        if block_alt_long:
            title += "🔒 Rejim: BTC/ETH bearish → Altcoin LONG kapalı\n"
        send_telegram(title + "".join(blocks))
        print("[INFO] Trend değişimi mesajı gönderildi.")
        return

    # ---------------- 2) Re-entry (trend devam) ---------------- #
    re_blocks = []
    for s, d in A.items():
        now = d["now"]["confirmed"]
        if now is None:
            continue

        # ALTCOIN LONG BLOK (BTC veya ETH bearish)
        if s in ALTCOINS and block_alt_long and now == "UP":
            continue

        if is_reentry_signal(d):
            re_blocks.append(build_signal_block(d, "RE-ENTRY"))

    if re_blocks:
        title = "✅ TREND DEVAM — RE-ENTRY (Pullback sonrası)\n"
        if block_alt_long:
            title += "🔒 Rejim: BTC/ETH bearish → Altcoin LONG kapalı\n"
        send_telegram(title + "".join(re_blocks))
        print("[INFO] Re-entry mesajı gönderildi.")
        return

    print("[INFO] Değişim yok, re-entry yok.")


if __name__ == "__main__":
    main()
