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
            )
