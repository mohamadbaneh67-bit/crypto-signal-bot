import os
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"

COINS = ["BTC", "ETH", "BNB", "SOL", "XRP"]
QUOTE = "USDT"

TRADE_LIMIT = 1000
TIMEOUT = 20

MIN_SCORE = 70
MAX_SIGNALS = 2

HISTORY_FILE = "signals_history.json"

session = requests.Session()


def tabdeal_api(endpoint, params=None):
    url = TABDEAL_BASE + "/" + endpoint

    response = session.get(
        url,
        params=params or {},
        timeout=TIMEOUT
    )

    response.raise_for_status()
    return response.json()


def send_telegram(message):
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_BOT_TOKEN وجود ندارد.")
        return False

    if not CHAT_ID:
        print("TELEGRAM_CHAT_ID وجود ندارد.")
        return False

    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    response = session.post(
        url,
        json=data,
        timeout=TIMEOUT
    )

    print("Telegram HTTP:", response.status_code)

    if not response.ok:
        print("Telegram:", response.text)
        return False

    print("پیام تلگرام ارسال شد.")
    return True


def get_value(data, *names):
    if not isinstance(data, dict):
        return None

    for name in names:
        if data.get(name) is not None:
            return data.get(name)

    return None


def to_number(value):
    try:
        if value is None or value == "":
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def get_timestamp(data):
    value = to_number(
        get_value(
            data,
            "time",
            "timestamp",
            "T",
            "createdAt",
            "created_at"
        )
    )

    if value is None:
        return None

    if value < 10000000000:
        value = value * 1000

    return int(value)


def get_list(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ["data", "result", "items", "symbols"]:
            value = data
            def analyze_market(market, trades):
    candles_5m = make_candles(trades, 5)
    candles_15m = make_candles(trades, 15)

    print(
        market["base"],
        "5m:",
        len(candles_5m),
        "15m:",
        len(candles_15m)
    )

    if len(candles_5m) < 60:
        print(
            market["base"],
            "داده 5 دقیقه‌ای کافی نیست."
        )
        return None

    if len(candles_15m) < 30:
        print(
            market["base"],
            "داده 15 دقیقه‌ای کافی نیست."
        )
        return None

    candles_5m = add_indicators(candles_5m)
    candles_15m = add_indicators(candles_15m)

    current_5m = candles_5m.iloc[-2]
    current_15m = candles_15m.iloc[-2]

    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []

    if current_15m["ema20"] > current_15m["ema50"]:
        long_score += 20
        long_reasons.append("روند 15 دقیقه‌ای صعودی")

    elif current_15m["ema20"] < current_15m["ema50"]:
        short_score += 20
        short_reasons.append("روند 15 دقیقه‌ای نزولی")

    rsi_15 = to_number(current_15m["rsi"])

    if rsi_15 is not None:

        if rsi_15 > 50:
            long_score += 15
            long_reasons.append("RSI 15 دقیقه‌ای مثبت")

        elif rsi_15 < 50:
            short_score += 15
            short_reasons.append("RSI 15 دقیقه‌ای منفی")

    macd_15 = to_number(
        current_15m["macd_hist"]
    )

    if macd_15 is not None:

        if macd_15 > 0:
            long_score += 20
            long_reasons.append("MACD 15 دقیقه‌ای مثبت")

        elif macd_15 < 0:
            short_score += 20
            short_reasons.append("MACD 15 دقیقه‌ای منفی")

    close_5 = to_number(
        current_5m["close"]
    )

    ema20_5 = to_number(
        current_5m["ema20"]
    )

    if close_5 is not None and ema20_5 is not None:

        if close_5 > ema20_5:
            long_score += 15
            long_reasons.append("قیمت بالای EMA20")

        elif close_5 < ema20_5:
            short_score += 15
            short_reasons.append("قیمت زیر EMA20")

    rsi_5 = to_number(
        current_5m["rsi"]
    )

    if rsi_5 is not None:

        if rsi_5 >= 52:
            long_score += 15
            long_reasons.append("مومنتوم 5 دقیقه‌ای صعودی")

        elif rsi_5 <= 48:
            short_score += 15
            short_reasons.append("مومنتوم 5 دقیقه‌ای نزولی")

    volume = to_number(
        current_5m["volume"]
    )

    volume_average = to_number(
        current_5m["volume_average"]
    )

    candle_open = to_number(
        current_5m["open"]
    )

    if (
        volume is not None
        and volume_average is not None
        and volume_average > 0
        and volume > volume_average * 1.2
    ):

        if close_5 > candle_open:
            long_score += 15
            long_reasons.append("حجم معاملات بالاتر از میانگین")

        elif close_5 < candle_open:
            short_score += 15
            short_reasons.append("حجم معاملات بالاتر از میانگین")

    entry = close_5

    atr_value = to_number(
        current_5m["atr"]
    )

    if entry is None or atr_value is None:
        return None

    if entry <= 0 or atr_value <= 0:
        return None

    atr_percent = (
        atr_value / entry
    ) * 100

    if atr_percent < 0.10:
        print(
            market["base"],
            "نوسان کافی ندارد."
        )
        return None

    if len(candles_5m) >= 8:

        old_price = to_number(
            candles_5m.iloc[-8]["close"]
        )

        if old_price is not None and old_price > 0:

            price_move = (
                abs(entry - old_price)
                / old_price
            ) * 100

        else:
            price_move = 0

    else:
        price_move = 0

    if price_move < 0.05:
        print(
            market["base"],
            "حرکت اخیر کم است."
        )
        return None

    if len(candles_5m) >= 7:

        recent_high = float(
            candles_5m.iloc[-7:-2]["high"].max()
        )

        recent_low = float(
            candles_5m.iloc[-7:-2]["low"].min()
        )

        if entry > recent_high:
            long_score += 10
            long_reasons.append("شکست سقف کوتاه‌مدت")

        if entry < recent_low:
            short_score += 10
            short_reasons.append("شکست کف کوتاه‌مدت")

    if (
        long_score >= MIN_SCORE
        and long_score > short_score
    ):

        direction = "LONG"
        score = min(100, int(long_score))
        reasons = long_reasons

    elif (
        short_score >= MIN_SCORE
        and short_score > long_score
    ):

        direction = "SHORT"
        score = min(100, int(short_score))
        reasons = short_reasons

    else:

        print(
            market["base"],
            "سیگنال معتبر نیست.",
            "LONG=",
            long_score,
            "SHORT=",
            short_score
        )

        return None

    if direction == "LONG":

        stop = entry - (
            atr_value * 1.2
        )

        risk = entry - stop

        take_profit_1 = entry + (
            risk * 1.5
        )

        take_profit_2 = entry + (
            risk * 2.5
        )

    else:

        stop = entry + (
            atr_value * 1.2
        )

        risk = stop - entry

        take_profit_1 = entry - (
            risk * 1.5
        )

        take_profit_2 = entry - (
            risk * 2.5
        )

    timestamp = get_timestamp(
        trades[-1]
    ) if trades else None

    if timestamp is None:
        timestamp = int(
            datetime.now(
                timezone.utc
            ).timestamp() * 1000
        )

    return {
        "base": market["base"],
        "quote": market["quote"],
        "symbol": market["symbol"],
        "direction": direction,
        "score": score,
        "entry": entry,
        "stop": stop,
        "tp1": take_profit_1,
        "tp2": take_profit_2,
        "atr_percent": atr_percent,
        "price_move": price_move,
        "signal_time": timestamp,
        "reasons": reasons
    }


def format_price(value):
    value = to_number(value)

    if value is None:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:,.4f}"

    if value >= 0.01:
        return f"{value:,.6f}"

    return f"{value:.8f}"


