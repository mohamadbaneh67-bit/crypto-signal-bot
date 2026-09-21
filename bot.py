import os
import time
import json
import subprocess
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"
SESSION = requests.Session()
REQUEST_TIMEOUT = 20
TRADE_LIMIT = 1000
REQUEST_SLEEP = 0.12
MAX_SIGNALS = 5
HISTORY_FILE = "signals_history.json"
HISTORY_MAX = 500
SIGNAL_TTL_HOURS = 24

IMPORTANT_COINS = [
    "BTC","ETH","DOGE","SOL","XRP","BNB","ADA","TRX","AVAX","LINK",
    "DOT","LTC","BCH","ATOM","ETC","FIL","NEAR","APT","ARB","SUI",
    "MATIC","UNI","AAVE","PEPE","SHIB"
]

def tabdeal_get(endpoint, params=None):
    r = SESSION.get(f"{TABDEAL_BASE}/{endpoint}",
                    params=params or {}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def get_markets():
    data = tabdeal_get("exchangeInfo")
    if isinstance(data, dict):
        markets = data.get("symbols", data.get("data", data.get("result", [])))
    else:
        markets = data
    if not isinstance(markets, list):
        return []

    active = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        if str(m.get("status", "TRADING")).upper() != "TRADING":
            continue
        if m.get("isSpotTradingAllowed") is False:
            continue

        symbol = m.get("symbol") or m.get("pair") or m.get("market") or m.get("tabdealSymbol")
        if not symbol:
            continue
        symbol = str(symbol).upper()

        base = m.get("baseAsset") or m.get("base")
        quote = m.get("quoteAsset") or m.get("quote")
        if not base or not quote:
            if symbol.endswith("USDT"):
                base, quote = symbol[:-4], "USDT"
            elif symbol.endswith("IRT"):
                base, quote = symbol[:-3], "IRT"
            else:
                base, quote = symbol, ""

        active.append({
            "symbol": symbol,
            "tabdeal_symbol": str(m.get("tabdealSymbol", symbol)),
            "base": str(base).upper(),
            "quote": str(quote).upper()
        })
    return active

def market_priority(m):
    base, quote = m["base"], m["quote"]
    if base in IMPORTANT_COINS:
        p = IMPORTANT_COINS.index(base)
        return (0 if quote == "USDT" else 1 if quote == "IRT" else 2, p)
    return (3 if quote == "USDT" else 4 if quote == "IRT" else 5, 999)

def get_trades(symbol):
    data = tabdeal_get("trades", {"symbol": symbol, "limit": TRADE_LIMIT})
    if isinstance(data, dict):
        data = data.get("data", data.get("result", []))
    return data if isinstance(data, list) else []

def tv(t, *keys):
    for k in keys:
        if t.get(k) is not None:
            return t[k]
    return None

def trade_time_ms(t):
    x = tv(t, "time", "timestamp", "T", "createdAt", "created_at")
    if x is None:
        return None
    try:
        x = float(x)
        return int(x * 1000 if x < 10_000_000_000 else x)
    except Exception:
        return None

def trades_to_candles(trades, minutes):
    rows = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        p, q, ts = tv(t, "price", "p"), tv(t, "qty", "quantity", "q", "amount", "volume"), trade_time_ms(t)
        if p is None or ts is None:
            continue
        try:
            rows.append((pd.to_datetime(ts, unit="ms", utc=True), float(p), float(q or 0)))
        except Exception:
            pass

    if not rows:
        return pd.DataFrame()

    d = pd.DataFrame(rows, columns=["time", "price", "volume"]).sort_values("time").set_index("time")
    c = d["price"].resample(f"{minutes}min").ohlc()
    c["volume"] = d["volume"].resample(f"{minutes}min").sum()
    return c.dropna(subset=["open","high","low","close"]).reset_index()

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def atr(d, n=14):
    prev = d["close"].shift(1)
    tr = pd.concat([
        d["high"]-d["low"],
        (d["high"]-prev).abs(),
        (d["low"]-prev).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def analyze_market(market, trades):
    d5, d15 = trades_to_candles(trades, 5), trades_to_candles(trades, 15)
    if len(d5) < 35 or len(d15) < 20:
        return None

    for d in (d5, d15):
        d["ema20"] = ema(d["close"], 20)
        d["ema50"] = ema(d["close"], 50)
        d["rsi"] = rsi(d["close"])
        d["atr"] = atr(d)
    d5["vol_ma"] = d5["volume"].rolling(20).mean()

    macd_line = ema(d15["close"], 12) - ema(d15["close"], 26)
    d15["macd_hist"] = macd_line - ema(macd_line, 9)

    a15, a5 = d15.iloc[-2], d5.iloc[-2]
    long_score = short_score = 0
    lr, sr = [], []

    if a15.ema20 > a15.ema50: long_score += 20; lr.append("روند 15 دقیقه صعودی")
    if a15.ema20 < a15.ema50: short_score += 20; sr.append("روند 15 دقیقه نزولی")
    if a15.rsi > 50: long_score += 15; lr.append("RSI مثبت")
    if a15.rsi < 50: short_score += 15; sr.append("RSI منفی")
    if a15.macd_hist > 0: long_score += 20; lr.append("MACD مثبت")
    if a15.macd_hist < 0: short_score += 20; sr.append("MACD منفی")
    if a5.close > a5.ema20: long_score += 15; lr.append("قیمت 5 دقیقه بالای EMA20")
    if a5.close < a5.ema20: short_score += 15; sr.append("قیمت 5 دقیقه زیر EMA20")

    if pd.notna(a5.vol_ma) and a5.volume > a5.vol_ma * 1.2:
        if a5.close > a5.open: long_score += 15; lr.append("حجم بالاتر از میانگین")
        if a5.close < a5.open: short_score += 15; sr.append("حجم بالاتر از میانگین")

    if a5.rsi >= 52: long_score += 15; lr.append("مومنتوم 5 دقیقه‌ای")
    if a5.rsi <= 48: short_score += 15; sr.append("مومنتوم 5 دقیقه‌ای")

    entry, av = float(a5.close), float(a5.atr)
    if not np.isfinite(av) or av <= 0:
        return None

    if long_score >= 75 and long_score > short_score:
        direction, score, reasons = "LONG", long_score, lr
        stop = entry - 1.2 * av
        risk = entry - stop
        tp1, tp2 = entry + 1.5*risk, entry + 2.5*risk
    elif short_score >= 75 and short_score > long_score:
        direction, score, reasons = "SHORT", short_score, sr
        stop = entry + 1.2 * av
        risk = stop - entry
        tp1, tp2 = entry - 1.5*risk, entry - 2.5*risk
    else:
        return None

    ts = [trade_time_ms(t) for t in trades if isinstance(t, dict)]
    ts = [x for x in ts if x is not None]
    return {
        "symbol": market["symbol"],
        "tabdeal_symbol": market["tabdeal_symbol"],
        "base": market["base"],
        "quote": market["quote"],
        "direction": direction,
        "score": int(score),
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "signal_time_ms": max(ts) if ts else int(datetime.now(timezone.utc).timestamp()*1000),
        "reasons": reasons
    }

def fmt(x):
    x = float(x)
    if x >= 1000: return f"{x:,.2f}"
    if x >= 1: return f"{x:,.4f}"
    if x >= 0.01: return f"{x:,.6f}"
    return f"{x:.10f}"

def send_telegram(text):
    r = SESSION.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text}, timeout=20)
    r.raise_for_status()

def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-HISTORY_MAX:], f, ensure_ascii=False, indent=2)

