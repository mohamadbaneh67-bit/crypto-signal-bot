import os
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone


# =========================
# Telegram
# =========================

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# =========================
# Tabdeal
# =========================

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"

SESSION = requests.Session()

REQUEST_TIMEOUT = 20

# تعداد معاملات دریافتی برای ساخت کندل
TRADE_LIMIT = 1000

# بین درخواست‌ها کمی مکث می‌کنیم تا فشار زیادی به API وارد نشود
REQUEST_SLEEP = 0.12


# =========================
# ارزهای مهم
# =========================

IMPORTANT_COINS = [
    "BTC",
    "ETH",
    "DOGE",
    "SOL",
    "XRP",
    "BNB",
    "ADA",
    "TRX",
    "AVAX",
    "LINK",
    "DOT",
    "LTC",
    "BCH",
    "ATOM",
    "ETC",
    "FIL",
    "NEAR",
    "APT",
    "ARB",
    "SUI",
    "MATIC",
    "UNI",
    "AAVE",
    "PEPE",
    "SHIB",
]


# =========================
# درخواست به API تبدیل
# =========================

def tabdeal_get(endpoint, params=None):
    response = SESSION.get(
        f"{TABDEAL_BASE}/{endpoint}",
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()
    return response.json()


# =========================
# دریافت بازارها
# =========================

def get_markets():

    data = tabdeal_get("exchangeInfo")

    if isinstance(data, dict):

        if "symbols" in data:
            markets = data["symbols"]

        elif "data" in data:
            markets = data["data"]

        elif "result" in data:
            markets = data["result"]

        else:
            markets = []

    else:
        markets = data

    if not isinstance(markets, list):
        return []

    active = []

    for market in markets:

        if not isinstance(market, dict):
            continue

        status = str(
            market.get("status", "TRADING")
        ).upper()

        if status != "TRADING":
            continue

        if market.get("isSpotTradingAllowed") is False:
            continue

        symbol = (
            market.get("symbol")
            or market.get("pair")
            or market.get("market")
            or market.get("tabdealSymbol")
        )

        if not symbol:
            continue

  symbol = str(symbol).upper()

        base_asset = (
            market.get("baseAsset")
            or market.get("base")
        )

        quote_asset = (
            market.get("quoteAsset")
            or market.get("quote")
        )

        if not base_asset or not quote_asset:

            raw = symbol

            if raw.endswith("USDT"):
                base_asset = raw[:-4]
                quote_asset = "USDT"

            elif raw.endswith("IRT"):
                base_asset = raw[:-3]
                quote_asset = "IRT"

            else:
                base_asset = raw
                quote_asset = ""

        active.append(
            {
                "symbol": symbol,
                "tabdeal_symbol": market.get(
                    "tabdealSymbol",
                    symbol
                ),
                "base": str(base_asset).upper(),
                "quote": str(quote_asset).upper(),
            }
        )

    return active


# =========================
# اولویت بازارها
# =========================

def market_priority(market):

    base = market["base"]
    quote = market["quote"]

    if base in IMPORTANT_COINS:
        coin_priority = IMPORTANT_COINS.index(base)

        if quote == "USDT":
            return (0, coin_priority)

        if quote == "IRT":
            return (1, coin_priority)

        return (2, coin_priority)

    if quote == "USDT":
        return (3, 999)

    if quote == "IRT":
        return (4, 999)

    return (5, 999)


# =========================
# دریافت معاملات تازه
# =========================

def get_trades(symbol):

    data = tabdeal_get(
        "trades",
        {
            "symbol": symbol,
            "limit": TRADE_LIMIT,
        },
    )

    if isinstance(data, dict):

        if "data" in data:
            data = data["data"]

        elif "result" in data:
            data = data["result"]

    if not isinstance(data, list):
        return []

    return data


# =========================
# تبدیل معاملات به کندل
# =========================

def trades_to_candles(trades, minutes):

    rows = []

    for trade in trades:

        if not isinstance(trade, dict):
            continue

        price = (
            trade.get("price")
            or trade.get("p")
        )

        quantity = (
            trade.get("qty")
            or trade.get("quantity")
            or trade.get("q")
        )

        trade_time = (
            trade.get("time")
            or trade.get("timestamp")
            or trade.get("T")
        )

        if price is None or trade_time is None:
            continue

        try:

            price = float(price)

            quantity = (
                float(quantity)
                if quantity is not None
                else 0.0
            )

            trade_time = int(trade_time)

            if trade_time < 100000000000:
                trade_time *= 1000

            rows.append(
                {
                    "time": pd.to_datetime(
                        trade_time,
                        unit="ms",
                        utc=True
                    ),
                    "price": price,
                    "quantity": quantity,
                }
            )

        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df = df.sort_values("time")

    df = df.drop_duplicates(
        subset=["time", "price", "quantity"],
        keep="last"
    )

    df = df.set_index("time")

    rule = f"{minutes}min"

    candles = df["price"].resample(rule).ohlc()

    candles["volume"] = (
        df["quantity"]
        .resample(rule)
        .sum()
    )

    candles = candles.dropna()

    return candles


# =========================
# EMA
# =========================

def ema(series, period):

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


# =========================
# RSI
# =========================

def rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)

    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    result = 100 - (
        100 / (1 + rs)
    )

    return result.fillna(50)


