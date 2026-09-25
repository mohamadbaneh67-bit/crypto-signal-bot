# ============================================================
# TABDEAL FUTURES ANALYSIS BOT
# VERSION 3 - DIRECT KLINES
# فقط داده Futures تبدیل
# 5m + 15m
# بدون اجرای معامله
# ============================================================

import os
import json
import time
import hashlib
import requests
from datetime import datetime, timezone


# ============================================================
# SETTINGS
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# TABDEAL API
# ============================================================

API_BASES = [
    "https://api1.tabdeal.org",
    "https://api.tabdeal.org",
]

EXCHANGE_INFO_PATHS = [
    "/r/fapi/v1/exchangeInfo",
    "/fapi/v1/exchangeInfo",
]

KLINES_PATHS = [
    "/r/fapi/v1/klines",
    "/fapi/v1/klines",
]

DEPTH_PATHS = [
    "/r/fapi/v1/depth",
    "/fapi/v1/depth",
]


# ============================================================
# SYMBOLS
# ============================================================

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "SOLUSDT",
]


TIMEFRAMES = {
    "5m": 5,
    "15m": 15,
}


# ============================================================
# SIGNAL SETTINGS
# ============================================================

MIN_SIGNAL_SCORE = 70

TP1_ATR = 1.0
TP2_ATR = 2.0
SL_ATR = 1.2

SIGNAL_TIMEOUT_CANDLES = 12

MAX_STORED_SIGNALS = 3000


# ============================================================
# FILES
# ============================================================

LEARNING_FILE = "tabdeal_learning.json"
SIGNALS_FILE = "tabdeal_signals.json"


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "Tabdeal-Futures-Analysis-Bot/3.0",

    "Accept":
        "application/json",
})


# ============================================================
# BASIC HELPERS
# ============================================================

def now_iso():

    return datetime.now(
        timezone.utc
    ).isoformat()


def safe_float(
    value,
    default=0.0
):

    try:

        return float(value)

    except Exception:

        return default


def normalize_symbol(symbol):

    return (
        str(symbol)
        .replace("_", "")
        .replace("-", "")
        .upper()
    )


# ============================================================
# JSON
# ============================================================

def load_json(
    filename,
    default
):

    if not os.path.exists(filename):

        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"JSON LOAD ERROR [{filename}]:",
            e
        )

        return default


def save_json(
    filename,
    data
):

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
# API REQUEST
# ============================================================

def api_get(
    paths,
    params=None
):

    if isinstance(
        paths,
        str
    ):

        paths = [paths]

    for base in API_BASES:

        for path in paths:

            url = (
                base.rstrip("/")
                +
                path
            )

            try:

                response = SESSION.get(
                    url,
                    params=params,
                    timeout=20
                )

                print(
                    "REQUEST:",
                    response.url,
                    "STATUS:",
                    response.status_code
                )

                if response.ok:

                    try:

                        return response.json()

                    except Exception as e:

                        print(
                            "JSON ERROR:",
                            e
                        )

                else:

                    print(
                        "API ERROR:",
                        response.status_code,
                        response.text[:250]
                    )

            except Exception as e:

                print(
                    "REQUEST ERROR:",
                    e
                )

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram bot token missing."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID missing."
        )

        return False

    try:

        response = SESSION.post(

            "https://api.telegram.org/bot"
            +
            TELEGRAM_BOT_TOKEN
            +
            "/sendMessage",

            json={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    text,
            },

            timeout=20,
        )

        if response.ok:

            return True

        print(
            "TELEGRAM ERROR:",
            response.status_code,
            response.text[:300]
        )

    except Exception as e:

        print(
            "TELEGRAM REQUEST ERROR:",
            e
        )

    return False


# ============================================================
# EXCHANGE INFO
# ============================================================

def get_exchange_info():

    return api_get(
        EXCHANGE_INFO_PATHS
    )