def make_signal_message(s):
    reasons = "\n".join("✅ " + x for x in s["reasons"])
    when = datetime.fromtimestamp(s["signal_time_ms"]/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "📢 سیگنال تحلیل تبدیل\n\n"
        f"🪙 {s['base']}/{s['quote']}\n"
        f"📌 جهت: {'خرید (LONG)' if s['direction']=='LONG' else 'فروش (SHORT)'}\n"
        "⏱ تایم‌فریم: 5m + 15m\n\n"
        f"📍 ورود: {fmt(s['entry'])}\n"
        f"🛑 حد ضرر: {fmt(s['stop'])}\n"
        f"🎯 TP1: {fmt(s['tp1'])}\n"
        f"🎯 TP2: {fmt(s['tp2'])}\n"
        f"📊 امتیاز: {s['score']}/100\n"
        f"🕐 زمان: {when}\n\n"
        f"دلایل:\n{reasons}\n\n"
        "⚠️ فقط تحلیل است؛ معامله خودکار انجام نمی‌شود."
    )

def make_result_message(x):
    label = {
        "TP1": "✅ درست — TP1",
        "TP2": "✅ درست — TP2",
        "SL": "❌ نادرست — حد ضرر",
        "EXPIRED": "⚪ نامشخص — منقضی شد"
    }[x["status"]]
    return (
        "📊 نتیجه سیگنال\n\n"
        f"🪙 {x['symbol']}\n"
        f"📌 جهت: {'خرید' if x['direction']=='LONG' else 'فروش'}\n"
        f"{label}\n"
        f"💵 ورود: {fmt(x['entry'])}\n"
        f"🎯 قیمت نتیجه: {fmt(x.get('hit_price', x['entry']))}\n"
        f"⏱ مدت: {x.get('duration_minutes', 0)} دقیقه"
    )