# =========================
# ATR
# =========================

def atr(df, period=14):

    high = df["high"]

    low = df["low"]

    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return true_range.ewm(
        span=period,
        adjust=False
    ).mean()


# =========================
# MACD
# =========================

def macd(series):

    fast = ema(series, 12)

    slow = ema(series, 26)

    macd_line = fast - slow

    signal = ema(macd_line, 9)

    histogram = macd_line - signal

    return (
        macd_line,
        signal,
        histogram
    )


# =========================
# تحلیل یک بازار
# =========================

def analyze_market(market, trades):

    symbol = market["symbol"]

    d5 = trades_to_candles(
        trades,
        5
    )

    d15 = trades_to_candles(
        trades,
        15
    )

    # حداقل داده موردنیاز
    if len(d5) < 35:
        return None

    if len(d15) < 20:
        return None

    # -------------------------
    # اندیکاتورها
    # -------------------------

    d5 = d5.copy()

    d15 = d15.copy()

    d5["ema20"] = ema(
        d5["close"],
        20
    )

    d15["ema20"] = ema(
        d15["close"],
        20
    )

    d15["ema50"] = ema(
        d15["close"],
        50
    )

    d5["rsi"] = rsi(
        d5["close"]
    )

    d15["rsi"] = rsi(
        d15["close"]
    )

    macd5, signal5, hist5 = macd(
        d5["close"]
    )

    macd15, signal15, hist15 = macd(
        d15["close"]
    )

    d5["macd"] = macd5

    d5["macd_signal"] = signal5

    d5["macd_hist"] = hist5

    d15["macd"] = macd15

    d15["macd_signal"] = signal15

    d15["macd_hist"] = hist15

    d5["atr"] = atr(d5)

    d5["volume_ma"] = (
        d5["volume"]
        .rolling(20)
        .mean()
    )

    a5 = d5.iloc[-1]

    a15 = d15.iloc[-1]

    # -------------------------
    # امتیاز
    # -------------------------

    long_score = 0

    short_score = 0

    long_reasons = []

    short_reasons = []

    # روند 15 دقیقه
    if a15["ema20"] > a15["ema50"]:

        long_score += 20

        long_reasons.append(
            "روند 15m صعودی"
        )

    elif a15["ema20"] < a15["ema50"]:

        short_score += 20

        short_reasons.append(
            "روند 15m نزولی"
        )

    # RSI 15m
    if a15["rsi"] > 50:

        long_score += 15

        long_reasons.append(
            f"RSI مثبت ({a15['rsi']:.1f})"
        )

    elif a15["rsi"] < 50:

        short_score += 15

        short_reasons.append(
            f"RSI منفی ({a15['rsi']:.1f})"
        )

    # MACD 15m
    if a15["macd_hist"] > 0:

        long_score += 20

        long_reasons.append(
            "MACD مثبت"
        )

    elif a15["macd_hist"] < 0:

        short_score += 20

        short_reasons.append(
            "MACD منفی"
        )

    # قیمت نسبت به EMA20 در 5m
    if a5["close"] > a5["ema20"]:

        long_score += 15

        long_reasons.append(
            "قیمت 5m بالای EMA20"
        )

    elif a5["close"] < a5["ema20"]:

        short_score += 15

        short_reasons.append(
            "قیمت 5m پایین EMA20"
        )

    # حجم و جهت کندل
    volume_ok = False

    if (
        pd.notna(a5["volume_ma"])
        and a5["volume_ma"] > 0
    ):

        if (
            a5["volume"]
            > a5["volume_ma"] * 1.2
        ):
            volume_ok = True

    if volume_ok:

        if a5["close"] > a5["open"]:

            long_score += 15

            long_reasons.append(
                "حجم و مومنتوم خرید"
            )

        elif a5["close"] < a5["open"]:

            short_score += 15

            short_reasons.append(
                "حجم و مومنتوم فروش"
            )

    # RSI پنج دقیقه
    if a5["rsi"] >= 52:

        long_score += 15

        long_reasons.append(
            f"RSI 5m مثبت ({a5['rsi']:.1f})"
        )

    elif a5["rsi"] <= 48:

        short_score += 15

        short_reasons.append(
            f"RSI 5m منفی ({a5['rsi']:.1f})"
        )

    # -------------------------
    # تصمیم
    # -------------------------

    if (
        long_score >= 75
        and long_score > short_score
    ):

        direction = "LONG"

        score = long_score

        reasons = long_reasons

    elif (
        short_score >= 75
        and short_score > long_score
    ):

        direction = "SHORT"

        score = short_score

        reasons = short_reasons

    else:

        return None

    # -------------------------
    # قیمت تازه
    # -------------------------

    entry = float(a5["close"])

    atr_value = float(a5["atr"])

    if not np.isfinite(atr_value):
        return None

    if atr_value <= 0:
        return None

    # -------------------------
    # حد ضرر و تارگت
    # -------------------------

    risk = atr_value * 1.2

    if direction == "LONG":

        stop = entry - risk

        tp1 = entry + risk * 1.5

        tp2 = entry + risk * 2.5

    else:

        stop = entry + risk

        tp1 = entry - risk * 1.5

        tp2 = entry - risk * 2.5

    # زمان آخرین معامله
    last_trade_time = None

    try:

        times = []

        for trade in trades:

            t = (
                trade.get("time")
                or trade.get("timestamp")
                or trade.get("T")
            )

            if t is not None:
                times.append(int(t))

        if times:

            last_trade_time = max(times)

    except Exception:
        pass

    return {
        "symbol": symbol,
        "base": market["base"],
        "quote": market["quote"],
        "direction": direction,
        "score": score,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "reasons": reasons,
        "last_trade_time": last_trade_time,
    }