# ============================================================
# SYMBOL CHECK
# ============================================================

def get_available_symbols():

    data = get_exchange_info()

    if not isinstance(
        data,
        dict
    ):

        print(
            "Exchange information unavailable."
        )

        return set()

    symbols = set()

    rows = data.get(
        "symbols",
        []
    )

    for row in rows:

        if not isinstance(
            row,
            dict
        ):

            continue

        symbol = normalize_symbol(
            row.get(
                "symbol",
                ""
            )
        )

        if symbol:

            symbols.add(symbol)

    return symbols


# ============================================================
# DIRECT TABDEAL KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit=200
):

    params = {

        "symbol":
            normalize_symbol(symbol),

        "interval":
            interval,

        "limit":
            limit,
    }

    data = api_get(
        KLINES_PATHS,
        params=params
    )

    if not isinstance(
        data,
        list
    ):

        print(
            "KLINES ERROR:",
            symbol,
            interval
        )

        return []

    candles = []

    for row in data:

        if not isinstance(
            row,
            (list, tuple)
        ):

            continue

        if len(row) < 6:

            continue

        try:

            candle = {

                "open_time":
                    int(
                        safe_float(
                            row[0]
                        )
                    ),

                "open":
                    safe_float(
                        row[1]
                    ),

                "high":
                    safe_float(
                        row[2]
                    ),

                "low":
                    safe_float(
                        row[3]
                    ),

                "close":
                    safe_float(
                        row[4]
                    ),

                "volume":
                    safe_float(
                        row[5]
                    ),
            }

            if (
                candle["open"] > 0
                and
                candle["high"] > 0
                and
                candle["low"] > 0
                and
                candle["close"] > 0
            ):

                candles.append(
                    candle
                )

        except Exception:

            continue

    print(
        "KLINES:",
        symbol,
        interval,
        "COUNT:",
        len(candles)
    )

    return candles


# ============================================================
# ORDER BOOK
# ============================================================

def get_order_book(
    symbol,
    limit=100
):

    params = {

        "symbol":
            normalize_symbol(symbol),

        "limit":
            limit,
    }

    data = api_get(
        DEPTH_PATHS,
        params=params
    )

    if not isinstance(
        data,
        dict
    ):

        return None

    bids = data.get(
        "bids",
        []
    )

    asks = data.get(
        "asks",
        []
    )

    if not bids or not asks:

        return None

    bid_volume = 0.0
    ask_volume = 0.0

    best_bid = 0.0
    best_ask = 0.0

    for row in bids:

        if len(row) < 2:

            continue

        price = safe_float(
            row[0]
        )

        qty = safe_float(
            row[1]
        )

        best_bid = max(
            best_bid,
            price
        )

        bid_volume += qty

    for row in asks:

        if len(row) < 2:

            continue

        price = safe_float(
            row[0]
        )

        qty = safe_float(
            row[1]
        )

        if (
            best_ask == 0
            or
            price < best_ask
        ):

            best_ask = price

        ask_volume += qty

    total = (
        bid_volume
        +
        ask_volume
    )

    if total > 0:

        buy_pressure = (
            bid_volume
            /
            total
            *
            100
        )

        sell_pressure = (
            ask_volume
            /
            total
            *
            100
        )

    else:

        buy_pressure = 50.0
        sell_pressure = 50.0

    spread = 0.0

    if (
        best_bid > 0
        and
        best_ask > 0
    ):

        spread = (
            (
                best_ask
                -
                best_bid
            )
            /
            best_bid
        ) * 100

    return {

        "best_bid":
            best_bid,

        "best_ask":
            best_ask,

        "bid_volume":
            bid_volume,

        "ask_volume":
            ask_volume,

        "buy_pressure":
            buy_pressure,

        "sell_pressure":
            sell_pressure,

        "spread_percent":
            spread,
    }


# ============================================================
# END PART 1
# ============================================================