def format_percent(value):
    value = to_number(value)

    if value is None:
        return "-"

    return f"{value:.2f}%"


def create_signal_message(signal):

    if signal["direction"] == "LONG":
        direction = "خرید / LONG"
    else:
        direction = "فروش / SHORT"

    reasons = signal.get(
        "reasons",
        []
    )

    if reasons:
        reason_text = "\n".join(
            "- " + reason
            for reason in reasons
        )
    else:
        reason_text = "- ترکیب شاخص‌های تکنیکال"

    lines = [
        "🚨 سیگنال جدید Tabdeal",
        "",
        "ارز: " + signal["base"] + "/" + signal["quote"],
        "جهت: " + direction,
        "امتیاز: " + str(signal["score"]) + " از 100",
        "",
        "💰 ورود:",
        format_price(signal["entry"]),
        "",
        "🛑 حد ضرر:",
        format_price(signal["stop"]),
        "",
        "🎯 هدف سود اول:",
        format_price(signal["tp1"]),
        "",
        "🎯 هدف سود دوم:",
        format_price(signal["tp2"]),
        "",
        "📊 ATR: " + format_percent(signal["atr_percent"]),
        "📈 حرکت اخیر: " + format_percent(signal["price_move"]),
        "",
        "🔎 دلایل:",
        reason_text,
        "",
        "⚠️ این ربات فقط تحلیل ارائه می‌کند و معامله انجام نمی‌دهد."
    ]

    return "\n".join(lines)


def load_history():

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

            if isinstance(data, list):
                return data

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):
        pass

    return []


def save_history(history):

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            history[-500:],
            file,
            ensure_ascii=False,
            indent=2
        )


