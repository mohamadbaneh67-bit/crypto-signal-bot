import os
import json
import time
import math
import hashlib
import requests
from datetime import datetime, timezone

# ============================================================
# TABDEAL FUTURES ANALYSIS BOT
# نسخه آزمایشی حرفه‌ای
# فقط تحلیل - بدون اجرای معامله
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ============================================================
# تنظیمات اصلی
# ============================================================

# فقط 5 ارز موردنظر
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "SOLUSDT",
]

# دو تایم‌فریم اصلی
TIMEFRAMES = {
    "5m": 5,
    "15m": 15,
}

# تعداد معاملات خام برای ساخت کندل
RAW_TRADES_LIMIT = 1000

# حداقل امتیاز برای صدور سیگنال
MIN_SIGNAL_SCORE = 70

# حداکثر سیگنال‌های ذخیره‌شده
MAX_STORED_SIGNALS = 3000

# جلوگیری از تکرار سیگنال
DEDUP_MINUTES = 60

# ATR
TP1_ATR = 1.0
TP2_ATR = 2.0
SL_ATR = 1.2

# ============================================================
# فایل‌های ذخیره
# ============================================================

LEARNING_FILE = "tabdeal_learning.json"
SIGNALS_FILE = "tabdeal_signals.json"
MARKET_FILE = "tabdeal_market_data.json"

# ============================================================
# آدرس‌های API عمومی Tabdeal
# ============================================================

BASE_URLS = [
    "https://api1.tabdeal.org",
]

EXCHANGE_INFO_PATHS = [
    "/r/fapi/v1/exchangeInfo",
    "/fapi/v1/exchangeInfo",
]

DEPTH_PATHS = [
    "/r/fapi/v1/depth",
    "/fapi/v1/depth",
]

TRADES_PATHS = [
    "/r/fapi/v1/trades",
    "/fapi/v1/trades",
]

TICKER_PATHS = [
    "/r/fapi/v1/ticker/price",
    "/fapi/v1/ticker/price",
]

# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Tabdeal-Futures-Analysis-Bot/2.0",
    "Accept": "application/json",
})

# ============================================================
# عمومی
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:
        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception as e:

        print(
            f"JSON LOAD ERROR [{filename}]:",
            e
        )

        return default


def save_json(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        return True

    except Exception as e:

        print(
            f"JSON SAVE ERROR [{filename}]:",
            e
        )

        return False


# ============================================================
# Telegram
# ============================================================

def send_telegram(text):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram secrets are missing."
        )

        return False

    try:

        response = SESSION.post(
            (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            ),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
            },
            timeout=20,
        )

        if response.ok:
            return True

        print(
            "Telegram HTTP ERROR:",
            response.status_code
        )

        print(
            response.text[:500]
        )

    except Exception as e:

        print(
            "Telegram ERROR:",
            e
        )

    return False


# ============================================================
# API Request
# ============================================================

def api_get(
    paths,
    params=None
):

    last_error = None

    for base_url in BASE_URLS:

        for path in paths:

            url = base_url + path

            try:

                response = SESSION.get(
                    url,
                    params=params,
                    timeout=25
                )

                print(
                    "REQUEST:",
                    url,
                    "STATUS:",
                    response.status_code
                )

                if response.ok:

                    try:
                        return response.json()

                    except Exception as e:

                        last_error = (
                            f"JSON error: {e}"
                        )

                else:

                    last_error = (
                        f"HTTP "
                        f"{response.status_code}: "
                        f"{response.text[:300]}"
                    )

            except Exception as e:

                last_error = str(e)

    print(
        "API ERROR:",
        last_error
    )

    return None


# ============================================================
# Symbol Helpers
# ============================================================

def normalize_symbol(symbol):

    return (
        str(symbol)
        .replace("_", "")
        .replace("-", "")
        .replace("/", "")
        .upper()
    )


def tabdeal_symbol(symbol):

    return normalize_symbol(symbol)


def get_requested_symbols():

    return [
        normalize_symbol(symbol)
        for symbol in SYMBOLS
    ]


# ============================================================
# Exchange Information
# ============================================================

