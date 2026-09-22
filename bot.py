import os
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN تنظیم نشده است."
    )

if not CHAT_ID:
    raise RuntimeError(
        "TELEGRAM_CHAT_ID تنظیم نشده است."
    )


TABDEAL_BASE = (
    "https://api1.tabdeal.org/r/api/v1"
)

TOP5 = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "XRP",
]

QUOTE = "USDT"

TRADE_LIMIT = 1000
TIMEOUT = 20

MIN_SCORE = 85
MAX_SIGNALS = 2

MIN_ATR_PERCENT = 0.12
MIN_MOVE_PERCENT = 0.10

HISTORY_FILE = "signals_history.json"
HISTORY_MAX = 500

SESSION = requests.Session()


def api(endpoint, params=None):

    url = (
        f"{TABDEAL_BASE}/{endpoint}"
    )

    response = SESSION.get(
        url,
        params=params or {},
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def telegram(message):

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
        timeout=TIMEOUT,
    )

    print(
        f"Telegram HTTP: "
        f"{response.status_code}"
    )

    if not response.ok:
        print(
            "Telegram response:",
            response.text,
        )

    response.raise_for_status()

    print(
        "✅ پیام Telegram ارسال شد."
    )


def val(data, *keys):

    if not isinstance(data, dict):
        return None

    for key in keys:

        if data.get(key) is not None:
            return data.get(key)

    return None


def num(value):

    try:

        if (
            value is None
            or value == ""
        ):
            return None

        return float(value)

    except (
        TypeError,
        ValueError,
    ):

        return None


def time_ms(data):

    value = num(
        val(
            data,
            "time",
            "timestamp",
            "T",
            "createdAt",
            "created_at",
            "timeMs",
        )
    )

    if value is None:
        return None

    if value < 10_000_000_000:
        value *= 1000

    return int(value)


def items(data):

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        for key in (
            "symbols",
            "data",
            "result",
            "items",
        ):

            value = data.get(key)

            if isinstance(value, list):
                return value

    return []


def markets():

    print(
        "📡 دریافت بازارهای Tabdeal..."
    )

    data = api(
        "exchangeInfo"
    )

    all_markets = items(data)

    print(
        f"📊 تعداد بازارهای دریافتی: "
        f"{len(all_markets)}"
    )

    result = {}

    for market in all_markets:

        if not isinstance(
            market,
            dict,
        ):
            continue

        status = str(
            market.get(
                "status",
                "TRADING",
            )
        ).upper()

        if status not in (
            "TRADING",
            "ENABLED",
            "ACTIVE",
        ):
            continue

        base = str(
            market.get("baseAsset")
            or market.get("base")
            or ""
        ).upper()

        quote = str(
            market.get("quoteAsset")
            or market.get("quote")
            or ""
        ).upper()

        symbol = str(
            market.get("symbol")
            or market.get("pair")
            or market.get("market")
            or ""
        ).upper()

        tabdeal_symbol = str(
            market.get(
                "tabdealSymbol"
            )
            or market.get(
                "tabdeal_symbol"
            )
            or symbol
        ).upper()

        if (
            base in TOP5
            and quote == QUOTE
            and symbol
        ):

            result[
                (base, quote)
            ] = {
                "base": base,
                "quote": quote,
                "symbol": symbol,
                "tabdeal_symbol":
                    tabdeal_symbol,
            }

    print(
        f"✅ بازارهای 5 ارز: "
        f"{len(result)}"
    )

    return result


def trades(market):

    symbols = []

    symbol = market.get(
        "symbol"
    )

    if symbol:
        symbols.append(symbol)

    tabdeal_symbol = market.get(
        "tabdeal_symbol"
    )

    if (
        tabdeal_symbol
        and tabdeal_symbol
        not in symbols
    ):
        symbols.append(
            tabdeal_symbol
        )

    last_error = None

    for symbol in symbols:

        try:

            print(
                f"📥 دریافت معاملات "
                f"{symbol}..."
            )

            data = api(
                "trades",
                {
                    "symbol": symbol,
                    "limit": TRADE_LIMIT,
                },
            )

            result = items(data)

            if result:

                print(
                    f"✅ {symbol}: "
                    f"{len(result)} معامله"
                )

                return result

        except Exception as error:

            last_error = error

            print(
                f"⚠️ خطا در {symbol}: "
                f"{error}"
            )

    if last_error:
        raise last_error

    return []