print(
    "\n"
    "====================================================\n"
    " TABDEAL FUTURES BOT - PART 1 LOADED\n"
    " DIRECT KLINES - NO TRADES ENDPOINT\n"
    " فقط 5 ارز | 5m + 15m\n"
    " بدون اجرای معامله\n"
    "===================================================="
        )# ============================================================
# JSON STORAGE
# ============================================================

def load_json(
    filename,
    default
):

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


def save_json(
    filename,
    data
):

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
# API REQUEST
# ============================================================

def api_get(
    paths,
    params=None
):

    if isinstance(
        paths,
        str
    ):

        paths = [paths]

    for base in API_BASES:

        for path in paths:

            url = (
                base.rstrip("/")
                +
                path
            )

            try:

                response = SESSION.get(
                    url,
                    params=params,
                    timeout=20
                )

                print(
                    "REQUEST:",
                    response.url,
                    "STATUS:",
                    response.status_code
                )

                if response.ok:

                    try:

                        return response.json()

                    except Exception as e:

                        print(
                            "JSON ERROR:",
                            e
                        )

                else:

                    print(
                        "API ERROR:",
                        response.status_code,
                        response.text[:250]
                    )

            except Exception as e:

                print(
                    "REQUEST ERROR:",
                    e
                )

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram bot token missing."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID missing."
        )

        return False

    try:

        response = SESSION.post(

            "https://api.telegram.org/bot"
            +
            TELEGRAM_BOT_TOKEN
            +
            "/sendMessage",

            json={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    text,
            },

            timeout=20,
        )

        if response.ok:

            return True

        print(
            "TELEGRAM ERROR:",
            response.status_code,
            response.text[:300]
        )# ============================================================
# EXCHANGE INFO
# ============================================================

def get_exchange_info():

    return api_get(
        EXCHANGE_INFO_PATHS
    )


# ============================================================
# AVAILABLE FUTURES SYMBOLS
# ============================================================

def get_available_symbols():

    data = get_exchange_info()

    if not isinstance(
        data,
        dict
    ):

        print(
            "Exchange information unavailable."
        )

        return set()

    symbols = set()

    rows = data.get(
        "symbols",
        []
    )

    for row in rows:

        if not isinstance(
            row,
            dict
        ):

            continue

        symbol = normalize_symbol(
            row.get(
                "symbol",
                ""
            )
        )

        if symbol:

            symbols.add(symbol)

    return symbols


# ============================================================
# DIRECT FUTURES KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
    limit=200
):

    params = {
        "symbol":
            normalize_symbol(symbol),

        "interval":
            interval,

        "limit":
            limit,
    }

    data = api_get(
        KLINES_PATHS,
        params=params
    )

    if not isinstance(
        data,
        list
    ):

        print(
            "KLINES ERROR:",
            symbol,
            interval
        )

        return []

    candles = []

    for row in data:

        if not isinstance(
            row,
            (list, tuple)
        ):

            continue

        if len(row) < 6:

            continue

        try:

            candle = {
                "open_time":
                    int(
                        safe_float(
                            row[0]
                        )
                    ),

                "open":
                    safe_float(
                        row[1]
                    ),

                "high":
                    safe_float(
                        row[2]
                    ),

                "low":
                    safe_float(
                        row[3]
                    ),

                "close":
                    safe_float(
                        row[4]
                    ),

                "volume":
                    safe_float(
                        row[5]
                    ),
            }

            if (
                candle["open"] > 0
                and
                candle["high"] > 0
                and
                candle["low"] > 0
                and
                candle["close"] > 0
            ):

                candles.append(
                    candle
                )

        except Exception:

            continue

    print(
        "KLINES:",
        symbol,
        interval,
        "COUNT:",
        len(candles)
    )

    return candles


# ============================================================
# ORDER BOOK
# ============================================================