def get_exchange_info():

    print(
        "\n=== دریافت اطلاعات Futures تبدیل ==="
    )

    data = api_get(
        EXCHANGE_INFO_PATHS
    )

    if data is None:

        print(
            "Exchange info unavailable."
        )

        return {}

    return data


# ============================================================
# بررسی وجود 5 ارز
# ============================================================

def verify_symbols():

    data = get_exchange_info()

    requested = get_requested_symbols()

    available = set()

    if isinstance(data, dict):

        raw_symbols = data.get(
            "symbols",
            []
        )

        if isinstance(
            raw_symbols,
            list
        ):

            for item in raw_symbols:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                symbol = item.get(
                    "symbol"
                )

                if isinstance(
                    symbol,
                    str
                ):

                    available.add(
                        normalize_symbol(
                            symbol
                        )
                    )

    valid = [
        symbol
        for symbol in requested
        if symbol in available
    ]

    missing = [
        symbol
        for symbol in requested
        if symbol not in available
    ]

    print(
        "\n=== بررسی 5 ارز ==="
    )

    print(
        "درخواست‌شده:",
        requested
    )

    print(
        "موجود:",
        valid
    )

    if missing:

        print(
            "پیدا نشد:",
            missing
        )

    return valid


# ============================================================
# Ticker
# ============================================================

def get_ticker(symbol):

    params = {
        "symbol": tabdeal_symbol(
            symbol
        )
    }

    data = api_get(
        TICKER_PATHS,
        params=params
    )

    if not isinstance(
        data,
        dict
    ):
        return None

    price = safe_float(
        data.get("price")
    )

    if price <= 0:
        return None

    return {
        "symbol": normalize_symbol(
            symbol
        ),
        "price": price,
        "timestamp": time.time(),
    }


# ============================================================
# Futures Trades
# ============================================================

def get_recent_trades(
    symbol,
    limit=RAW_TRADES_LIMIT
):

    params = {
        "symbol": tabdeal_symbol(
            symbol
        ),
        "limit": limit,
    }

    data = api_get(
        TRADES_PATHS,
        params=params
    )

    if not isinstance(
        data,
        list
    ):

        return []

    trades = []

    for row in data:

        if not isinstance(
            row,
            dict
        ):
            continue

        price = safe_float(
            row.get("price")
        )

        qty = safe_float(
            row.get("qty")
        )

        timestamp = (
            row.get("time")
            or row.get("timestamp")
            or row.get("T")
        )

        timestamp = safe_float(
            timestamp
        )

        if timestamp <= 0:
            continue

        if price <= 0 or qty <= 0:
            continue

        trades.append({
            "time": int(timestamp),
            "price": price,
            "qty": qty,
            "is_buyer_maker": bool(
                row.get(
                    "isBuyerMaker",
                    False
                )
            ),
        })

    trades.sort(
        key=lambda x: x["time"]
    )

    return trades


# ============================================================
# ساخت کندل از معاملات
# ============================================================

def build_candles_from_trades(
    trades,
    timeframe_minutes
):

    if not trades:
        return []

    interval_ms = (
        timeframe_minutes
        * 60
        * 1000
    )

    grouped = {}

    for trade in trades:

        timestamp = trade["time"]

        bucket = (
            timestamp
            // interval_ms
        ) * interval_ms

        if bucket not in grouped:

            grouped[bucket] = {
                "open_time": bucket,
                "open": trade["price"],
                "high": trade["price"],
                "low": trade["price"],
                "close": trade["price"],
                "volume": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "trades": 0,
            }

        candle = grouped[bucket]

        candle["high"] = max(
            candle["high"],
            trade["price"]
        )

        candle["low"] = min(
            candle["low"],
            trade["price"]
        )

        candle["close"] = trade["price"]

        candle["volume"] += trade["qty"]

        if trade["is_buyer_maker"]:

            candle["sell_volume"] += (
                trade["qty"]
            )

        else:

            candle["buy_volume"] += (
                trade["qty"]
            )

        candle["trades"] += 1

    candles = list(
        grouped.values()
    )

    candles.sort(
        key=lambda x: x["open_time"]
    )

    for candle in candles:

        total = (
            candle["buy_volume"]
            +
            candle["sell_volume"]
        )

        if total > 0:

            candle["buy_ratio"] = (
                candle["buy_volume"]
                / total
                * 100
            )

            candle["sell_ratio"] = (
                candle["sell_volume"]
                / total
                * 100
            )

        else:

            candle["buy_ratio"] = 50.0
            candle["sell_ratio"] = 50.0

    return candles