def candles(
    trade_list,
    minutes,
):

    rows = []

    for trade in trade_list:

        if not isinstance(
            trade,
            dict,
        ):
            continue

        price = num(
            val(
                trade,
                "price",
                "p",
            )
        )

        quantity = num(
            val(
                trade,
                "qty",
                "quantity",
                "q",
                "amount",
                "volume",
            )
        )

        if quantity is None:
            quantity = 0.0

        timestamp = time_ms(
            trade
        )

        if (
            price is not None
            and timestamp is not None
        ):

            rows.append(
                (
                    pd.to_datetime(
                        timestamp,
                        unit="ms",
                        utc=True,
                    ),
                    price,
                    quantity,
                )
            )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=[
            "time",
            "price",
            "volume",
        ],
    )

    df = (
        df
        .sort_values("time")
        .set_index("time")
    )

    result = (
        df["price"]
        .resample(
            f"{minutes}min"
        )
        .ohlc()
    )

    result["volume"] = (
        df["volume"]
        .resample(
            f"{minutes}min"
        )
        .sum()
    )

    return result.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    ).reset_index()


def ema(
    series,
    period,
):

    return series.ewm(
        span=period,
        adjust=False,
    ).mean()


def rsi(
    series,
    period=14,
):

    delta = series.diff()

    gain = (
        delta
        .clip(lower=0)
        .ewm(
            alpha=1 / period,
            adjust=False,
        )
        .mean()
    )

    loss = (
        -delta
        .clip(upper=0)
        .ewm(
            alpha=1 / period,
            adjust=False,
        )
        .mean()
    )

    rs = (
        gain
        /
        loss.replace(
            0,
            np.nan,
        )
    )

    return 100 - (
        100 / (1 + rs)
    )