def is_duplicate(signal, history):

    for old_signal in history:

        if not isinstance(old_signal, dict):
            continue

        if old_signal.get("base") != signal.get("base"):
            continue

        if old_signal.get("direction") != signal.get("direction"):
            continue

        old_time = to_number(
            old_signal.get("signal_time")
        )

        new_time = to_number(
            signal.get("signal_time")
        )

        if old_time is None or new_time is None:
            continue

        difference = abs(
            new_time - old_time
        )

        if difference < 15 * 60 * 1000:
            return True

    return False
    def add_signal(history, signal):
    item = {
        "base": signal["base"],
        "quote": signal["quote"],
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "score": signal["score"],
        "entry": signal["entry"],
        "stop": signal["stop"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "atr_percent": signal["atr_percent"],
        "price_move": signal["price_move"],
        "signal_time": signal["signal_time"],
        "status": "OPEN",
        "result": None
    }

    history.append(item)


def get_statistics(history):
    total = 0
    long_count = 0
    short_count = 0
    open_count = 0
    wins = 0
    losses = 0

    for item in history:

        if not isinstance(item, dict):
            continue

        total += 1

        if item.get("direction") == "LONG":
            long_count += 1

        if item.get("direction") == "SHORT":
            short_count += 1

        if item.get("status") == "OPEN":
            open_count += 1

        if item.get("result") == "WIN":
            wins += 1

        if item.get("result") == "LOSS":
            losses += 1

    finished = wins + losses

    if finished > 0:
        accuracy = wins / finished * 100
    else:
        accuracy = 0

    return {
        "total": total,
        "long": long_count,
        "short": short_count,
        "open": open_count,
        "wins": wins,
        "losses": losses,
        "accuracy": accuracy
    }


def create_status_message(history):

    stats = get_statistics(history)

    lines = [
        "🤖 وضعیت ربات تحلیل Tabdeal",
        "",
        "🪙 ارزهای تحت بررسی:",
        "BTC / ETH / BNB / SOL / XRP",
        "",
        "⏱ تایم‌فریم:",
        "5 دقیقه و 15 دقیقه",
        "",
        "📊 کل سیگنال‌ها: " + str(stats["total"]),
        "🟢 LONG: " + str(stats["long"]),
        "🔴 SHORT: " + str(stats["short"]),
        "⏳ سیگنال‌های باز: " + str(stats["open"]),
        "✅ برد: " + str(stats["wins"]),
        "❌ باخت: " + str(stats["losses"]),
        "🎯 دقت ثبت‌شده: " + f"{stats['accuracy']:.2f}%",
        "",
        "⚠️ دقت واقعی بعد از جمع شدن سیگنال‌های کافی مشخص می‌شود."
    ]

    return "\n".join(lines)


def process_market(market, history):

    try:

        trades = get_trades(market)

        if not trades:

            print(
                market["base"],
                "معامله‌ای دریافت نشد."
            )

            return None

        signal = analyze_market(
            market,
            trades
        )

        if signal is None:
            return None

        print(
            "سیگنال:",
            market["base"],
            signal["direction"],
            signal["score"]
        )

        if is_duplicate(
            signal,
            history
        ):

            print(
                market["base"],
                "سیگنال تکراری است."
            )

            return None

        add_signal(
            history,
            signal
        )

        return signal

    except Exception as error:

        print(
            "خطا در",
            market.get("base", "?"),
            ":",
            error
        )

        return None


def test_telegram():

    message = "\n".join(
        [
            "✅ اتصال ربات تلگرام برقرار است.",
            "",
            "🤖 Tabdeal Signal Bot",
            "📊 آماده دریافت داده‌های بازار.",
            "",
            "🪙 BTC / ETH / BNB / SOL / XRP",
            "⏱ 5m / 15m",
            "",
            "⚠️ حالت فعلی فقط تحلیل است."
        ]
    )

    return send_telegram(message)


def main():

    print("=" * 50)
    print("Tabdeal Signal Bot")
    print("BTC / ETH / BNB / SOL / XRP")
    print("5m / 15m")
    print("=" * 50)

    if not TELEGRAM_TOKEN:
        print("خطا: TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        return

    if not CHAT_ID:
        print("خطا: TELEGRAM_CHAT_ID تنظیم نشده است.")
        return

    history = load_history()

    print(
        "تعداد سیگنال‌های ذخیره‌شده:",
        len(history)
    )

    try:

        markets = get_markets()

    except Exception as error:

        print(
            "خطا در اتصال به API Tabdeal:"
        )

        print(error)

        send_telegram(
            "❌ اتصال به API Tabdeal با خطا مواجه شد.\n\n"
            + str(error)
        )

        return

    if not markets:

        message = "\n".join(
            [
                "⚠️ ربات به Tabdeal وصل شد",
                "اما بازارهای موردنظر پیدا نشدند.",
                "",
                "BTC / ETH / BNB / SOL / XRP"
            ]
        )

        print(message)

        send_telegram(message)

        return

    print("بازارهای پیدا شده:")

    for coin in COINS:

        if coin in markets:

            print(
                "✅",
                coin,
                markets[coin]["symbol"]
            )

        else:

            print(
                "❌",
                coin
            )

    signals = []

    for coin in COINS:

        if coin not in markets:

            continue

        signal = process_market(
            markets[coin],
            history
        )

        if signal is not None:
            signals.append(signal)

    signals.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    signals = signals[:MAX_SIGNALS]

    if signals:

        for signal in signals:

            message = create_signal_message(
                signal
            )

            send_telegram(message)

            print(
                "سیگنال ارسال شد:",
                signal["base"]
            )

    else:

        print(
            "در این اجرا سیگنال معتبر پیدا نشد."
        )

    save_history(history)

    status = create_status_message(
        history
    )

    send_telegram(status)

    print("=" * 50)
    print("اجرای ربات تمام شد.")
    print("=" * 50)


if __name__ == "__main__":
    main()