# ============================================================
# پایان بخش 1
# ============================================================

print(
    "\n"
    "====================================================\n"
    " TABDEAL FUTURES BOT - PART 1 LOADED\n"
    " فقط 5 ارز | 5m + 15m\n"
    " بدون اجرای معامله\n"
    "===================================================="
            )# ============================================================
# بخش 2 - Market Depth / Indicators / Analysis
# ============================================================

def get_order_book(symbol, limit=100):

    params = {
        "symbol": tabdeal_symbol(symbol),
        "limit": limit,
    }

    data = api_get(
        DEPTH_PATHS,
        params=params
    )

    if not isinstance(data, dict):
        return None

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    if not bids or not asks:
        return None

    bid_volume = 0.0
    ask_volume = 0.0

    best_bid = 0.0
    best_ask = 0.0

    for row in bids:
        if len(row) < 2:
            continue

        price = safe_float(row[0])
        qty = safe_float(row[1])

        if price > best_bid:
            best_bid = price

        bid_volume += qty

    for row in asks:
        if len(row) < 2:
            continue

        price = safe_float(row[0])
        qty = safe_float(row[1])

        if best_ask == 0 or price < best_ask:
            best_ask = price

        ask_volume += qty

    total = bid_volume + ask_volume

    if total > 0:
        buy_pressure = (
            bid_volume / total
        ) * 100

        sell_pressure = (
            ask_volume / total
        ) * 100
    else:
        buy_pressure = 50.0
        sell_pressure = 50.0

    spread = 0.0

    if best_bid > 0 and best_ask > 0:
        spread = (
            (best_ask - best_bid)
            / best_bid
        ) * 100

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
        "buy_pressure": buy_pressure,
        "sell_pressure": sell_pressure,
        "spread_percent": spread,
    }


# ============================================================
# Indicators
# ============================================================

def sma(values, period):

    if len(values) < period:
        return None

    return (
        sum(values[-period:])
        / period
    )


def ema(values, period):

    if len(values) < period:
        return None

    multiplier = (
        2 / (period + 1)
    )

    result = sum(
        values[:period]
    ) / period

    for value in values[period:]:

        result = (
            (value - result)
            * multiplier
        ) + result

    return result


def rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = (
            values[i]
            - values[i - 1]
        )

        if change >= 0:

            gains.append(change)
            losses.append(0.0)

        else:

            gains.append(0.0)
            losses.append(
                abs(change)
            )

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return 100 - (
        100 / (1 + rs)
    )


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        high = current["high"]
        low = current["low"]
        previous_close = previous["close"]

        true_range = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            ),
        )

        true_ranges.append(
            true_range
        )

    if len(true_ranges) < period:
        return None

    return (
        sum(
            true_ranges[-period:]
        )
        / period
    )


def volume_ratio(
    candles,
    period=20
):

    if len(candles) < period + 1:
        return 1.0

    current = candles[-1]["volume"]

    previous = [
        candle["volume"]
        for candle in
        candles[-period-1:-1]
    ]

    if not previous:
        return 1.0

    average = (
        sum(previous)
        / len(previous)
    )

    if average <= 0:
        return 1.0

    return (
        current / average
    )


# ============================================================
# Candle Momentum
# ============================================================

def momentum_percent(
    candles,
    lookback=5
):

    if len(candles) <= lookback:
        return 0.0

    old_price = candles[
        -lookback - 1
    ]["close"]

    current_price = candles[
        -1
    ]["close"]

    if old_price <= 0:
        return 0.0

    return (
        (
            current_price
            - old_price
        )
        / old_price
    ) * 100


# ============================================================
# تحلیل یک تایم‌فریم
# ============================================================