def get_order_book(
    symbol,
    limit=100
):

    params = {
        "symbol":
            normalize_symbol(symbol),

        "limit":
            limit,
    }

    data = api_get(
        DEPTH_PATHS,
        params=params
    )

    if not isinstance(
        data,
        dict
    ):

        return None

    bids = data.get(
        "bids",
        []
    )

    asks = data.get(
        "asks",
        []
    )

    if not bids or not asks:

        return None

    bid_volume = 0.0
    ask_volume = 0.0

    best_bid = 0.0
    best_ask = 0.0

    for row in bids:

        if len(row) < 2:
            continue

        price = safe_float(
            row[0]
        )

        qty = safe_float(
            row[1]
        )

        if price > best_bid:
            best_bid = price

        bid_volume += qty

    for row in asks:

        if len(row) < 2:
            continue

        price = safe_float(
            row[0]
        )

        qty = safe_float(
            row[1]
        )

        if (
            best_ask == 0
            or
            price < best_ask
        ):

            best_ask = price

        ask_volume += qty

    total = (
        bid_volume
        +
        ask_volume
    )

    if total > 0:

        buy_pressure = (
            bid_volume
            /
            total
            *
            100
        )

        sell_pressure = (
            ask_volume
            /
            total
            *
            100
        )

    else:

        buy_pressure = 50.0
        sell_pressure = 50.0

    spread = 0.0

    if (
        best_bid > 0
        and
        best_ask > 0
    ):

        spread = (
            (
                best_ask
                -
                best_bid
            )
            /
            best_bid
        ) * 100

    return {
        "best_bid":
            best_bid,

        "best_ask":
            best_ask,

        "bid_volume":
            bid_volume,

        "ask_volume":
            ask_volume,

        "buy_pressure":
            buy_pressure,

        "sell_pressure":
            sell_pressure,

        "spread_percent":
            spread,
    }


# ============================================================
# PART 1 COMPLETE
# ============================================================

print(
    "\n"
    "====================================================\n"
    " TABDEAL FUTURES BOT - PART 1 LOADED\n"
    " DIRECT KLINES - NO TRADES ENDPOINT\n"
    " فقط 5 ارز | 5m + 15m\n"
    " بدون اجرای معامله\n"
    "===================================================="
            )# ============================================================
# PART 2 - INDICATORS
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

    multiplier = 2.0 / (
        period + 1
    )

    result = (
        sum(values[:period])
        / period
    )

    for value in values[period:]:

        result = (
            (
                value - result
            )
            * multiplier
        ) + result

    return result


def rsi(
    values,
    period=14
):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(
        1,
        len(values)
    ):

        change = (
            values[i]
            -
            values[i - 1]
        )

        if change > 0:

            gains.append(
                change
            )

            losses.append(0.0)

        else:

            gains.append(0.0)

            losses.append(
                abs(change)
            )

    if len(gains) < period:
        return None

    avg_gain = (
        sum(gains[:period])
        /
        period
    )

    avg_loss = (
        sum(losses[:period])
        /
        period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                *
                (period - 1)
            )
            +
            gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                *
                (period - 1)
            )
            +
            losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    relative_strength = (
        avg_gain
        /
        avg_loss
    )

    return (
        100.0
        -
        (
            100.0
            /
            (
                1.0
                +
                relative_strength
            )
        )
    )


def atr(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]

        previous = candles[i - 1]

        high = safe_float(
            current.get("high")
        )

        low = safe_float(
            current.get("low")
        )

        previous_close = safe_float(
            previous.get("close")
        )

        true_range = max(
            high - low,
            abs(
                high
                -
                previous_close
            ),
            abs(
                low
                -
                previous_close
            )
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
        /
        period
    )


