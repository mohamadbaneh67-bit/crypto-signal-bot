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
    raise RuntimeError("TELEGRAM_BOT_TOKEN تنظیم نشده است.")

if not CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID تنظیم نشده است.")

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
    response = S.get(
        f"{TABDEAL_BASE}/{endpoint}",
        params=params or {},
        timeout=TIMEOUT,
    )

    response.raise_for_status()
    return response.json()


def telegram(text):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = S.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
        },
        timeout=TIMEOUT,
    )

    if not response.ok:
        print(
            "❌ خطای Telegram:",
            response.status_code,
            response.text,
        )

    response.raise_for_status()

    print("✅ پیام Telegram ارسال شد.")


def val(data, *keys):
    if not isinstance(data, dict):
        return None

    for key in keys:
        if data.get(key) is not None:
            return data.get(key)

    return None


def num(value):
    try:
        if value is None or value == "":
            return None

        return float(value)

    except (TypeError, ValueError):
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
    print("📡 دریافت بازارهای Tabdeal...")

    data = api("exchangeInfo")
    result = {}

    all_markets = items(data)

    print(
        f"📊 تعداد کل بازارهای دریافتی: "
        f"{len(all_markets)}"
    )

    for market in all_markets:

        if not isinstance(market, dict):
            continue

        status = str(
            market.get("status", "TRADING")
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
            market.get("tabdealSymbol")
            or market.get("tabdeal_symbol")
            or symbol
        ).upper()

        if (
            base in TOP5
            and quote == QUOTE
            and symbol
        ):
            result[(base, quote)] = {
                "base": base,
                "quote": quote,
                "symbol": symbol,
                "tabdeal_symbol": tabdeal_symbol,
            }

    print(
        f"✅ بازارهای 5 ارز انتخابی: "
        f"{len(result)}"
    )

    return result


def trades(market):
    symbols = []

    if market.get("symbol"):
        symbols.append(market["symbol"])

    if (
        market.get("tabdeal_symbol")
        and market["tabdeal_symbol"] not in symbols
    ):
        symbols.append(market["tabdeal_symbol"])

    last_error = None

    for symbol in symbols:

        try:
            print(
                f"📥 دریافت معاملات {symbol} ..."
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
                    f"{len(result)} معامله دریافت شد."
                )

                return result

        except Exception as error:
            last_error = error

            print(
                f"⚠️ خطا در دریافت {symbol}: "
                f"{error}"
            )

    if last_error:
        raise last_error

    return []