def analyze_timeframe(
    candles
):

    if len(candles) < 60:
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    price = closes[-1]

    ema9 = ema(
        closes,
        9
    )

    ema21 = ema(
        closes,
        21
    )

    ema50 = ema(
        closes,
        50
    )

    rsi_value = rsi(
        closes,
        14
    )

    atr_value = atr(
        candles,
        14
    )

    vol_ratio = volume_ratio(
        candles,
        20
    )

    momentum = momentum_percent(
        candles,
        5
    )

    if any(
        value is None
        for value in [
            ema9,
            ema21,
            ema50,
            rsi_value,
            atr_value,
        ]
    ):
        return None

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    ema12 = ema(
        closes,
        12
    )

    ema26 = ema(
        closes,
        26
    )

    macd_value = 0.0

    if (
        ema12 is not None
        and ema26 is not None
    ):
        macd_value = (
            ema12
            - ema26
        )

    # --------------------------------------------------------
    # روند
    # --------------------------------------------------------

    bullish = (
        ema9 > ema21
        and ema21 > ema50
        and price > ema21
    )

    bearish = (
        ema9 < ema21
        and ema21 < ema50
        and price < ema21
    )

    # --------------------------------------------------------
    # BUY SCORE
    # --------------------------------------------------------

    buy_score = 0

    if price > ema9:
        buy_score += 10

    if ema9 > ema21:
        buy_score += 15

    if ema21 > ema50:
        buy_score += 15

    if rsi_value >= 50:
        buy_score += 10

    if 50 <= rsi_value <= 68:
        buy_score += 10

    if macd_value > 0:
        buy_score += 10

    if vol_ratio >= 1.2:
        buy_score += 10

    if momentum > 0:
        buy_score += 5

    if bullish:
        buy_score += 15

    # --------------------------------------------------------
    # SELL SCORE
    # --------------------------------------------------------

    sell_score = 0

    if price < ema9:
        sell_score += 10

    if ema9 < ema21:
        sell_score += 15

    if ema21 < ema50:
        sell_score += 15

    if rsi_value <= 50:
        sell_score += 10

    if 32 <= rsi_value <= 50:
        sell_score += 10

    if macd_value < 0:
        sell_score += 10

    if vol_ratio >= 1.2:
        sell_score += 10

    if momentum < 0:
        sell_score += 5

    if bearish:
        sell_score += 15

    # --------------------------------------------------------
    # نوسان
    # --------------------------------------------------------

    volatility_percent = 0.0

    if price > 0:
        volatility_percent = (
            atr_value / price
        ) * 100

    return {
        "price": price,
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "rsi": rsi_value,
        "atr": atr_value,
        "volume_ratio": vol_ratio,
        "momentum": momentum,
        "macd": macd_value,
        "bullish": bullish,
        "bearish": bearish,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "volatility_percent":
            volatility_percent,
    }


# ============================================================
# پایان بخش 2
# ============================================================

print(
    "TABDEAL FUTURES BOT - PART 2 LOADED"
    )# ============================================================
# بخش 3 - Final Analysis / Learning / Telegram / Main
# ============================================================

def choose_direction(
    analysis_5m,
    analysis_15m,
    order_book
):

    buy_5 = analysis_5m["buy_score"]
    sell_5 = analysis_5m["sell_score"]

    buy_15 = analysis_15m["buy_score"]
    sell_15 = analysis_15m["sell_score"]

    # --------------------------------------------------------
    # امتیاز پایه با وزن بیشتر برای 15 دقیقه
    # --------------------------------------------------------

    buy_score = (
        buy_5 * 0.45
        +
        buy_15 * 0.55
    )

    sell_score = (
        sell_5 * 0.45
        +
        sell_15 * 0.55
    )

    # --------------------------------------------------------
    # فشار دفتر سفارش
    # --------------------------------------------------------

    if order_book:

        buy_pressure = order_book[
            "buy_pressure"
        ]

        sell_pressure = order_book[
            "sell_pressure"
        ]

        if buy_pressure >= 55:
            buy_score += 5

        if sell_pressure >= 55:
            sell_score += 5

    else:

        buy_pressure = 50.0
        sell_pressure = 50.0

    # محدود کردن امتیاز
    buy_score = min(
        round(buy_score, 2),
        100
    )

    sell_score = min(
        round(sell_score, 2),
        100
    )

    # --------------------------------------------------------
    # تصمیم نهایی
    # --------------------------------------------------------

    difference = abs(
        buy_score - sell_score
    )

    if (
        buy_score >= MIN_SIGNAL_SCORE
        and buy_score > sell_score
        and difference >= 5
    ):

        direction = "BUY"
        score = buy_score

    elif (
        sell_score >= MIN_SIGNAL_SCORE
        and sell_score > buy_score
        and difference >= 5
    ):

        direction = "SELL"
        score = sell_score

    else:

        direction = "WAIT"
        score = max(
            buy_score,
            sell_score
        )

    return {
        "direction": direction,
        "score": round(score, 2),
        "buy_score": buy_score,
        "sell_score": sell_score,
        "buy_pressure": round(
            buy_pressure,
            2
        ),
        "sell_pressure": round(
            sell_pressure,
            2
        ),
    }