def macd(
    values,
    fast_period=12,
    slow_period=26,
    signal_period=9
):

    if len(values) < (
        slow_period
        +
        signal_period
    ):

        return None

    macd_values = []

    for i in range(
        slow_period,
        len(values) + 1
    ):

        window = values[
            :i
        ]

        fast = ema(
            window,
            fast_period
        )

        slow = ema(
            window,
            slow_period
        )

        if (
            fast is None
            or
            slow is None
        ):

            continue

        macd_values.append(
            fast - slow
        )

    if len(macd_values) < signal_period:
        return None

    macd_line = (
        macd_values[-1]
    )

    signal_line = ema(
        macd_values,
        signal_period
    )

    if signal_line is None:
        return None

    histogram = (
        macd_line
        -
        signal_line
    )

    return {
        "macd":
            macd_line,

        "signal":
            signal_line,

        "histogram":
            histogram,
            }# ============================================================
# MARKET PRESSURE ANALYSIS
# ============================================================

def calculate_volume_pressure(candles):

    if not candles:
        return {
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "buy_percent": 50.0,
            "sell_percent": 50.0,
        }

    recent = candles[-20:]

    buy_volume = 0.0
    sell_volume = 0.0

    for candle in recent:

        volume = safe_float(
            candle.get("volume")
        )

        if volume <= 0:
            continue

        open_price = safe_float(
            candle.get("open")
        )

        close_price = safe_float(
            candle.get("close")
        )

        if close_price > open_price:

            buy_volume += volume

        elif close_price < open_price:

            sell_volume += volume

        else:

            buy_volume += (
                volume * 0.5
            )

            sell_volume += (
                volume * 0.5
            )

    total = (
        buy_volume
        +
        sell_volume
    )

    if total <= 0:

        return {
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "buy_percent": 50.0,
            "sell_percent": 50.0,
        }

    buy_percent = (
        buy_volume
        /
        total
        *
        100.0
    )

    sell_percent = (
        sell_volume
        /
        total
        *
        100.0
    )

    return {
        "buy_volume":
            buy_volume,

        "sell_volume":
            sell_volume,

        "buy_percent":
            buy_percent,

        "sell_percent":
            sell_percent,
    }


# ============================================================
# ORDER BOOK PRESSURE
# ============================================================

def calculate_orderbook_pressure(
    orderbook
):

    if not orderbook:

        return {
            "buy_percent": 50.0,
            "sell_percent": 50.0,
            "imbalance": 0.0,
        }

    buy_percent = safe_float(
        orderbook.get(
            "buy_pressure",
            50.0
        ),
        50.0
    )

    sell_percent = safe_float(
        orderbook.get(
            "sell_pressure",
            50.0
        ),
        50.0
    )

    total = (
        buy_percent
        +
        sell_percent
    )

    if total <= 0:

        buy_percent = 50.0
        sell_percent = 50.0

    else:

        buy_percent = (
            buy_percent
            /
            total
            *
            100.0
        )

        sell_percent = (
            sell_percent
            /
            total
            *
            100.0
        )

    imbalance = (
        buy_percent
        -
        sell_percent
    ) / 100.0

    return {
        "buy_percent":
            buy_percent,

        "sell_percent":
            sell_percent,

        "imbalance":
            imbalance,
    }


# ============================================================
# TREND DETECTION
# ============================================================

def detect_trend(
    price,
    ema9,
    ema21,
    ema50
):

    if any(
        value is None
        for value in [
            price,
            ema9,
            ema21,
            ema50,
        ]
    ):

        return "NEUTRAL"

    if (
        price > ema9
        and
        ema9 > ema21
        and
        ema21 > ema50
    ):

        return "BULLISH"

    if (
        price < ema9
        and
        ema9 < ema21
        and
        ema21 < ema50
    ):

        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# TIMEFRAME ANALYSIS
# ============================================================

