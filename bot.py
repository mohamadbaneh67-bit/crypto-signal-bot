import os
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"


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
        return data.get("data", data.get("trades", []))

    return data


def trades_to_candles(trades, minutes):
    rows = []

    for t in trades:
        if not isinstance(t, dict):
            continue

        price = t.get("price") or t.get("p")
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

    if len(rows) < 30:
        return None

    df = pd.DataFrame(rows)
    df = df.sort_values("time")
    df = df.set_index("time")

    rule = f"{minutes}min"

    candles = df["price"].resample(rule).ohlc()
    candles["volume"] = df["volume"].resample(rule).sum()

    return candles.dropna()


def ema(s, n):
    return s.ewm(
        span=n,
        adjust=False
    ).mean()


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


def prepare(df):
    df = df.copy()

    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"])
    df["atr"] = atr(df)

    df["vol_ma"] = df["volume"].rolling(20).mean()

    return df


def analyze(symbol):
    trades = get_trades(symbol, 1000)

    d5 = trades_to_candles(trades, 5)
    d15 = trades_to_candles(trades, 15)

    if d5 is None or d15 is None:
        return None

    # برای EMA50 و MACD داده کافی لازم است
    if len(d5) < 55 or len(d15) < 55:
        return None

    d5 = prepare(d5)
    d15 = prepare(d15)

    _, _, macd_hist = macd(d15["close"])
    d15["macd_hist"] = macd_hist

    # آخرین کندل کامل
    a5 = d5.iloc[-2]
    a15 = d15.iloc[-2]

    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []

    # -------------------------
    # روند 15 دقیقه
    # -------------------------

    if a15["ema20"] > a15["ema50"]:
        long_score += 20
        long_reasons.append("روند 15 دقیقه صعودی")

    if a15["ema20"] < a15["ema50"]:
        short_score += 20
        short_reasons.append("روند 15 دقیقه نزولی")

    # -------------------------
    # RSI سخت‌گیرانه‌تر
    # -------------------------

    if a15["rsi"] >= 52:
        long_score += 15
        long_reasons.append("RSI مثبت")

    if a15["rsi"] <= 48:
        short_score += 15
        short_reasons.append("RSI منفی")

    # -------------------------
    # MACD
    # -------------------------

    if a15["macd_hist"] > 0:
        long_score += 20
        long_reasons.append("MACD مثبت")

    if a15["macd_hist"] < 0:
        short_score += 20
        short_reasons.append("MACD منفی")

    # -------------------------
    # تأیید تایم‌فریم 5 دقیقه
    # -------------------------

    if (
        a5["close"] > a5["ema20"]
        and a5["ema20"] > a5["ema50"]
    ):
        long_score += 15
        long_reasons.append(
            "قیمت و EMAهای 5 دقیقه صعودی"
        )

    if (
        a5["close"] < a5["ema20"]
        and a5["ema20"] < a5["ema50"]
    ):
        short_score += 15
        short_reasons.append(
            "قیمت و EMAهای 5 دقیقه نزولی"
        )

    # -------------------------
    # حجم
    # -------------------------

    volume_confirmed = False

    if pd.notna(a5["vol_ma"]):
        if a5["volume"] > a5["vol_ma"] * 1.2:
            volume_confirmed = True

            if a5["close"] > a5["open"]:
                long_score += 15
                long_reasons.append(
                    "حجم بالاتر از میانگین"
                )

            elif a5["close"] < a5["open"]:
                short_score += 15
                short_reasons.append(
                    "حجم بالاتر از میانگین"
                )

    # -------------------------
    # مومنتوم
    # -------------------------

    if a5["rsi"] >= 55:
        long_score += 15
        long_reasons.append(
            "مومنتوم 5 دقیقه‌ای قوی"
        )

    if a5["rsi"] <= 45:
        short_score += 15
        short_reasons.append(
            "مومنتوم 5 دقیقه‌ای قوی"
        )

    price = float(a5["close"])
    atr_value = float(a5["atr"])

    if not np.isfinite(atr_value) or atr_value <= 0:
        return None

    # -------------------------
    # فیلتر نهایی LONG
    # -------------------------

    long_structure = (
        a15["ema20"] > a15["ema50"]
        and a15["macd_hist"] > 0
        and a15["rsi"] >= 52
        and a5["close"] > a5["ema20"]
    )

    if (
        long_structure
        and long_score >= 80
        and long_score > short_score
    ):
        stop = price - 1.2 * atr_value
        risk = price - stop

        return (
            "LONG",
            long_score,
            price,
            stop,
            price + 1.5 * risk,
            price + 2.5 * risk,
            long_reasons
        )

    # -------------------------
    # فیلتر نهایی SHORT
    # -------------------------

    short_structure = (
        a15["ema20"] < a15["ema50"]
        and a15["macd_hist"] < 0
        and a15["rsi"] <= 48
        and a5["close"] < a5["ema20"]
    )

    if (
        short_structure
        and short_score >= 80
        and short_score > long_score
    ):
        stop = price + 1.2 * atr_value
        risk = stop - price

        return (
            "SHORT",
            short_score,
            price,
            stop,
            price - 1.5 * risk,
            price - 2.5 * risk,
            short_reasons
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

    # حداکثر 60 بازار برای جلوگیری از فشار زیاد به API
    symbols = symbols[:60]

    for symbol in symbols:

        try:
            result = analyze(symbol)

            if result:
                found.append((symbol, result))

        except Exception as e:
            print(
                "ERROR",
                symbol,
                str(e)
            )

    now = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    # -------------------------
    # بدون سیگنال
    # -------------------------

    if not found:

        send(
            "🔎 اسکن صرافی تبدیل انجام شد\n\n"
            "در این اسکن سیگنال قوی پیدا نشد.\n\n"
            f"📊 بازارهای بررسی‌شده: {len(symbols)}\n"
            "🎯 حداقل امتیاز سیگنال: 80/100\n"
            "⏱ تایم‌فریم: 5m + 15m\n"
            f"🕐 زمان: {now}\n\n"
            "⚠️ نبودن سیگنال به معنی نبودن فرصت قطعی "
            "در بازار نیست؛ فیلترهای ربات سخت‌گیرانه‌تر شده‌اند."
        )

        return

    # -------------------------
    # ارسال سیگنال‌ها
    # -------------------------

    for symbol, result in found:

        (
            side,
            score,
            entry,
            stop,
            tp1,
            tp2,
            reasons
        ) = result

        emoji = (
            "🟢"
            if side == "LONG"
            else "🔴"
        )

        text = (
            f"{emoji} فرصت {side}\n"
            f"🏦 صرافی: تبدیل\n"
            f"💰 ارز: {symbol}\n"
            f"⏱ تایم‌فریم: 5m + 15m\n\n"

            f"📍 ورود: {fmt(entry)}\n"
            f"🛑 حد ضرر: {fmt(stop)}\n"
            f"🎯 هدف 1: {fmt(tp1)}\n"
            f"🎯 هدف 2: {fmt(tp2)}\n"
            f"⚖️ R:R تقریبی: 1:2.5\n"
            f"📊 امتیاز شرایط: {score}/100\n\n"

            "دلایل:\n"
            + "\n".join(
                "✅ " + x
                for x in reasons
            )

            + "\n\n"
            "⚠️ این فقط تحلیل بازار است و "
            "تضمین سود یا توصیه معاملاتی نیست."
        )

        send(text)


if __name__ == "__main__":
    main()