# ============================================================
# پیشنهاد اهرم بر اساس نوسان
# ============================================================

def calculate_leverage(
    analysis_5m,
    analysis_15m,
    score
):

    volatility_5 = (
        analysis_5m[
            "volatility_percent"
        ]
    )

    volatility_15 = (
        analysis_15m[
            "volatility_percent"
        ]
    )

    volatility = (
        volatility_5 * 0.45
        +
        volatility_15 * 0.55
    )

    # --------------------------------------------------------
    # اهرم پیشنهادی
    #
    # این فقط پیشنهاد تحلیلی است.
    # ربات فعلاً هیچ سفارشی ارسال نمی‌کند.
    # --------------------------------------------------------

    if volatility >= 2.5:
        leverage = 3

    elif volatility >= 1.5:
        leverage = 5

    elif volatility >= 0.8:
        leverage = 8

    elif volatility >= 0.4:
        leverage = 10

    else:
        leverage = 12

    # اگر امتیاز سیگنال ضعیف‌تر باشد،
    # اهرم پیشنهادی پایین‌تر می‌آید.

    if score < 75:
        leverage = min(
            leverage,
            5
        )

    elif score < 85:
        leverage = min(
            leverage,
            8
        )

    return {
        "recommended_leverage":
            leverage,

        "volatility_5m":
            round(
                volatility_5,
                3
            ),

        "volatility_15m":
            round(
                volatility_15,
                3
            ),

        "combined_volatility":
            round(
                volatility,
                3
            ),
    }


# ============================================================
# Pattern Fingerprint
# ============================================================

def create_pattern_fingerprint(
    analysis
):

    a5 = analysis["5m"]
    a15 = analysis["15m"]

    def rsi_bucket(value):

        if value < 35:
            return "LOW"

        if value < 45:
            return "WEAK"

        if value < 55:
            return "MID"

        if value < 65:
            return "STRONG"

        return "HIGH"

    def volume_bucket(value):

        if value < 0.8:
            return "LOW"

        if value < 1.2:
            return "NORMAL"

        if value < 2:
            return "HIGH"

        return "SPIKE"

    raw = "|".join([
        analysis["direction"],

        "5_BULL"
        if a5["bullish"]
        else
        "5_BEAR"
        if a5["bearish"]
        else
        "5_MIXED",

        "15_BULL"
        if a15["bullish"]
        else
        "15_BEAR"
        if a15["bearish"]
        else
        "15_MIXED",

        rsi_bucket(
            a5["rsi"]
        ),

        rsi_bucket(
            a15["rsi"]
        ),

        volume_bucket(
            a5["volume_ratio"]
        ),

        "MACD_POS"
        if a5["macd"] > 0
        else
        "MACD_NEG",
    ])

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:20]


# ============================================================
# Learning
# ============================================================

def get_learning():

    default = {
        "patterns": {},
        "stats": {
            "total": 0,
            "tp1": 0,
            "tp2": 0,
            "sl": 0,
            "ambiguous": 0,
        }
    }

    data = load_json(
        LEARNING_FILE,
        default
    )

    if not isinstance(
        data,
        dict
    ):
        return default

    if "patterns" not in data:
        data["patterns"] = {}

    if "stats" not in data:
        data["stats"] = default[
            "stats"
        ]

    return data


