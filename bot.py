import os
import json
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN در GitHub Secrets تنظیم نشده است.")
if not CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID در GitHub Secrets تنظیم نشده است.")

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"
TOP5 = ["BTC", "ETH", "BNB", "SOL", "XRP"]
QUOTE = "USDT"
TRADE_LIMIT = 1000
TIMEOUT = 20
MIN_SCORE = 85
MAX_SIGNALS = 2
MIN_ATR_PERCENT = 0.12
MIN_MOVE_PERCENT = 0.10
HISTORY_FILE = "signals_history.json"
HISTORY_MAX = 500

S = requests.Session()


def api(endpoint, params=None):
    r = S.get(f"{TABDEAL_BASE}/{endpoint}", params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def telegram(text):
    r = S.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=TIMEOUT,
    )
    r.raise_for_status()


def val(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def num(x):
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def time_ms(t):
    x = num(val(t, "time", "timestamp", "T", "createdAt", "created_at", "timeMs"))
    if x is None:
        return None
    if x < 10_000_000_000:
        x *= 1000
    return int(x)


def items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("symbols", "data", "result", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def markets():
    out = {}
    for m in items(api("exchangeInfo")):
        if not isinstance(m, dict):
            continue
        status = str(m.get("status", "TRADING")).upper()
        if status not in ("TRADING", "ENABLED", "ACTIVE"):
            continue
        base = str(m.get("baseAsset") or m.get("base") or "").upper()
        quote = str(m.get("quoteAsset") or m.get("quote") or "").upper()
        symbol = str(m.get("symbol") or m.get("pair") or m.get("market") or "").upper()
        td_symbol = str(m.get("tabdealSymbol") or m.get("tabdeal_symbol") or symbol).upper()
        if base in TOP5 and quote == QUOTE and symbol:
            out[(base, quote)] = {
                "base": base, "quote": quote,
                "symbol": symbol, "tabdeal_symbol": td_symbol
            }
    return out


def trades(market):
    last = None
    for symbol in (market["symbol"], market["tabdeal_symbol"]):
        try:
            data = api("trades", {"symbol": symbol, "limit": TRADE_LIMIT})
            got = items(data)
            if got:
                return got
        except Exception as e:
            last = e
    if last:
        raise last
    return []


def candles(trade_list, minutes):
    rows = []
    for t in trade_list:
        if not isinstance(t, dict):
            continue
        p = num(val(t, "price", "p"))
        q = num(val(t, "qty", "quantity", "q", "amount", "volume")) or 0.0
        ts = time_ms(t)
        if p is not None and ts is not None:
            rows.append((pd.to_datetime(ts, unit="ms", utc=True), p, q))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["time", "price", "volume"]).sort_values("time").set_index("time")
    c = df["price"].resample(f"{minutes}min").ohlc()
    c["volume"] = df["volume"].resample(f"{minutes}min").sum()
    return c.dropna(subset=["open", "high", "low", "close"]).reset_index()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - pc).abs(),
        (df["low"] - pc).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def indicators(df):
    d = df.copy()
    d["ema20"] = ema(d["close"], 20)
    d["ema50"] = ema(d["close"], 50)
    d["rsi"] = rsi(d["close"])
    d["atr"] = atr(d)
    macd = ema(d["close"], 12) - ema(d["close"], 26)
    d["macd_hist"] = macd - ema(macd, 9)
    d["vol_ma"] = d["volume"].rolling(20).mean()
    return d