def evaluate_history(history, markets):
    completed = []
    for x in history:
        if x.get("status") != "OPEN":
            continue

        m = markets.get(x.get("symbol"))
        if not m:
            continue

        try:
            trades = get_trades(m["tabdeal_symbol"])
        except Exception:
            continue

        signal_ms = int(x["signal_time_ms"])
        direction = x["direction"]
        found = None

        for t in sorted(trades, key=lambda z: trade_time_ms(z) or 0):
            ts = trade_time_ms(t)
            price = tv(t, "price", "p")
            if ts is None or price is None or ts <= signal_ms:
                continue
            try:
                price = float(price)
            except Exception:
                continue

            if direction == "LONG":
                if price <= float(x["stop"]): found = ("SL", price, ts); break
                if price >= float(x["tp2"]): found = ("TP2", price, ts); break
                if price >= float(x["tp1"]): found = ("TP1", price, ts); break
            else:
                if price >= float(x["stop"]): found = ("SL", price, ts); break
                if price <= float(x["tp2"]): found = ("TP2", price, ts); break
                if price <= float(x["tp1"]): found = ("TP1", price, ts); break

        if found:
            status, price, ts = found
            x["status"] = status
            x["result"] = "درست" if status in ("TP1", "TP2") else "نادرست"
            x["hit_price"] = price
            x["closed_time"] = datetime.fromtimestamp(ts/1000, tz=timezone.utc).isoformat()
            x["duration_minutes"] = round((ts-signal_ms)/60000, 2)
            completed.append(x)
            continue

        age = datetime.now(timezone.utc) - datetime.fromtimestamp(signal_ms/1000, tz=timezone.utc)
        if age > timedelta(hours=SIGNAL_TTL_HOURS):
            x["status"] = "EXPIRED"
            x["result"] = "نامشخص"
            x["closed_time"] = datetime.now(timezone.utc).isoformat()
            x["duration_minutes"] = round(age.total_seconds()/60, 2)
            completed.append(x)

    return completed

def git_persist():
    if not os.environ.get("GITHUB_TOKEN"):
        print("GITHUB_TOKEN موجود نیست؛ تاریخچه فقط در همین اجرا ذخیره شد.")
        return
    try:
        subprocess.run(["git","config","user.name","tabdeal-bot"], check=True)
        subprocess.run(["git","config","user.email","actions@github.com"], check=True)
        subprocess.run(["git","add",HISTORY_FILE], check=True)
        if subprocess.run(["git","diff","--cached","--quiet"]).returncode == 0:
            return
        subprocess.run(["git","commit","-m","Update signal results"], check=True)
        subprocess.run(["git","push"], check=True)
    except Exception as e:
        print("خطا در ذخیره تاریخچه GitHub:", e)

def main():
    history = load_history()
    market_list = get_markets()
    markets = {m["symbol"]: m for m in market_list}

    completed = evaluate_history(history, markets)
    for x in completed:
        try:
            send_telegram(make_result_message(x))
        except Exception as e:
            print("خطا در ارسال نتیجه:", e)

    signals = []
    for m in sorted(market_list, key=market_priority):
        try:
            trades = get_trades(m["tabdeal_symbol"])
            if trades:
                s = analyze_market(m, trades)
                if s:
                    signals.append(s)
        except Exception as e:
            print(f"{m['symbol']}: خطا - {e}")
        time.sleep(REQUEST_SLEEP)

    signals.sort(key=lambda s: s["score"], reverse=True)

    for s in signals[:MAX_SIGNALS]:
        duplicate = any(
            x.get("status") == "OPEN"
            and x.get("symbol") == s["symbol"]
            and x.get("direction") == s["direction"]
            and x.get("signal_time_ms") == s["signal_time_ms"]
            for x in history
        )
        if duplicate:
            continue

        send_telegram(make_signal_message(s))
        history.append({
            **{k:s[k] for k in ("symbol","direction","score","entry","stop","tp1","tp2","signal_time_ms")},
            "status": "OPEN",
            "result": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        time.sleep(1)

    save_history(history)
    git_persist()
    print(f"اسکن تمام شد | سیگنال جدید: {len(signals[:MAX_SIGNALS])} | نتیجه جدید: {len(completed)}")

if __name__ == "__main__":
    main()
                
