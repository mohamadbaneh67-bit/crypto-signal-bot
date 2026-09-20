import os
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"

# فقط بازارهای تبدیل
QUOTE_PRIORITY = ["USDT", "IRT"]

def tabdeal_get(endpoint, params=None):
    r = requests.get(
        f"{TABDEAL_BASE}/{endpoint}",
        params=params or {},
        timeout=20
    )
    r.raise_for_status()
    return r.json()


def get_markets():
    data = tabdeal_get("exchangeInfo")

    if isinstance(data, dict):
        markets = data.get("symbols", data.get("data", []))
    else:
        markets = data

    result = []

    for m in markets:
        if not isinstance(m, dict):
            continue

        symbol = (
            m.get("symbol")
            or m.get("pair")
            or m.get("market")
            or m.get("tabdealSymbol")
        )

        if not symbol:
            continue

        symbol = str(symbol).upper()

        # فقط بازارهای USDT و IRT
        if symbol.endswith("USDT") or symbol.endswith("IRT"):
            result.append(symbol)

    return sorted(set(result))


def get_trades(symbol, limit=1000):
    data = tabdeal_get(
        "trades",
        {
            "symbol": symbol,
            "limit": limit
        }
    )

    if isinstance(data, dict):
        trades = data.get("data", data.get("trades", []))
    else:
        trades = data

    return trades


def trades_to_candles(trades, minutes):
    rows = []

    for t in trades:
        if not isinstance(t, dict):
            continue

        price = (
            t.get("price")
            or t.get("p")
        )

        quantity = (
            t.get("qty")
            or t.get("quantity")
            or t.get("q")
        )

        trade_time = (
            t.get("time")
            or t.get("timestamp")
            or t.get("T")
        )

        if price is None or quantity is None or trade_time is None:
            continue

        try:
            price = float(price)
            quantity = float(quantity)
            trade_time = int(float(trade_time))

            # تبدیل timestamp ثانیه به میلی‌ثانیه
            if trade_time < 10_000_000_000:
                trade_time *= 1000

            rows.append({
                "time": pd.to_datetime(
                    trade_time,
                    unit="ms",
                    utc=True
                ),
                "price": price,
                "volume": quantity
            })

        except Exception:
            continue

    if len(rows) < 20:
        return None

    df = pd.DataFrame(rows)
    df = df.sort_values("time")
    df = df.set_index("time")

    rule = f"{minutes}min"

    candles = df["price"].resample(rule).ohlc()
    candles["volume"] = df["volume"].resample(rule).sum()

    candles = candles.dropna()

    return candles


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()

    gain = d.clip(lower=0).ewm(
        alpha=1 / n,
        adjust=False
    ).mean()

    loss = (-d.clip(upper=0)).ewm(
        alpha=1 / n,
        adjust=False
    ).mean()

    rs = gain / loss.replace(0, np.nan)

    return 100 - (100 / (1 + rs))


def atr(df, n=14):
    prev = df["close"].shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs()
    ], axis=1).max(axis=1)

    return tr.ewm(
        alpha=1 / n,
        adjust=False
    ).mean()


def macd(s):
    line = ema(s, 12) - ema(s, 26)
    signal = ema(line, 9)

    return line, signal, line - signal