def atr(
    df,
    period=14,
):

    previous_close = (
        df["close"].shift(1)
    )

    true_range = pd.concat(
        [
            df["high"] - df["low"],

            (
                df["high"]
                - previous_close
            ).abs(),

            (
                df["low"]
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()


def indicators(df):

    result = df.copy()

    result["ema20"] = ema(
        result["close"],
        20,
    )

    result["ema50"] = ema(
        result["close"],
        50,
    )

    result["rsi"] = rsi(
        result["close"],
    )

    result["atr"] = atr(
        result,
    )

    macd = (
        ema(
            result["close"],
            12,
        )
        -
        ema(
            result["close"],
            26,
        )
    )

    result["macd_hist"] = (
        macd
        -
        ema(
            macd,
            9,
        )
    )

    result["vol_ma"] = (
        result["volume"]
        .rolling(20)
        .mean()
    )

    return result
    def analyze(market, trade_list):

    candles_5 = candles(
        trade_list,
        5,
    )

    candles_15 = candles(
        trade_list,
        15,
    )

    print(
        f"{market['base']}: "
        f"5m={len(candles_5)} | "
        f"15m={len(candles_15)}"
    )

    if (
        len(candles_5) < 60
        or len(candles_15) < 30
    ):
        print(
            f"⚠️ {market['base']}: "
            "داده کافی نیست."
        )

        return None

    candles_5 = indicators(
        candles_5,
    )

    candles_15 = indicators(
        candles_15,
    )

    a5 = candles_5.iloc[-2]
    a15 = candles_15.iloc[-2]

    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []

    if a15["ema20"] > a15["ema50"]:

        long_score += 20

        long_reasons.append(
            "روند 15 دقیقه صعودی"
        )

    elif a15["ema20"] < a15["ema50"]:

        short_score += 20

        short_reasons.append(
            "روند 15 دقیقه نزولی"
        )

    if a15["rsi"] > 50:

        long_score += 15

        long_reasons.append(
            "RSI 15 دقیقه مثبت"
        )

    elif a15["rsi"] < 50:

        short_score += 15

        short_reasons.append(
            "RSI 15 دقیقه منفی"
        )

    if a15["macd_hist"] > 0:

        long_score += 20

        long_reasons.append(
            "MACD 15 دقیقه مثبت"
        )

    elif a15["macd_hist"] < 0:

        short_score += 20

        short_reasons.append(
            "MACD 15 دقیقه منفی"
        )

    if a5["close"] > a5["ema20"]:

        long_score += 15

        long_reasons.append(
            "قیمت 5 دقیقه بالای EMA20"
        )

    elif a5["close"] < a5["ema20"]:

        short_score += 15

        short_reasons.append(
            "قیمت 5 دقیقه زیر EMA20"
        )

    if a5["rsi"] >= 52:

        long_score += 15

        long_reasons.append(
            "مومنتوم 5 دقیقه‌ای صعودی"
        )

    elif
    def is_duplicate(signal, history):

    for item in history:

        if not isinstance(item, dict):
            continue

        if (
            item.get("base")
            == signal.get("base")
            and item.get("direction")
            == signal.get("direction")
        ):

            old_time = num(
                item.get("signal_time_ms")
            )

            new_time = num(
                signal.get("signal_time_ms")
            )

            if (
                old_time is not None
                and new_time is not None
                and abs(
                    new_time - old_time
                )
                < 15 * 60 * 1000
            ):

                return True

    return False


def add_signal_to_history(
    history,
    signal,
):

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
        "atr_percent":
            signal["atr_percent"],
        "recent_move":
            signal["recent_move"],
        "signal_time_ms":
            signal["signal_time_ms"],
        "status": "OPEN",
        "result": None,
    }

    history.append(item)

    return history


def statistics(history):

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

        elif item.get("direction") == "SHORT":

            short_count += 1

        status = str(
            item.get(
                "status",
                "",
            )
        ).upper()

        result = str(
            item.get(
                "result",
                "",
            )
        ).upper()

        if status == "OPEN":

            open_count += 1

        if result == "WIN":

            wins += 1

        elif result == "LOSS":

            losses += 1

    finished = (
        wins + losses
    )

    if finished > 0:

        accuracy = (
            wins
            / finished
            * 100
        )

    else:

        accuracy = 0.0

    return {
        "total": total,
        "long": long_count,
        "short": short_count,
        "open": open_count,
        "wins": wins,
        "losses": losses,
        "accuracy": accuracy,
    }


def status_message(history):

    stats = statistics(
        history
    )

    return (
        "🤖 وضعیت ربات تحلیل Tabdeal\n\n"
        "🪙 ارزهای تحت بررسی:\n"
        "BTC / ETH / BNB / SOL / XRP\n\n"
        "⏱ تایم‌فریم:\n"
        "5 دقیقه و 15 دقیقه\n\n"
        f"📊 کل سیگنال‌ها: "
        f"{stats['total']}\n\n"
        f"🟢 LONG: "
        f"{stats['long']}\n\n"
        f"🔴 SHORT: "
        f"{stats['short']}\n\n"
        f"⏳ باز: "
        f"{stats['open']}\n\n"
        f"✅ برد: "
        f"{stats['wins']}\n\n"
        f"❌ باخت: "
        f"{stats['losses']}\n\n"
        f"🎯 دقت فعلی: "
        f"{stats['accuracy']:.2f}%\n\n"
        "⚠️ دقت واقعی بعد از جمع شدن "
        "تعداد کافی سیگنال مشخص می‌شود."
    )


def process_market(
    market,
    history,
):

    try:

        trade_list = trades(
            market
        )

        if not trade_list:

            print(
                f"⚠️ {market['base']}: "
                "معامله‌ای دریافت نشد."
            )

            return None

        signal = analyze(
            market,
            trade_list,
        )

        if signal is None:

            return None

        print(
            f"🚨 {market['base']}: "
            f"{signal['direction']} "
            f"امتیاز="
            f"{signal['score']}"
        )

        if is_duplicate(
            signal,
            history,
        ):

            print(
                f"ℹ️ {market['base']}: "
                "سیگنال تکراری است."
            )

            return None

        add_signal_to_history(
            history,
            signal,
        )

        return signal

    except Exception as error:

        print(
            f"❌ خطا در پردازش "
            f"{market.get('base', '?')}: "
            f"{error}"
        )

        return None


def main():

    print("=" * 50)

    print(
        "🤖 Tabdeal Signal Bot"
    )

    print(
        "📊 BTC / ETH / BNB / SOL / XRP"
    )

    print(
        "⏱ 5m / 15m"
    )

    print("=" * 50)

    history = load_history()

    print(
        f"📚 سیگنال‌های ذخیره‌شده: "
        f"{len(history)}"
    )

    try:

        market_map = markets()

    except Exception as error:

        print(
            "❌ خطا در دریافت "
            "بازارهای Tabdeal:"
        )

        print(error)

        try:

            telegram(
                "❌ خطا در اتصال به API Tabdeal\n\n"
                f"{error}"
            )

        except Exception as telegram_error:

            print(
                "❌ Telegram نیز خطا داد:"
            )

            print(
                telegram_error
            )

        return

    if not market_map:

        message = (
            "⚠️ ربات اجرا شد اما "
            "هیچ‌کدام از بازارهای "
            "موردنظر پیدا نشد.\n\n"
            "BTC / ETH / BNB / SOL / XRP"
        )

        print(message)

        try:

            telegram(message)

        except Exception as error:

            print(error)

        return

    signals = []

    for base in TOP5:

        market = market_map.get(
            (
                base,
                QUOTE,
            )
        )

        if not market:

            print(
                f"⚠️ بازار {base}/USDT "
                "در Tabdeal پیدا نشد."
            )

            continue

        signal = process_market(
            market,
            history,
        )

        if signal is not None:

            signals.append(
                signal
            )

    signals.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    signals = signals[
        :MAX_SIGNALS
    ]

    if signals:

        for signal in signals:

            try:

                telegram(
                    signal_message(
                        signal
                    )
                )

                print(
                    f"✅ سیگنال "
                    f"{signal['base']} "
                    "ارسال شد."
                )

            except Exception as error:

                print(
                    f"❌ خطای Telegram "
                    f"برای "
                    f"{signal['base']}: "
                    f"{error}"
                )

    else:

        print(
            "ℹ️ در این نوبت "
            "سیگنال معتبر پیدا نشد."
        )

    save_history(
        history
    )

    stats = statistics(
        history
    )

    print(
        "📊