def historical_pattern_score(
    pattern_id
):

    learning = get_learning()

    pattern = learning[
        "patterns"
    ].get(
        pattern_id
    )

    if not pattern:
        return None

    total = (
        pattern.get("tp1", 0)
        +
        pattern.get("tp2", 0)
        +
        pattern.get("sl", 0)
        +
        pattern.get("ambiguous", 0)
    )

    if total < 3:
        return None

    successful = (
        pattern.get("tp1", 0)
        +
        pattern.get("tp2", 0)
    )

    return round(
        successful
        / total
        * 100,
        2
    )


def update_learning(
    signal,
    result
):

    learning = get_learning()

    pattern_id = signal.get(
        "pattern_id"
    )

    if not pattern_id:
        return

    if pattern_id not in learning[
        "patterns"
    ]:

        learning[
            "patterns"
        ][pattern_id] = {
            "total": 0,
            "tp1": 0,
            "tp2": 0,
            "sl": 0,
            "ambiguous": 0,
        }

    pattern = learning[
        "patterns"
    ][pattern_id]

    pattern["total"] += 1

    if result in pattern:
        pattern[result] += 1

    stats = learning[
        "stats"
    ]

    stats["total"] += 1

    if result in stats:
        stats[result] += 1

    save_json(
        LEARNING_FILE,
        learning
    )


# ============================================================
# Signal Storage
# ============================================================

def load_signals():

    data = load_json(
        SIGNALS_FILE,
        []
    )

    if not isinstance(
        data,
        list
    ):
        return []

    return data


def save_signals(
    signals
):

    if len(signals) > MAX_STORED_SIGNALS:

        signals = signals[
            -MAX_STORED_SIGNALS:
        ]

    save_json(
        SIGNALS_FILE,
        signals
    )


# ============================================================
# Duplicate Protection
# ============================================================

def is_duplicate_signal(
    signals,
    symbol,
    direction,
    pattern_id
):

    now = time.time()

    for signal in reversed(
        signals
    ):

        if signal.get(
            "symbol"
        ) != symbol:
            continue

        if signal.get(
            "direction"
        ) != direction:
            continue

        if signal.get(
            "pattern_id"
        ) != pattern_id:
            continue

        if signal.get(
            "status"
        ) not in [
            "OPEN",
            "TP1"
        ]:
            continue

        created = safe_float(
            signal.get(
                "created_timestamp"
            )
        )

        if created <= 0:
            continue

        age = (
            now - created
        ) / 60

        if age < DEDUP_MINUTES:
            return True

    return False


# ============================================================
# Create Signal
# ============================================================

def create_signal(
    analysis
):

    direction = analysis[
        "direction"
    ]

    if direction == "WAIT":
        return None

    price = analysis[
        "price"
    ]

    atr_value = analysis[
        "atr"
    ]

    if price <= 0:
        return None

    if atr_value <= 0:
        return None

    pattern_id = (
        create_pattern_fingerprint(
            analysis
        )
    )

    history_score = (
        historical_pattern_score(
            pattern_id
        )
    )

    leverage = (
        calculate_leverage(
            analysis["5m"],
            analysis["15m"],
            analysis["score"]
        )
    )

    if direction == "BUY":

        stop_loss = (
            price
            - atr_value * SL_ATR
        )

        tp1 = (
            price
            + atr_value * TP1_ATR
        )

        tp2 = (
            price
            + atr_value * TP2_ATR
        )

    else:

        stop_loss = (
            price
            + atr_value * SL_ATR
        )

        tp1 = (
            price
            - atr_value * TP1_ATR
        )

        tp2 = (
            price
            - atr_value * TP2_ATR
        )

    return {

        "id": hashlib.sha256(
            (
                analysis["symbol"]
                + direction
                + str(
                    time.time()
                )
            ).encode()
        ).hexdigest()[:16],

        "created_at":
            now_iso(),

        "created_timestamp":
            time.time(),

        "symbol":
            analysis["symbol"],

        "direction":
            direction,

        "score":
            analysis["score"],

        "buy_score":
            analysis["buy_score"],

        "sell_score":
            analysis["sell_score"],

        "buy_pressure":
            analysis["buy_pressure"],

        "sell_pressure":
            analysis["sell_pressure"],

        "entry":
            price,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "stop_loss":
            stop_loss,

        "recommended_leverage":
            leverage[
                "recommended_leverage"
            ],

        "volatility_5m":
            leverage[
                "volatility_5m"
            ],

        "volatility_15m":
            leverage[
                "volatility_15m"
            ],

        "combined_volatility":
            leverage[
                "combined_volatility"
            ],

        "pattern_id":
            pattern_id,

        "historical_pattern_score":
            history_score,

        "status":
            "OPEN",

        "result":
            None,
    }