def candles(trade_list, minutes):

    rows = []

    for trade in trade_list:

        if not isinstance(trade, dict):
            continue

        price = num(
            val(trade, "price", "p")
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

        timestamp = time_ms(trade)

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
        .resample(f"{minutes}min")
        .ohlc()
    )

    result["volume"] = (
        df["volume"]
        .resample(f"{minutes}min")
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
    def ema(series, period):
    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def rsi(series, period=14):
    delta = series.diff()

    gain = (
        delta
        .clip(lower=0)
        .ewm(
            alpha=1 / period,
            adjust=False
        )
        .mean()
    )

    loss = (
        -delta
        .clip(upper=0)
        .ewm(
            alpha=1 / period,
            adjust=False
        )
        .mean()
    )

    rs = (
        gain /
        loss.replace(0, np.nan)
    )

    return 100 - (
        100 / (1 + rs)
    )


def atr(df, period=14):
    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (
                df["high"] - previous_close
            ).abs(),
            (
                df["low"] - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()


def indicators(df):
    result = df.copy()

    result["ema20"] = ema(
        result["close"],
        20
    )

    result["ema50"] = ema(
        result["close"],
        50
    )

    result["rsi"] = rsi(
        result["close"]
    )

    result["atr"] = atr(result)

    macd = (
        ema(result["close"], 12)
        -
        ema(result["close"], 26)
    )

    result["macd_hist"] = (
        macd -
        ema(macd, 9)
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
        5
    )

    candles_15 = candles(
        trade_list,
        15
    )

    print(
        f"{market['base']}: "
        f"کندل 5m={len(candles_5)}, "
        f"کندل 15m={len(candles_15)}"
    )

    if (
        len(candles_5) < 60
        or len(candles_15) < 30
    ):
        print(
            f"⚠️ {market['base']}: "
            f"داده کافی نیست."
        )

        return None

    candles_5 = indicators(
        candles_5
    )

    candles_15 = indicators(
        candles_15
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

    elif a5["rsi"] <= 48:
        short_score += 15
        short_reasons.append(
            "مومنتوم 5 دقیقه‌ای نزولی"
        )

    if (
        pd.notna(a5["vol_ma"])
        and a5["vol_ma"] > 0
        and a5["volume"]
        > a5["vol_ma"] * 1.2
    ):

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

    entry = num(a5["close"])
    atr_value = num(a5["atr"])

    if (
        entry is None
        or atr_value is None
        or atr_value <= 0
    ):
        return None

    atr_percent = (
        atr_value / entry * 100
    )

    if atr_percent < MIN_ATR_PERCENT:
        return None

    old_price = num(
        candles_5.iloc[-7]["close"]
    )

    if (
        old_price is None
        or old_price == 0
    ):
        return None

    recent_move = (
        abs(entry - old_price)
        / old_price
        * 100
    )

    if recent_move < MIN_MOVE_PERCENT:
        return None

    recent_high = float(
        candles_5
        .iloc[-7:-2]["high"]
        .max()
    )

    recent_low = float(
        candles_5
        .iloc[-7:-2]["low"]
        .min()
    )

    if entry > recent_high:
        long_score += 10
        long_reasons.append(
            "شکست سقف کوتاه‌مدت"
        )

    if entry < recent_low:
        short_score += 10
        short_reasons.append(
            "شکست کف کوتاه‌مدت"
        )

    if (
        long_score >= MIN_SCORE
        and long_score > short_score
    ):
        direction = "LONG"
        score = min(
            100,
            int(long_score)
        )
        reasons = long_reasons

    elif (
        short_score >= MIN_SCORE
        and short_score > long_score
    ):
        direction = "SHORT"
        score = min(
            100,
            int(short_score)
        )
        reasons = short_reasons

    else:
        print(
            f"ℹ️ {market['base']}: "
            f"سیگنال معتبر نیست "
            f"(LONG={long_score}, "
            f"SHORT={short_score})"
        )

        return None

    if direction == "LONG":

        stop = (
            entry -
            1.2 * atr_value
        )

        risk = entry - stop

        tp1 = (
            entry +
            1.5 * risk
        )

        tp2 = (
            entry +
            2.5 * risk
        )

    else:

        stop = (
            entry +
            1.2 * atr_value
        )

        risk = stop - entry

        tp1 = (
            entry -
            1.5 * risk
        )

        tp2 = (
            entry -
            2.5 * risk
        )

    timestamps = [
        time_ms(t)
        for t in trade_list
        if isinstance(t, dict)
    ]

    timestamps = [
        x for x in timestamps
        if x is not None
    ]

    if timestamps:
        signal_time = max(
            timestamps
        )
    else:
        signal_time = int(
            datetime.now(
                timezone.utc
            ).timestamp()
            * 1000
        )

    return {
        "base": market["base"],
        "quote": market["quote"],
        "symbol": market["symbol"],
        "tabdeal_symbol":
            market["tabdeal_symbol"],
        "direction": direction,
        "score": score,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "atr_percent": atr_percent,
        "recent_move": recent_move,
        "signal_time_ms": signal_time,
        "reasons": reasons,
    }


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

            return []

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):
        return []


def save_history(history):

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            history[-HISTORY_MAX:],
            file,
            ensure_ascii=False,
            indent=2
    )
        def evaluate_signal(signal):
    direction = signal["direction"]
    entry = signal["entry"]
    stop = signal["stop"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]

    if direction == "LONG":
        risk = entry - stop

        return {
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "risk": risk,
        }

    risk = stop - entry

    return {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "risk": risk,
    }


def fmt_price(value):
    value = num(value)

    if value is None:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:,.4f}"

    if value >= 0.01:
        return f"{value:,.6f}"

    return f"{value:.8f}"


def fmt_percent(value):
    value = num(value)

    if value is None:
        return "-"

    return f"{value:.2f}%"