# =========================
# فرمت قیمت
# =========================

def format_price(value):

    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:,.4f}"

    if value >= 0.01:
        return f"{value:,.6f}"

    return f"{value:.10f}"


# =========================
# ارسال تلگرام
# =========================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = SESSION.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
        },
        timeout=20,
    )

    response.raise_for_status()


# =========================
# ساخت پیام
# =========================

def make_message(signal):

    if signal["direction"] == "LONG":
        title = "🟢 فرصت LONG"
    else:
        title = "🔴 فرصت SHORT"

    if signal["last_trade_time"]:

        dt = datetime.fromtimestamp(
            signal["last_trade_time"] / 1000,
            tz=timezone.utc
        )

        last_time = dt.strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    else:

        last_time = "نامشخص"

    reasons = "\n".join(
        f"• {reason}"
        for reason in signal["reasons"]
    )

    message = f"""
{title}

🏦 صرافی: تبدیل

💰 ارز: {signal["symbol"]}

⏱ تایم‌فریم: 5m + 15m

📌 ورود:
{format_price(signal["entry"])}

🛑 حد ضرر:
{format_price(signal["stop"])}

🎯 TP1:
{format_price(signal["tp1"])}

🎯 TP2:
{format_price(signal["tp2"])}

📊 امتیاز:
{signal["score"]}/100

📈 دلایل:
{reasons}

🕐 آخرین معامله دریافت‌شده:
{last_time}

⚠️ این پیام فقط تحلیل است.
⚠️ هیچ معامله‌ای توسط ربات انجام نمی‌شود.
⚠️ سود قطعی تضمین نمی‌شود.
""".strip()

    return message


# =========================
# اجرای اصلی
# =========================

def main():

    print("شروع اسکن بازارهای تبدیل...")

    try:

        markets = get_markets()

    except Exception as e:

        print(
            "خطا در دریافت بازارها:",
            e
        )

        return

    if not markets:

        print(
            "هیچ بازار فعالی پیدا نشد."
        )

        return

    markets = sorted(
        markets,
        key=market_priority
    )

    print(
        f"تعداد بازارهای فعال: {len(markets)}"
    )

    print(
        "بازارهای مهم در اولویت هستند."
    )

    signals = []

    seen_symbols = set()

    for index, market in enumerate(markets, 1):

        symbol = market["symbol"]

        if symbol in seen_symbols:
            continue

        seen_symbols.add(symbol)

        try:

            print(
                f"[{index}/{len(markets)}] "
                f"بررسی {symbol}"
            )

            trades = get_trades(symbol)

            if not trades:

                print(
                    f"{symbol}: معامله‌ای دریافت نشد"
                )

                time.sleep(
                    REQUEST_SLEEP
                )

                continue

            signal = analyze_market(
                market,
                trades
            )

            if signal:

                signals.append(signal)

                print(
                    f"{symbol}: "
                    f"{signal['direction']} "
                    f"score={signal['score']}"
                )

            time.sleep(
                REQUEST_SLEEP
            )

        except Exception as e:

            print(
                f"{symbol}: خطا - {e}"
            )

            time.sleep(
                REQUEST_SLEEP
            )

    # مرتب‌سازی بر اساس امتیاز
    signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    print(
        f"تعداد سیگنال‌ها: {len(signals)}"
    )

    # حداکثر 5 سیگنال در هر اجرای ربات
    signals = signals[:5]

    for signal in signals:

        try:

            message = make_message(
                signal
            )

            send_telegram(message)

            print(
                "ارسال شد:",
                signal["symbol"],
                signal["direction"]
            )

            time.sleep(1)

        except Exception as e:

            print(
                "خطا در ارسال تلگرام:",
                e
            )

    print("اسکن تمام شد.")


if __name__ == "__main__":
    main()