def analyze(symbol):
    trades = get_trades(symbol, 1000)

    d5 = trades_to_candles(trades, 5)
    d15 = trades_to_candles(trades, 15)

    if d5 is None or d15 is None:
        return None

    if len(d5) < 55 or len(d15) < 55:
        return None

    for d in (d5, d15):
        d["ema20"] = ema(d["close"], 20)
        d["ema50"] = ema(d["close"], 50)
        d["rsi"] = rsi(d["close"])
        d["atr"] = atr(d)

        d["vol_ma"] = d["volume"].rolling(20).mean()

    mline, msignal, mhist = macd(d15["close"])
    d15["macd_hist"] = mhist

    a15 = d15.iloc[-2]
    a5 = d5.iloc[-2]

    long_score = 0
    short_score = 0

    lr = []
    sr = []

    if a15["ema20"] > a15["ema50"]:
        long_score += 20
        lr.append("روند 15 دقیقه صعودی")

    if a15["ema20"] < a15["ema50"]:
        short_score += 20
        sr.append("روند 15 دقیقه نزولی")

    if a15["rsi"] > 50:
        long_score += 15
        lr.append("RSI مثبت")

    if a15["rsi"] < 50:
        short_score += 15
        sr.append("RSI منفی")

    if a15["macd_hist"] > 0:
        long_score += 20
        lr.append("MACD مثبت")

    if a15["macd_hist"] < 0:
        short_score += 20
        sr.append("MACD منفی")

    if a5["close"] > a5["ema20"]:
        long_score += 15
        lr.append("قیمت 5 دقیقه بالای EMA20")

    if a5["close"] < a5["ema20"]:
        short_score += 15
        sr.append("قیمت 5 دقیقه زیر EMA20")

    if (
        pd.notna(a5["vol_ma"])
        and a5["volume"] > a5["vol_ma"] * 1.2
    ):
        if a5["close"] > a5["open"]:
            long_score += 15
            lr.append("حجم بالاتر از میانگین")

        elif a5["close"] < a5["open"]:
            short_score += 15
            sr.append("حجم بالاتر از میانگین")

    if a5["rsi"] >= 52:
        long_score += 15
        lr.append("مومنتوم 5 دقیقه‌ای")

    if a5["rsi"] <= 48:
        short_score += 15
        sr.append("مومنتوم 5 دقیقه‌ای")

    price = float(a5["close"])
    a = float(a5["atr"])

    if not np.isfinite(a) or a <= 0:
        return None

    if long_score >= 75 and long_score > short_score:

        stop = price - 1.2 * a
        risk = price - stop

        return (
            "LONG",
            long_score,
            price,
            stop,
            price + 1.5 * risk,
            price + 2.5 * risk,
            lr
        )

    if short_score >= 75 and short_score > long_score:

        stop = price + 1.2 * a
        risk = stop - price

        return (
            "SHORT",
            short_score,
            price,
            stop,
            price - 1.5 * risk,
            price - 2.5 * risk,
            sr
        )

    return None


def fmt(x):
    if x >= 1000:
        return f"{x:,.2f}"

    if x >= 1:
        return f"{x:,.4f}"

    return f"{x:.8f}".rstrip("0").rstrip(".")


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=15
    )

    r.raise_for_status()


def main():

    try:
        symbols = get_markets()
    except Exception as e:
        send(
            "❌ خطا در دریافت بازارهای صرافی تبدیل\n\n"
            f"{e}"
        )
        return

    found = []

    # محدودیت برای جلوگیری از فشار زیاد به API
    symbols = symbols[:60]

    for symbol in symbols:

        try:
            result = analyze(symbol)

            if result:
                found.append((symbol, result))

        except Exception as e:
            print("ERROR", symbol, e)

    now = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    if not found:

        send(
            "🔎 اسکن صرافی تبدیل انجام شد\n\n"
            "در این اسکن سیگنال قوی پیدا نشد.\n\n"
            f"تعداد بازارهای بررسی‌شده: {len(symbols)}\n"
            f"زمان: {now}"
        )

        return

    for symbol, s in found:

        side, score, entry, stop, tp1, tp2, reasons = s

        emoji = "🟢" if side == "LONG" else "🔴"

        text = (
            f"{emoji} فرصت {side}\n"
            f"🏦 صرافی: تبدیل\n"
            f"ارز: {symbol}\n"
            f"تایم‌فریم: 5m + 15m\n\n"

            f"📍 ورود: {fmt(entry)}\n"
            f"🛑 حد ضرر: {fmt(stop)}\n"
            f"🎯 هدف 1: {fmt(tp1)}\n"
            f"🎯 هدف 2: {fmt(tp2)}\n"
            f"⚖️ R:R تقریبی: 1:2.5\n"
            f"📊 امتیاز شرایط: {score}/100\n\n"

            "دلایل:\n"
            + "\n".join(
                "✅ " + x for x in reasons
            )

            + "\n\n"
            "⚠️ این فقط تحلیل بازار است و تضمین سود نیست."
        )

        send(text)


if __name__ == "__main__":
    main()