def analyze(market, trade_list):
    c5 = candles(trade_list, 5)
    c15 = candles(trade_list, 15)
    if len(c5) < 60 or len(c15) < 30:
        return None

    c5 = indicators(c5)
    c15 = indicators(c15)
    a5, a15 = c5.iloc[-2], c15.iloc[-2]

    long_score = short_score = 0
    lr, sr = [], []

    if a15["ema20"] > a15["ema50"]:
        long_score += 20; lr.append("روند 15 دقیقه صعودی")
    if a15["ema20"] < a15["ema50"]:
        short_score += 20; sr.append("روند 15 دقیقه نزولی")
    if a15["rsi"] > 50:
        long_score += 15; lr.append("RSI 15 دقیقه مثبت")
    if a15["rsi"] < 50:
        short_score += 15; sr.append("RSI 15 دقیقه منفی")
    if a15["macd_hist"] > 0:
        long_score += 20; lr.append("MACD 15 دقیقه مثبت")
    if a15["macd_hist"] < 0:
        short_score += 20; sr.append("MACD 15 دقیقه منفی")
    if a5["close"] > a5["ema20"]:
        long_score += 15; lr.append("قیمت 5 دقیقه بالای EMA20")
    if a5["close"] < a5["ema20"]:
        short_score += 15; sr.append("قیمت 5 دقیقه زیر EMA20")
    if a5["rsi"] >= 52:
        long_score += 15; lr.append("مومنتوم 5 دقیقه‌ای صعودی")
    if a5["rsi"] <= 48:
        short_score += 15; sr.append("مومنتوم 5 دقیقه‌ای نزولی")

    if pd.notna(a5["vol_ma"]) and a5["vol_ma"] > 0 and a5["volume"] > a5["vol_ma"] * 1.2:
        if a5["close"] > a5["open"]:
            long_score += 15; lr.append("حجم بالاتر از میانگین")
        elif a5["close"] < a5["open"]:
            short_score += 15; sr.append("حجم بالاتر از میانگین")

    entry = num(a5["close"])
    a = num(a5["atr"])
    if entry is None or a is None or a <= 0:
        return None

    atr_pct = a / entry * 100
    if atr_pct < MIN_ATR_PERCENT:
        return None

    old = num(c5.iloc[-7]["close"])
    if old is None or old == 0:
        return None
    move = abs(entry - old) / old * 100
    if move < MIN_MOVE_PERCENT:
        return None

    hi = float(c5.iloc[-7:-2]["high"].max())
    lo = float(c5.iloc[-7:-2]["low"].min())
    if entry > hi:
        long_score += 10; lr.append("شکست سقف کوتاه‌مدت")
    if entry < lo:
        short_score += 10; sr.append("شکست کف کوتاه‌مدت")

    if long_score >= MIN_SCORE and long_score > short_score:
        direction, score, reasons = "LONG", min(100, int(long_score)), lr
    elif short_score >= MIN_SCORE and short_score > long_score:
        direction, score, reasons = "SHORT", min(100, int(short_score)), sr
    else:
        return None

    if direction == "LONG":
        stop = entry - 1.2 * a
        risk = entry - stop
        tp1, tp2 = entry + 1.5 * risk, entry + 2.5 * risk
    else:
        stop = entry + 1.2 * a
        risk = stop - entry
        tp1, tp2 = entry - 1.5 * risk, entry - 2.5 * risk

    times = [time_ms(t) for t in trade_list if isinstance(t, dict)]
    times = [x for x in times if x is not None]
    signal_time = max(times) if times else int(datetime.now(timezone.utc).timestamp() * 1000)

    return {
        "base": market["base"], "quote": market["quote"],
        "symbol": market["symbol"], "tabdeal_symbol": market["tabdeal_symbol"],
        "direction": direction, "score": score,
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "atr_percent": atr_pct, "recent_move": move,
        "signal_time_ms": signal_time, "reasons": reasons
    }