# ============================================================
# پیام سیگنال
# ============================================================

def signal_to_message(
    signal
):

    history = signal.get(
        "historical_pattern_score"
    )

    if history is None:
        history_text = (
            "هنوز داده کافی ندارد"
        )
    else:
        history_text = (
            f"{history}%"
        )

    direction_text = (
        "🟢 خرید / لانگ"
        if signal["direction"]
        == "BUY"
        else
        "🔴 فروش / شورت"
    )

    return (
        "🚨 سیگنال Futures تبدیل\n\n"

        f"🪙 ارز: {signal['symbol']}\n"
        f"📊 جهت: {direction_text}\n\n"

        f"⭐ قدرت سیگنال: "
        f"{signal['score']}%\n"

        f"🟢 قدرت خرید: "
        f"{signal['buy_score']}%\n"

        f"🔴 قدرت فروش: "
        f"{signal['sell_score']}%\n\n"

        f"💰 ورود: "
        f"{signal['entry']}\n"

        f"🎯 TP1: "
        f"{signal['tp1']}\n"

        f"🎯 TP2: "
        f"{signal['tp2']}\n"

        f"🛑 حد ضرر: "
        f"{signal['stop_loss']}\n\n"

        f"⚡ اهرم پیشنهادی تحلیلی: "
        f"{signal['recommended_leverage']}x\n"

        f"📈 نوسان 5m: "
        f"{signal['volatility_5m']}%\n"

        f"📈 نوسان 15m: "
        f"{signal['volatility_15m']}%\n\n"

        f"🟢 فشار خرید: "
        f"{signal['buy_pressure']}%\n"

        f"🔴 فشار فروش: "
        f"{signal['sell_pressure']}%\n\n"

        f"🧠 سابقه همین الگو: "
        f"{history_text}\n\n"

        "ℹ️ این نسخه فقط تحلیل می‌کند "
        "و معامله‌ای باز نمی‌کند."
    )


# ============================================================
# تحلیل یک ارز
# ============================================================

def analyze_symbol(
    symbol
):

    print(
        f"\n=== ANALYZING {symbol} ==="
    )

    trades = get_recent_trades(
        symbol
    )

    if len(trades) < 100:
        print(
            "Not enough trades:",
            len(trades)
        )
        return None

    candles_5m = (
        build_candles_from_trades(
            trades,
            5
        )
    )

    candles_15m = (
        build_candles_from_trades(
            trades,
            15
        )
    )

    if (
        len(candles_5m) < 60
        or
        len(candles_15m) < 60
    ):

        print(
            "Not enough candles:",
            len(candles_5m),
            len(candles_15m)
        )

        return None

    analysis_5m = (
        analyze_timeframe(
            candles_5m
        )
    )

    analysis_15m = (
        analyze_timeframe(
            candles_15m
        )
    )

    if (
        not analysis_5m
        or
        not analysis_15m
    ):
        return None

    order_book = (
        get_order_book(
            symbol
        )
    )

    decision = choose_direction(
        analysis_5m,
        analysis_15m,
        order_book
    )

    # قیمت لحظه‌ای
    ticker = get_ticker(
        symbol
    )

    price = (
        ticker["price"]
        if ticker
        else candles_5m[-1]["close"]
    )

    return {

        "symbol":
            symbol,

        "direction":
            decision["direction"],

        "score":
            decision["score"],

        "buy_score":
            decision["buy_score"],

        "sell_score":
            decision["sell_score"],

        "buy_pressure":
            decision["buy_pressure"],

        "sell_pressure":
            decision["sell_pressure"],

        "price":
            price,

        "atr":
            analysis_5m["atr"],

        "5m":
            analysis_5m,

        "15m":
            analysis_15m,

        "candles_5m":
            candles_5m,

        "candles_15m":
            candles_15m,
    }