def analyze_timeframe(
    symbol,
    interval,
    candles,
    orderbook
):

    if len(candles) < 55:

        print(
            "Not enough candles:",
            symbol,
            interval,
            len(candles)
        )

        return None

    closes = [

        safe_float(
            candle.get("close")
        )

        for candle in candles

        if safe_float(
            candle.get("close")
        ) > 0
    ]

    if len(closes) < 55:
        return None

    current_price = closes[-1]

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

    macd_data = macd(
        closes
    )

    volume_pressure = (
        calculate_volume_pressure(
            candles
        )
    )

    book_pressure = (
        calculate_orderbook_pressure(
            orderbook
        )
    )

    if (
        ema9 is None
        or
        ema21 is None
        or
        ema50 is None
        or
        rsi_value is None
        or
        atr_value is None
        or
        macd_data is None
    ):

        return None

    trend = detect_trend(
        current_price,
        ema9,
        ema21,
        ema50
    )

    atr_percent = 0.0

    if current_price > 0:

        atr_percent = (
            atr_value
            /
            current_price
            *
            100.0
        )

    return {
        "symbol":
            symbol,

        "interval":
            interval,

        "price":
            current_price,

        "ema9":
            ema9,

        "ema21":
            ema21,

        "ema50":
            ema50,

        "rsi":
            rsi_value,

        "atr":
            atr_value,

        "atr_percent":
            atr_percent,

        "macd":
            macd_data["macd"],

        "macd_signal":
            macd_data["signal"],

        "macd_histogram":
            macd_data["histogram"],

        "trend":
            trend,

        "buy_volume_percent":
            volume_pressure[
                "buy_percent"
            ],

        "sell_volume_percent":
            volume_pressure[
                "sell_percent"
            ],

        "book_buy_percent":
            book_pressure[
                "buy_percent"
            ],

        "book_sell_percent":
            book_pressure[
                "sell_percent"
            ],

        "book_imbalance":
            book_pressure[
                "imbalance"
            ],

        "candle_count":
            len(candles),
            }

    except Exception as e:

        print(
            "TELEGRAM REQUEST ERROR:",
            e
        )

    return False# ============================================================
# PART 2 - FINAL CHECK
# ============================================================

def calculate_signal_strength(
    analysis
):

    if not analysis:
        return 0

    buy_score = 0
    sell_score = 0

    price = safe_float(
        analysis.get("price")
    )

    ema9 = safe_float(
        analysis.get("ema9")
    )

    ema21 = safe_float(
        analysis.get("ema21")
    )

    ema50 = safe_float(
        analysis.get("ema50")
    )

    rsi_value = safe_float(
        analysis.get("rsi"),
        50.0
    )

    macd_histogram = safe_float(
        analysis.get(
            "macd_histogram"
        )
    )

    buy_volume = safe_float(
        analysis.get(
            "buy_volume_percent",
            50.0
        ),
        50.0
    )

    sell_volume = safe_float(
        analysis.get(
            "sell_volume_percent",
            50.0
        ),
        50.0
    )

    book_buy = safe_float(
        analysis.get(
            "book_buy_percent",
            50.0
        ),
        50.0
    )

    book_sell = safe_float(
        analysis.get(
            "book_sell_percent",
            50.0
        ),
        50.0
    )


    # --------------------------------------------------------
    # BUY CONDITIONS
    # --------------------------------------------------------

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

    if macd_histogram > 0:
        buy_score += 10

    if buy_volume > 55:
        buy_score += 10

    if book_buy > 55:
        buy_score += 10


    # --------------------------------------------------------
    # SELL CONDITIONS
    # --------------------------------------------------------

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

    if macd_histogram < 0:
        sell_score += 10

    if sell_volume > 55:
        sell_score += 10

    if book_sell > 55:
        sell_score += 10


    return {
        "buy_score":
            buy_score,

        "sell_score":
            sell_score,
    }


# ============================================================
# PART 2 LOADED
# ============================================================

print(
    "\n"
    "====================================================\n"
    " TABDEAL FUTURES BOT - PART 2 LOADED\n"
    " EMA / RSI / MACD / ATR / VOLUME / ORDER BOOK\n"
    " 5m + 15m ANALYSIS\n"
    "===================================================="
    )