def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            x = json.load(f)
            return x if isinstance(x, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(h):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(h[-HISTORY_MAX:], f, ensure_ascii=False, indent=2)


def evaluate(history, mkts):
    done = []
    for s in history:
        if s.get("status") != "OPEN":
            continue
        m = mkts.get((s.get("base"), s.get("quote")))
        if not m:
            continue
        try:
            ts = trades(m)
        except Exception as e:
            print(f"{s.get('base')}: خطا در بررسی نتیجه: {e}")
            continue
        if not ts:
            continue

        st = int(s["signal_time_ms"])
        times = [time_ms(t) for t in ts if isinstance(t, dict)]
        times = [x for x in times if x is not None]
        if not times:
            continue

        if min(times) > st:
            s["status"] = "UNKNOWN"
            s["result"] = "نامشخص"
            s["note"] = "داده 1000 معامله اخیر برای بررسی کامل کافی نبود."
            done.append(s)
            continue

        direction = s.get("direction")
        stop, tp1, tp2 = num(s.get("stop")), num(s.get("tp1")), num(s.get("tp2"))
        ordered = sorted(ts, key=lambda t: time_ms(t) or 0)

        hit = None
        for t in ordered:
            tm, price = time_ms(t), num(val(t, "price", "p"))
            if tm is None or price is None or tm <= st:
                continue
            if direction == "LONG":
                if price <= stop: hit = ("SL", price, tm); break
                if price >= tp2: hit = ("TP2", price, tm); break
                if price >= tp1: hit = ("TP1", price, tm); break
            else:
                if price >= stop: hit = ("SL", price, tm); break
                if price <= tp2: hit = ("TP2", price, tm); break
                if price <= tp1: hit = ("TP1", price, tm); break

        if hit:
            status, price, tm = hit
            s["status"] = status
            s["hit_price"] = price
            s["closed_time_ms"] = tm
            s["result"] = "درست" if status in ("TP1", "TP2") else "نادرست"
            done.append(s)
    return done


def fmt(x):
    x = num(x)
    if x is None: return "-"
    if x >= 1000: return f"{x:,.2f}"
    if x >= 1: return f"{x:,.4f}"
    return f"{x:.8f}".rstrip("0").rstrip(".")


def signal_message(s):
    d = "🟢 LONG" if s["direction"] == "LONG" else "🔴 SHORT"
    reasons = "\n".join(f"• {x}" for x in s["reasons"])
    return (
        "📊 سیگنال Tabdeal\n\n"
        f"ارز: {s['base']}/{s['quote']}\n"
        f"جهت: {d}\n"
        f"امتیاز: {s['score']}٪\n"
        f"ورود: {fmt(s['entry'])}\n"
        f"حد ضرر: {fmt(s['stop'])}\n"
        f"هدف ۱: {fmt(s['tp1'])}\n"
        f"هدف ۲: {fmt(s['tp2'])}\n"
        f"ATR: {s['atr_percent']:.2f}%\n"
        f"حرکت اخیر: {s['recent_move']:.2f}%\n\n"
        f"دلایل:\n{reasons}\n\n"
        "داده: فقط Tabdeal\nتایم‌فریم: 5m + 15m\n"
        "⚠️ فقط سیگنال تحلیلی؛ معامله خودکار انجام نمی‌شود."
    )


def result_message(s):
    return (
        "📌 نتیجه سیگنال Tabdeal\n\n"
        f"ارز: {s.get('base')}/{s.get('quote')}\n"
        f"جهت: {s.get('direction')}\n"
        f"نتیجه: {s.get('status')}\n"
        f"وضعیت: {s.get('result', 'نامشخص')}\n"
        f"قیمت ورود: {fmt(s.get('entry'))}\n"
        f"قیمت برخورد: {fmt(s.get('hit_price'))}"
    )


def main():
    print("شروع بررسی بازار Tabdeal...")
    mkts = markets()
    if not mkts:
        raise RuntimeError("هیچ بازار USDT از 5 ارز انتخاب‌شده در Tabdeal پیدا نشد.")

    history = load_history()

    for s in evaluate(history, mkts):
        try:
            telegram(result_message(s))
        except Exception as e:
            print(f"خطا در ارسال نتیجه: {e}")

    found = []
    for base in TOP5:
        m = mkts.get((base, QUOTE))
        if not m:
            print(f"{base}: بازار USDT پیدا نشد.")
            continue
        try:
            ts = trades(m)
            if not ts:
                print(f"{base}: معامله‌ای دریافت نشد.")
                continue
            s = analyze(m, ts)
            if s:
                found.append(s)
                print(f"{base}: {s['direction']} score={s['score']}")
            else:
                print(f"{base}: سیگنال معتبر پیدا نشد.")
        except Exception as e:
            print(f"{base}: خطا در تحلیل: {e}")
        time.sleep(0.2)

    found.sort(key=lambda x: x["score"], reverse=True)
    existing = {
        (s.get("base"), s.get("quote"), s.get("direction"), s.get("signal_time_ms"))
        for s in history
    }

    for s in found[:MAX_SIGNALS]:
        key = (s["base"], s["quote"], s["direction"], s["signal_time_ms"])
        if key in existing:
            continue
        s["status"] = "OPEN"
        s["result"] = "در انتظار نتیجه"
        history.append(s)
        existing.add(key)
        try:
            telegram(signal_message(s))
        except Exception as e:
            print(f"خطا در ارسال سیگنال {s['base']}: {e}")

    save_history(history)

    closed = [s for s in history if s.get("status") in ("TP1", "TP2", "SL")]
    wins = sum(s.get("status") in ("TP1", "TP2") for s in closed)
    losses = sum(s.get("status") == "SL" for s in closed)
    accuracy = wins / (wins + losses) * 100 if wins + losses else 0
    print(f"آمار: کل={wins + losses}, موفق={wins}, ناموفق={losses}, دقت={accuracy:.1f}%")


if __name__ == "__main__":
    main()
    