def signal_message(signal):
    direction = signal["direction"]

    if direction == "LONG":
        title = "🟢 سیگنال خرید / LONG"
    else:
        title = "🔴 سیگنال فروش / SHORT"

    reasons = signal.get(
        "reasons",
        []
    )

    reason_text = "\n".join(
        f"• {reason}"
        for reason in reasons
    )

    if not reason_text:
        reason_text = "• ترکیب شاخص‌های تکنیکال"

    return (
        f"🚨 سیگنال جدید Tabdeal\n\n"
        f"ارز: {signal['base']}/{signal['quote']}\n"
        f"جهت: {title}\n"
        f"امتیاز: {signal['score']} از 100\n\n"
        f"💰 ورود:\n"
        f"{fmt_price(signal['entry'])}\n\n"
        f"🛑 حد ضرر:\n"
        f"{fmt_price(signal['stop'])}\n\n"
        f"🎯 سود اول:\n"
        f"{fmt_price(signal['tp1'])}\n\n"
        f"🎯 سود دوم:\n"
        f"{fmt_price(signal['tp2'])}\n\n"
        f"📊 ATR:\n"
        f"{fmt_percent(signal['atr_percent'])}\n\n"
        f"📈 حرکت اخیر:\n"
        f"{fmt_percent(signal['recent_move'])}\n\n"
        f"🔎 دلایل سیگنال:\n"
        f"{reason_text}\n\n"
        f"⚠️ این ربات فقط تحلیل ارائه می‌کند "
        f"و معامله‌ای انجام نمی‌دهد."
    )


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
                and abs(new_time - old_time)
                < 15 * 60 * 1000
            ):
                return True

    return False


def add_signal_to_history(
    history,
    signal
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
        "atr_percent": signal["atr_percent"],
        "recent_move": signal["recent_move"],
        "signal_time_ms":
            signal["signal_time_ms"],
        "status": "OPEN",
       def main():

    print("=" * 50)
    print("🤖 Tabdeal Signal Bot")
    print("📊 تحلیل BTC / ETH / BNB / SOL / XRP")
    print("⏱ تایم‌فریم 5m و 15m")
    print("=" * 50)

    history = load_history()

    print(
        f"📚 تعداد سیگنال‌های ذخیره‌شده: "
        f"{len(history)}"
    )

    try:
        market_map = markets()

    except Exception as error:

        print(
            "❌ خطا در دریافت بازارهای Tabdeal:"
        )

        print(error)

        telegram(
            "❌ ربات Tabdeal اجرا شد، "
            "اما دریافت بازارهای Tabdeal با خطا مواجه شد.\n\n"
            f"خطا:\n{error}"
        )

        return

    if not market_map:

        message = (
            "⚠️ ربات Tabdeal اجرا شد اما "
            "هیچ‌کدام از 5 بازار موردنظر "
            "در پاسخ API پیدا نشد.\n\n"
            "BTC / ETH / BNB / SOL / XRP"
        )

        print(message)

        telegram(message)

        return

    signals = []

    for base in TOP5:

        market = market_map.get(
            (base, QUOTE)
        )

        if not market:

            print(
                f"⚠️ بازار {base}/USDT "
                f"در Tabdeal پیدا نشد."
            )

            continue

        signal = process_market(
            market,
            history
        )

        if signal is not None:
            signals.append(signal)

    signals = sorted(
        signals,
        key=lambda x: x["score"],
        reverse=True
    )

    signals = signals[
        :MAX_SIGNALS
    ]

    if signals:

        for signal in signals:

            try:
                telegram(
                    signal_message(signal)
                )

                print(
                    f"✅ سیگنال {signal['base']} "
                    f"به Telegram ارسال شد."
                )

            except Exception as error:

                print(
                    f"❌ خطا در ارسال "
                    f"{signal['base']}: "
                    f"{error}"
                )

    else:

        print(
            "ℹ️ در این نوبت سیگنال معتبر "
            "پیدا نشد."
        )

    save_history(history)

    stats = statistics(history)

    print(
        "📊 آمار:"
    )

    print(
        f"کل={stats['total']} | "
        f"LONG={stats['long']} | "
        f"SHORT={stats['short']} | "
        f"OPEN={stats['open']} | "
        f"WIN={stats['wins']} | "
        f"LOSS={stats['losses']} | "
        f"Accuracy={stats['accuracy']:.2f}%"
    )

    try:

        telegram(
            status_message(history)
        )

    except Exception as error:

        print(
            "⚠️ ارسال وضعیت به Telegram "
            f"ناموفق بود: {error}"
        )

    print("=" * 50)
    print("✅ اجرای ربات تمام شد.")
    print("=" * 50)


if __name__ == "__main__":
    main()