# ============================================================
# Scanner
# ============================================================

def scan_market():

    print("\n")
    print("=" * 60)
    print(
        "TABDEAL FUTURES 5-COIN SCANNER"
    )
    print("=" * 60)

    symbols = verify_symbols()

    if not symbols:

        print(
            "هیچ‌کدام از 5 ارز پیدا نشد."
        )

        send_telegram(
            "❌ ربات نتوانست نمادهای "
            "Futures تبدیل را پیدا کند."
        )

        return

    print(
        "\nتعداد ارزهای مورد بررسی:",
        len(symbols)
    )

    signals = load_signals()

    new_signals = []

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        print(
            f"\n[{index}/{len(symbols)}]"
            f" {symbol}"
        )

        try:

            analysis = (
                analyze_symbol(
                    symbol
                )
            )

            if not analysis:

                print(
                    "No sufficient data."
                )

                continue

            print(
                "5m BUY:",
                analysis["5m"][
                    "buy_score"
                ],
                "SELL:",
                analysis["5m"][
                    "sell_score"
                ]
            )

            print(
                "15m BUY:",
                analysis["15m"][
                    "buy_score"
                ],
                "SELL:",
                analysis["15m"][
                    "sell_score"
                ]
            )

            print(
                "FINAL:",
                analysis[
                    "direction"
                ],
                analysis[
                    "score"
                ]
            )

            if (
                analysis[
                    "direction"
                ]
                == "WAIT"
            ):

                print(
                    "WAIT - no signal"
                )

                continue

            signal = create_signal(
                analysis
            )

            if not signal:
                continue

            duplicate = (
                is_duplicate_signal(
                    signals,
                    signal["symbol"],
                    signal["direction"],
                    signal["pattern_id"]
                )
            )

            if duplicate:

                print(
                    "DUPLICATE SIGNAL IGNORED"
                )

                continue

            signals.append(
                signal
            )

            new_signals.append(
                signal
            )

            send_telegram(
                signal_to_message(
                    signal
                )
            )

            print(
                "NEW SIGNAL:",
                signal["symbol"],
                signal["direction"]
            )

            time.sleep(
                0.5
            )

        except Exception as e:

            print(
                "ANALYSIS ERROR:",
                symbol,
                e
            )

    save_signals(
        signals
    )

    # --------------------------------------------------------
    # گزارش نهایی
    # --------------------------------------------------------

    report = (
        "📊 اسکن Futures تبدیل تمام شد\n\n"
        f"🪙 ارزهای بررسی‌شده: "
        f"{len(symbols)}\n"
        f"🆕 سیگنال جدید: "
        f"{len(new_signals)}\n\n"
        "BTC / ETH / ADA / DOGE / SOL\n"
        "⏱ تایم‌فریم: 5m + 15m\n\n"
        "🤖 وضعیت: تحلیل فقط"
    )

    send_telegram(
        report
    )

    print(
        "\nSCAN FINISHED"
    )


# ============================================================
# Main
# ============================================================

def main():

    print(
        "\n"
        "====================================================\n"
        " TABDEAL FUTURES ANALYSIS BOT\n"
        " 5 COINS / 5m + 15m\n"
        " ANALYSIS ONLY - NO TRADE EXECUTION\n"
        "===================================================="
    )

    print(
        "Started:",
        now_iso()
    )

    try:

        scan_market()

    except Exception as e:

        print(
            "FATAL ERROR:",
            e
        )

        send_telegram(
            "❌ خطای اصلی ربات\n\n"
            + str(e)[:1000]
        )

        raise


if __name__ == "__main__":
    main()
