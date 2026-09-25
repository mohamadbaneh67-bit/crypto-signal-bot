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
            )

    except Exception as e:

        print(
            "TELEGRAM REQUEST ERROR:",
            e
        )

    return False
