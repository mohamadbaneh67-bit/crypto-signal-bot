import os
import json
import math
import time
import hashlib
import threading
from datetime import datetime, timezone

import requests
import websocket

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Tabdeal Futures / Professional Leverage public endpoints.
EXCHANGE_INFO_URL = "https://api1.tabdeal.org/r/fapi/v1/exchangeInfo"
DEPTH_URL = "https://api1.tabdeal.org/r/fapi/v1/depth"
TRADE_WS_URL = "wss://api1.tabdeal.org/special_margin/broadcast/"
DEPTH_WS_URL = "wss://api1.tabdeal.org/special_margin/stream/"

# Symbols used for the first validation run.
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "SOLUSDT",
]

COLLECT_SECONDS = int(
    os.getenv("TABDEAL_COLLECT_SECONDS", "210")
)

MIN_SIGNAL_SCORE = 70
DEDUP_MINUTES = 60

MAX_STORED_SIGNALS = 3000
MAX_SNAPSHOTS_PER_SYMBOL = 2500

TP1_ATR = 1.0
TP2_ATR = 2.0
SL_ATR = 1.2

MAX_CANDLES_PER_SYMBOL = 1500
MAX_TRADES_PER_SYMBOL = 30000
MAX_SIGNALS = 3000

STATE_FILE = "tabdeal_futures_state.json"
SIGNALS_FILE = "tabdeal_signals.json"
LEARNING_FILE = "tabdeal_learning.json"

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Tabdeal-Futures-Analysis-Bot/3.0",
    "Accept": "application/json",
})

state_lock = threading.Lock()

run_state = {
    "trades": {
        symbol: []
        for symbol in SYMBOLS
    },

    "book": {
        symbol: {
            "imbalance": 0.0,
            "price": 0.0,
            "timestamp": 0,
        }
        for symbol in SYMBOLS
    },
}


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

    except (
        TypeError,
        ValueError
    ):

        return default


def load_json(
    path,
    default
):

    if not os.path.exists(path):
        return default

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as exc:

        print(
            f"JSON LOAD ERROR [{path}]: {exc}"
        )

        return default


def save_json(
    path,
    data
):

    tmp = path + ".tmp"

    try:

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            tmp,
            path
        )

        return True

    except Exception as exc:

        print(
            f"JSON SAVE ERROR [{path}]: {exc}"
        )

        try:

            if os.path.exists(tmp):
                os.remove(tmp)

        except OSError:
            pass

        return False


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

            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage",

            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
            },

            timeout=20,
        )

        print(
            "TELEGRAM STATUS:",
            response.status_code
        )

        if response.ok:
            return True

        print(
            "TELEGRAM ERROR:",
            response.text[:300]
        )

    except Exception as exc:

        print(
            "TELEGRAM ERROR:",
            exc
        )

    return False


def api_get(
    url,
    params=None
):

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
            return response.json()

        print(
            "API ERROR:",
            response.status_code,
            response.text[:300]
        )

    except Exception as exc:

        print(
            "API ERROR:",
            exc
        )

    return None


def normalize_symbol(
    symbol
):

    return (
        str(symbol or "")
        .replace("_", "")
        .replace("-", "")
        .upper()
    )


def underscore_symbol(
    symbol
):

    symbol = normalize_symbol(symbol)

    if symbol.endswith("USDT"):

        return (
            symbol[:-4]
            + "_USDT"
        )

    return symbol


def get_exchange_info():

    return api_get(
        EXCHANGE_INFO_URL
    )


def get_depth(
    symbol
):

    data = api_get(

        DEPTH_URL,

        {
            "symbol":
                normalize_symbol(symbol),

            "limit":
                50,
        }
    )

    if not isinstance(
        data,
        dict
    ):

        return None

    bids = (
        data.get("bids")
        or []
    )

    asks = (
        data.get("asks")
        or []
    )

    if not bids or not asks:
        return None

    try:
        bid_qty = sum(
            safe_float(x[1])
            for x in bids[:50]
        )

        ask_qty = sum(
            safe_float(x[1])
            for x in asks[:50]
        )

    except Exception:
        return None

    total_qty = bid_qty + ask_qty

    if total_qty <= 0:
        return None

    imbalance = (
        (bid_qty - ask_qty)
        / total_qty
    )

    return {
        "symbol": normalize_symbol(symbol),
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "imbalance": imbalance,
        "timestamp": time.time(),
    }
    def extract_trade(data):
    return None


def trade_ws_worker(symbol):
    Extract price and quantity from a Tabdeal Futures
    WebSocket trade message.
    """

    if not isinstance(data, dict):
        return None

    payload = data.get("data", data)

    if not isinstance(payload, dict):
        return None

    price = (
        payload.get("p")
        or payload.get("price")
        or payload.get("P")
    )

    quantity = (
        payload.get("q")
        or payload.get("quantity")
        or payload.get("Q")
        or 0
    )

    timestamp = (
        payload.get("T")
        or payload.get("E")
        or int(time.time() * 1000)
    )

    price = safe_float(price)
    quantity = safe_float(quantity)

    if price <= 0:
        return None

    return {
        "price": price,
        "quantity": quantity,
        "timestamp": timestamp / 1000.0,
    }
    def trade_ws_worker(symbol):
    """
    Listen for Tabdeal Futures trade broadcasts.

    The public broadcast may use different JSON wrappers,
    therefore the message parser is intentionally flexible.
    """

    symbol = normalize_symbol(symbol)

    def on_message(ws, message):

        trade = extract_trade(
            message,
            symbol
        )

        if trade is None:
            return

        with state_lock:

            trades = run_state[
                "trades"
            ].setdefault(
                symbol,
                []
            )

            trades.append(
                trade
            )

            if len(trades) > MAX_TRADES_PER_SYMBOL:

                del trades[
                    :-MAX_TRADES_PER_SYMBOL
                ]

    def on_error(ws, error):

        print(
            f"TRADE WS ERROR [{symbol}]:",
            error
        )

    def on_close(
        ws,
        close_status_code,
        close_msg
    ):

        print(
            f"TRADE WS CLOSED [{symbol}]",
            close_status_code,
            close_msg
        )

    def on_open(ws):

        print(
            f"TRADE WS CONNECTED [{symbol}]"
        )

        subscriptions = [
            {
                "method": "SUBSCRIBE",
                "params": [
                    f"{symbol.lower()}@trade"
                ],
                "id": int(
                    time.time()
                    * 1000
                ) % 1000000,
            }
        ]

        for request in subscriptions:

            try:

                ws.send(
                    json.dumps(
                        request
                    )
                )

                print(
                    "TRADE WS SUBSCRIBE:",
                    request
                )

            except Exception as exc:

                print(
                    "TRADE WS SEND ERROR:",
                    exc
                )

    while True:

        try:

            ws = websocket.WebSocketApp(

                TRADE_WS_URL,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close,
            )

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10
            )

        except Exception as exc:

            print(
                f"TRADE WS EXCEPTION [{symbol}]:",
                exc
            )

        print(
            f"Reconnecting trade WS [{symbol}]..."
        )

        time.sleep(5)


def extract_book(
    payload,
    subscribed_symbol
):

    if isinstance(
        payload,
        str
    ):

        try:

            payload = json.loads(
                payload
            )

        except Exception:

            return None

    if not isinstance(
        payload,
        dict
    ):

        return None

    data = payload.get(
        "data"
    )

    if isinstance(
        data,
        dict
    ):

        source = data

    else:

        source = payload

    symbol = normalize_symbol(

        source.get("symbol")
        or source.get("s")
        or payload.get("symbol")
        or subscribed_symbol
    )

    if (
        symbol
        != normalize_symbol(
            subscribed_symbol
        )
    ):

        return None

    bids = (

        source.get("bids")
        or source.get("B")
        or source.get("buy")
        or []
    )

    asks = (

        source.get("asks")
        or source.get("A")
        or source.get("sell")
        or []
    )

    if not bids or not asks:
        return None

    try:

        bid_qty = sum(

            safe_float(
                item[1]
            )

            for item in bids[:50]
        )

        ask_qty = sum(

            safe_float(
                item[1]
            )

            for item in asks[:50]
        )

        best_bid = safe_float(
            bids[0][0]
        )

        best_ask = safe_float(
            asks[0][0]
        )

    except Exception:

        return None

    total_qty = (
        bid_qty
        + ask_qty
    )

    if total_qty <= 0:
        return None

    imbalance = (

        (bid_qty - ask_qty)
        / total_qty
    )

    price = (

        (best_bid + best_ask)
        / 2

        if best_bid > 0
        and best_ask > 0

        else 0.0
    )

    return {

        "symbol":
            symbol,

        "price":
            price,

        "imbalance":
            imbalance,

        "bid_qty":
            bid_qty,

        "ask_qty":
            ask_qty,

        "timestamp":
            time.time(),
    }


def book_ws_worker(symbol):

    symbol = normalize_symbol(
        symbol
    )

    def on_message(ws, message):

        book = extract_book(
            message,
            symbol
        )

        if book is None:
            return

        with state_lock:

            run_state[
                "book"
            ][symbol] = book

    def on_error(ws, error):

        print(
            f"BOOK WS ERROR [{symbol}]:",
            error
        )

    def on_close(
        ws,
        close_status_code,
        close_msg
    ):

        print(
            f"BOOK WS CLOSED [{symbol}]",
            close_status_code,
            close_msg
        )

    def on_open(ws):

        print(
            f"BOOK WS CONNECTED [{symbol}]"
        )

        request = {

            "method":
                "SUBSCRIBE",

            "params": [

                f"{symbol.lower()}@depth"
            ],

            "id":
                int(
                    time.time()
                    * 1000
                ) % 1000000,
        }

        try:

            ws.send(
                json.dumps(
                    request
                )
            )

            print(
                "BOOK WS SUBSCRIBE:",
                request
            )

        except Exception as exc:

            print(
                "BOOK WS SEND ERROR:",
                exc
            )

    while True:

        try:

            ws = websocket.WebSocketApp(

                DEPTH_WS_URL,

                on_open=on_open,

                on_message=on_message,

                on_error=on_error,

                on_close=on_close,
            )

            ws.run_forever(
                ping_interval=20,
                ping_timeout=10
            )

        except Exception as exc:

            print(
                f"BOOK WS EXCEPTION [{symbol}]:",
                exc
            )

        print(
            f"Reconnecting book WS [{symbol}]..."
        )

        time.sleep(5)


def start_websocket_workers():

    workers = []

    for symbol in SYMBOLS:

        trade_thread = threading.Thread(

            target=trade_ws_worker,

            args=(symbol,),

            daemon=True
        )

        book_thread = threading.Thread(

            target=book_ws_worker,

            args=(symbol,),

            daemon=True
        )

        trade_thread.start()

        book_thread.start()

        workers.append(
            trade_thread
        )

        workers.append(
            book_thread
        )

        time.sleep(0.5)

    return workers


def get_live_trades(
    symbol
):

    symbol = normalize_symbol(
        symbol
    )

    with state_lock:

        return list(
            run_state[
                "trades"
            ].get(
                symbol,
                []
            )
        )


def get_live_book(
    symbol
):

    symbol = normalize_symbol(
        symbol
    )

    with state_lock:

        return dict(
            run_state[
                "book"
            ].get(
                symbol,
                {}
            )
        )


def trades_to_candles(
    trades,
    minutes
):

    if not trades:
        return []

    seconds = (
        minutes * 60
    )

    buckets = {}

    for trade in trades:

        timestamp = safe_float(
            trade.get(
                "timestamp"
            )
        )

        price = safe_float(
            trade.get(
                "price"
            )
        )

        qty = safe_float(
            trade.get(
                "qty"
            )
        )

        if (
            timestamp <= 0
            or price <= 0
        ):

            continue

        bucket = (
            int(
                timestamp
                // seconds
            )
            * seconds
        )

        buckets.setdefault(
            bucket,
            []
        ).append(
            trade
        )

    candles = []

    for bucket in sorted(
        buckets
    ):

        rows = buckets[
            bucket
        ]

        prices = [

            safe_float(
                row.get(
                    "price"
                )
            )

            for row in rows
        ]

        prices = [
            price
            for price in prices
            if price > 0
        ]

        if not prices:
            continue

        total_volume = sum(

            abs(
                safe_float(
                    row.get(
                        "qty"
                    )
                )
            )

            for row in rows
        )

        imbalances = [

            safe_float(
                row.get(
                    "imbalance"
                )
            )

            for row in rows
        ]

        valid_imbalances = [

            value
            for value in imbalances
            if -1 <= value <= 1
        ]

        avg_imbalance = (

            sum(
                valid_imbalances
            )
            / len(
                valid_imbalances
            )

            if valid_imbalances

            else 0.0
        )

        candles.append({

            "open_time":
                int(
                    bucket * 1000
                ),

            "open":
                prices[0],

            "high":
                max(prices),

            "low":
                min(prices),

            "close":
                prices[-1],

            "volume":
                total_volume,

            "book_imbalance":
                avg_imbalance,

            "samples":
                len(rows),
        })

    return candles[
        -MAX_CANDLES_PER_SYMBOL:
    ]# ============================================================
# Technical Indicators
# ============================================================

def sma(
    values,
    period
):

    if len(values) < period:
        return None

    return (
        sum(values[-period:])
        / period
    )


def ema(
    values,
    period
):

    if len(values) < period:
        return None

    multiplier = (
        2.0 / (period + 1.0)
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

    for index in range(
        1,
        len(values)
    ):

        change = (
            values[index]
            - values[index - 1]
        )

        if change > 0:

            gains.append(
                change
            )

            losses.append(
                0.0
            )

        else:

            gains.append(
                0.0
            )

            losses.append(
                abs(change)
            )

    avg_gain = (
        sum(
            gains[:period]
        )
        / period
    )

    avg_loss = (
        sum(
            losses[:period]
        )
        / period
    )

    for index in range(
        period,
        len(gains)
    ):

        avg_gain = (

            (
                avg_gain
                * (period - 1)
            )
            + gains[index]

        ) / period

        avg_loss = (

            (
                avg_loss
                * (period - 1)
            )
            + losses[index]

        ) / period

    if avg_loss <= 0:

        if avg_gain > 0:
            return 100.0

        return 50.0

    relative_strength = (
        avg_gain
        / avg_loss
    )

    return (
        100.0
        -
        (
            100.0
            /
            (
                1.0
                + relative_strength
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

    for index in range(
        1,
        len(candles)
    ):

        current = candles[index]

        previous = (
            candles[index - 1]
        )

        high = safe_float(
            current.get(
                "high"
            )
        )

        low = safe_float(
            current.get(
                "low"
            )
        )

        previous_close = (
            safe_float(
                previous.get(
                    "close"
                )
            )
        )

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

    if len(
        true_ranges
    ) < period:

        return None

    return (

        sum(
            true_ranges[-period:]
        )
        / period
    )


def macd(
    values,
    fast_period=12,
    slow_period=26
):

    fast = ema(
        values,
        fast_period
    )

    slow = ema(
        values,
        slow_period
    )

    if (
        fast is None
        or slow is None
    ):

        return None

    return fast - slow


def price_change_pct(
    values,
    periods
):

    if len(values) <= periods:
        return 0.0

    previous = safe_float(
        values[-periods - 1]
    )

    current = safe_float(
        values[-1]
    )

    if previous == 0:
        return 0.0

    return (
        (
            current
            - previous
        )
        / previous
        * 100.0
    )


def volume_average(
    candles,
    period=20
):

    if len(candles) < period:
        return None

    values = [

        safe_float(
            candle.get(
                "volume"
            )
        )

        for candle
        in candles[-period:]
    ]

    return (
        sum(values)
        / len(values)
    )


def latest_volume_ratio(
    candles,
    period=20
):

    if not candles:
        return 0.0

    current = safe_float(
        candles[-1].get(
            "volume"
        )
    )

    average = volume_average(
        candles,
        period
    )

    if (
        average is None
        or average <= 0
    ):

        return 0.0

    return (
        current
        / average
    )


# ============================================================
# Timeframe Analysis
# ============================================================

def analyze_timeframe(
    candles,
    live_book=None
):

    if len(candles) < 55:
        return None

    closes = [

        safe_float(
            candle.get(
                "close"
            )
        )

        for candle in candles
    ]

    closes = [
        value
        for value in closes
        if value > 0
    ]

    if len(closes) < 55:
        return None

    current_price = (
        closes[-1]
    )

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

    macd_value = macd(
        closes
    )

    if any(
        value is None
        for value in [
            ema9,
            ema21,
            ema50,
            rsi_value,
            atr_value,
            macd_value,
        ]
    ):

        return None

    candle_imbalance = safe_float(
        candles[-1].get(
            "book_imbalance"
        )
    )

    live_imbalance = 0.0

    if isinstance(
        live_book,
        dict
    ):

        live_imbalance = safe_float(
            live_book.get(
                "imbalance"
            )
        )

    if abs(
        live_imbalance
    ) > 0.000001:

        imbalance = (
            live_imbalance
        )

    else:

        imbalance = (
            candle_imbalance
        )

    atr_pct = (

        atr_value
        / current_price
        * 100.0

        if current_price > 0

        else 0.0
    )

    change_5 = price_change_pct(
        closes,
        5
    )

    change_10 = price_change_pct(
        closes,
        10
    )

    volume_ratio = (
        latest_volume_ratio(
            candles,
            20
        )
    )

    bullish = (

        ema9 > ema21
        and ema21 > ema50
        and current_price > ema21
    )

    bearish = (

        ema9 < ema21
        and ema21 < ema50
        and current_price < ema21
    )

    # --------------------------------------------------------
    # BUY SCORE
    # --------------------------------------------------------

    buy_score = 0

    if current_price > ema9:
        buy_score += 12

    if ema9 > ema21:
        buy_score += 13

    if ema21 > ema50:
        buy_score += 13

    if rsi_value >= 50:
        buy_score += 10

    if (
        50 <= rsi_value <= 68
    ):
        buy_score += 8

    if macd_value > 0:
        buy_score += 10

    if imbalance >= 0.10:
        buy_score += 10

    if change_5 > 0:
        buy_score += 6

    if change_10 > 0:
        buy_score += 5

    if volume_ratio >= 1.10:
        buy_score += 3

    # --------------------------------------------------------
    # SELL SCORE
    # --------------------------------------------------------

    sell_score = 0

    if current_price < ema9:
        sell_score += 12

    if ema9 < ema21:
        sell_score += 13

    if ema21 < ema50:
        sell_score += 13

    if rsi_value <= 50:
        sell_score += 10

    if (
        32 <= rsi_value <= 50
    ):
        sell_score += 8

    if macd_value < 0:
        sell_score += 10

    if imbalance <= -0.10:
        sell_score += 10

    if change_5 < 0:
        sell_score += 6

    if change_10 < 0:
        sell_score += 5

    if volume_ratio >= 1.10:
        sell_score += 3

    return {

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

        "atr_pct":
            atr_pct,

        "macd":
            macd_value,

        "imbalance":
            imbalance,

        "change_5":
            change_5,

        "change_10":
            change_10,

        "volume_ratio":
            volume_ratio,

        "buy_score":
            buy_score,

        "sell_score":
            sell_score,

        "bullish":
            bullish,

        "bearish":
            bearish,

        "samples":
            len(candles),
    }


# ============================================================
# Multi-Timeframe Analysis
# ============================================================

def suggested_leverage(
    volatility_pct,
    score
):

    if score < MIN_SIGNAL_SCORE:
        return 1

    if volatility_pct <= 0.25:
        return 10

    if volatility_pct <= 0.45:
        return 7

    if volatility_pct <= 0.75:
        return 5

    if volatility_pct <= 1.20:
        return 3

    return 2


def analyze_symbol(
    symbol
):

    symbol = normalize_symbol(
        symbol
    )

    trades = get_live_trades(
        symbol
    )

    live_book = get_live_book(
        symbol
    )

    candles_5m = trades_to_candles(
        trades,
        5
    )

    candles_15m = trades_to_candles(
        trades,
        15
    )

    analysis_5m = analyze_timeframe(
        candles_5m,
        live_book
    )

    analysis_15m = analyze_timeframe(
        candles_15m,
        live_book
    )

    if (
        analysis_5m is None
        or analysis_15m is None
    ):

        return None

    buy_score = (

        analysis_5m[
            "buy_score"
        ] * 0.45

        +

        analysis_15m[
            "buy_score"
        ] * 0.55
    )

    sell_score = (

        analysis_5m[
            "sell_score"
        ] * 0.45

        +

        analysis_15m[
            "sell_score"
        ] * 0.55
    )

    if (
        buy_score >= MIN_SIGNAL_SCORE
        and buy_score > sell_score
    ):

        direction = "BUY"

        score = buy_score

    elif (
        sell_score >= MIN_SIGNAL_SCORE
        and sell_score > buy_score
    ):

        direction = "SELL"

        score = sell_score

    else:

        direction = "WAIT"

        score = max(
            buy_score,
            sell_score
        )

    volatility = max(

        analysis_5m[
            "atr_pct"
        ],

        analysis_15m[
            "atr_pct"
        ]
    )

    leverage = suggested_leverage(
        volatility,
        score
    )

    return {

        "symbol":
            symbol,

        "direction":
            direction,

        "score":
            round(
                score,
                2
            ),

        "buy_score":
            round(
                buy_score,
                2
            ),

        "sell_score":
            round(
                sell_score,
                2
            ),

        "price":
            analysis_5m[
                "price"
            ],

        "atr":
            analysis_5m[
                "atr"
            ],

        "volatility_pct":
            round(
                volatility,
                4
            ),

        "suggested_leverage":
            leverage,

        "5m":
            analysis_5m,

        "15m":
            analysis_15m,

        "candles_5m":
            len(candles_5m),

        "candles_15m":
            len(candles_15m),

        "trade_count":
            len(trades),

        "book":
            live_book,
    }# ============================================================
# Pattern / Learning
# ============================================================

def create_pattern_fingerprint(
    analysis
):

    a5 = analysis.get(
        "5m",
        {}
    )

    a15 = analysis.get(
        "15m",
        {}
    )

    def rsi_zone(value):

        value = safe_float(
            value
        )

        if value < 35:
            return "LOW"

        if value < 45:
            return "WEAK"

        if value < 55:
            return "MID"

        if value < 65:
            return "STRONG"

        return "HIGH"

    def trend(value):

        if value.get(
            "bullish",
            False
        ):

            return "BULL"

        if value.get(
            "bearish",
            False
        ):

            return "BEAR"

        return "NEUTRAL"

    parts = [

        analysis.get(
            "direction",
            "WAIT"
        ),

        trend(a5),

        trend(a15),

        rsi_zone(
            a5.get(
                "rsi"
            )
        ),

        rsi_zone(
            a15.get(
                "rsi"
            )
        ),

        (
            "BOOK+"
            if safe_float(
                a5.get(
                    "imbalance"
                )
            ) >= 0.10

            else

            "BOOK-"
            if safe_float(
                a5.get(
                    "imbalance"
                )
            ) <= -0.10

            else

            "BOOK0"
        ),

        (
            "MACD+"
            if safe_float(
                a5.get(
                    "macd"
                )
            ) > 0

            else

            "MACD-"
        ),

        (
            "VOL+"
            if safe_float(
                a5.get(
                    "volume_ratio"
                )
            ) >= 1.10

            else

            "VOL0"
        ),
    ]

    raw = "|".join(
        parts
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()[:20]


def get_learning_data():

    data = load_json(
        LEARNING_FILE,
        {}
    )

    if not isinstance(
        data,
        dict
    ):

        data = {}

    data.setdefault(
        "patterns",
        {}
    )

    data.setdefault(
        "stats",
        {}
    )

    defaults = {

        "total": 0,
        "tp1": 0,
        "tp2": 0,
        "sl": 0,
        "ambiguous": 0,
        "unknown": 0,
    }

    for key in defaults:

        data["stats"].setdefault(
            key,
            defaults[key]
        )

    return data


def historical_pattern_score(
    pattern_id
):

    learning = (
        get_learning_data()
    )

    pattern = (
        learning[
            "patterns"
        ].get(
            pattern_id
        )
    )

    if not isinstance(
        pattern,
        dict
    ):

        return None

    total = sum(

        int(
            pattern.get(
                key,
                0
            )
        )

        for key in [
            "tp1",
            "tp2",
            "sl",
            "ambiguous",
        ]
    )

    if total < 3:
        return None

    success = (

        int(
            pattern.get(
                "tp1",
                0
            )
        )

        +

        int(
            pattern.get(
                "tp2",
                0
            )
        )
    )

    return round(
        success
        / total
        * 100.0,
        2
    )


def update_learning(
    signal,
    result
):

    learning = (
        get_learning_data()
    )

    pattern_id = signal.get(
        "pattern_id"
    )

    if not pattern_id:
        return

    pattern = (

        learning[
            "patterns"
        ].setdefault(

            pattern_id,

            {
                "total": 0,
                "tp1": 0,
                "tp2": 0,
                "sl": 0,
                "ambiguous": 0,
                "unknown": 0,
            }
        )
    )

    pattern[
        "total"
    ] = int(
        pattern.get(
            "total",
            0
        )
    ) + 1

    result_key = (

        result
        if result in [
            "tp1",
            "tp2",
            "sl",
            "ambiguous",
            "unknown",
        ]

        else

        "unknown"
    )

    pattern[
        result_key
    ] = int(
        pattern.get(
            result_key,
            0
        )
    ) + 1

    learning[
        "stats"
    ][
        "total"
    ] = int(
        learning[
            "stats"
        ].get(
            "total",
            0
        )
    ) + 1

    learning[
        "stats"
    ][
        result_key
    ] = int(
        learning[
            "stats"
        ].get(
            result_key,
            0
        )
    ) + 1

    save_json(
        LEARNING_FILE,
        learning
    )


# ============================================================
# Signal Storage
# ============================================================

def get_signals():

    data = load_json(
        SIGNALS_FILE,
        []
    )

    if not isinstance(
        data,
        list
    ):

        data = []

    return data


def save_signals(
    signals
):

    signals = signals[
        -MAX_STORED_SIGNALS:
    ]

    save_json(
        SIGNALS_FILE,
        signals
    )


def signal_fingerprint(
    symbol,
    direction,
    pattern_id
):

    raw = (

        f"{normalize_symbol(symbol)}"
        f"|{direction}"
        f"|{pattern_id}"
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()[:24]


def is_duplicate_signal(
    signals,
    fingerprint
):

    now = time.time()

    limit = (
        DEDUP_MINUTES
        * 60
    )

    for signal in reversed(
        signals
    ):

        if signal.get(
            "fingerprint"
        ) != fingerprint:

            continue

        timestamp = safe_float(
            signal.get(
                "timestamp"
            )
        )

        if (
            timestamp > 0
            and now - timestamp
            < limit
        ):

            return True

    return False


# ============================================================
# Signal Creation
# ============================================================

def calculate_targets(
    direction,
    price,
    atr_value
):

    price = safe_float(
        price
    )

    atr_value = safe_float(
        atr_value
    )

    if (
        price <= 0
        or atr_value <= 0
    ):

        return {

            "entry":
                price,

            "tp1":
                price,

            "tp2":
                price,

            "sl":
                price,
        }

    if direction == "BUY":

        tp1 = (
            price
            + atr_value
            * TP1_ATR
        )

        tp2 = (
            price
            + atr_value
            * TP2_ATR
        )

        sl = (
            price
            - atr_value
            * SL_ATR
        )

    elif direction == "SELL":

        tp1 = (
            price
            - atr_value
            * TP1_ATR
        )

        tp2 = (
            price
            - atr_value
            * TP2_ATR
        )

        sl = (
            price
            + atr_value
            * SL_ATR
        )

    else:

        tp1 = price
        tp2 = price
        sl = price

    return {

        "entry":
            price,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "sl":
            sl,
    }


def build_signal(
    analysis
):

    if not analysis:
        return None

    direction = analysis.get(
        "direction"
    )

    if direction not in [
        "BUY",
        "SELL"
    ]:

        return None

    score = safe_float(
        analysis.get(
            "score"
        )
    )

    if score < MIN_SIGNAL_SCORE:
        return None

    pattern_id = (
        create_pattern_fingerprint(
            analysis
        )
    )

    historical_score = (
        historical_pattern_score(
            pattern_id
        )
    )

    confidence = score

    if historical_score is not None:

        confidence = (
            score * 0.70
            +
            historical_score * 0.30
        )

    price = safe_float(
        analysis.get(
            "price"
        )
    )

    atr_value = safe_float(
        analysis.get(
            "atr"
        )
    )

    targets = calculate_targets(
        direction,
        price,
        atr_value
    )

    signal = {

        "id":
            hashlib.sha256(
                (
                    f"{analysis.get('symbol')}"
                    f"|{direction}"
                    f"|{time.time_ns()}"
                ).encode(
                    "utf-8"
                )
            ).hexdigest()[:24],

        "fingerprint":
            signal_fingerprint(
                analysis.get(
                    "symbol",
                    ""
                ),
                direction,
                pattern_id
            ),

        "pattern_id":
            pattern_id,

        "symbol":
            normalize_symbol(
                analysis.get(
                    "symbol",
                    ""
                )
            ),

        "direction":
            direction,

        "score":
            round(
                score,
                2
            ),

        "historical_score":
            historical_score,

        "confidence":
            round(
                confidence,
                2
            ),

        "entry":
            targets["entry"],

        "tp1":
            targets["tp1"],

        "tp2":
            targets["tp2"],

        "sl":
            targets["sl"],

        "suggested_leverage":
            analysis.get(
                "suggested_leverage",
                1
            ),

        "volatility_pct":
            analysis.get(
                "volatility_pct",
                0
            ),

        "created_at":
            now_iso(),

        "timestamp":
            time.time(),

        "status":
            "OPEN",

        "result":
            "unknown",

        "analysis":
            analysis,
    }

    return signal# ============================================================
# Signal Result Checker
# ============================================================

def check_signal_result(
    signal
):

    if not isinstance(
        signal,
        dict
    ):

        return "unknown"

    if signal.get(
        "status"
    ) != "OPEN":

        return signal.get(
            "result",
            "unknown"
        )

    symbol = normalize_symbol(
        signal.get(
            "symbol",
            ""
        )
    )

    direction = signal.get(
        "direction"
    )

    if direction not in [
        "BUY",
        "SELL"
    ]:

        return "unknown"

    trades = get_live_trades(
        symbol
    )

    if not trades:
        return "unknown"

    entry = safe_float(
        signal.get(
            "entry"
        )
    )

    tp1 = safe_float(
        signal.get(
            "tp1"
        )
    )

    tp2 = safe_float(
        signal.get(
            "tp2"
        )
    )

    sl = safe_float(
        signal.get(
            "sl"
        )
    )

    if (
        entry <= 0
        or tp1 <= 0
        or tp2 <= 0
        or sl <= 0
    ):

        return "unknown"

    for trade in trades:

        price = safe_float(
            trade.get(
                "price"
            )
        )

        if price <= 0:
            continue

        if direction == "BUY":

            # حد ضرر قبل از تارگت
            if price <= sl:
                return "sl"

            if price >= tp2:
                return "tp2"

            if price >= tp1:
                return "tp1"

        elif direction == "SELL":

            if price >= sl:
                return "sl"

            if price <= tp2:
                return "tp2"

            if price <= tp1:
                return "tp1"

    return "unknown"


def update_open_signals():

    signals = get_signals()

    changed = False

    for signal in signals:

        if signal.get(
            "status"
        ) != "OPEN":

            continue

        result = check_signal_result(
            signal
        )

        if result == "unknown":
            continue

        signal[
            "result"
        ] = result

        signal[
            "status"
        ] = "CLOSED"

        signal[
            "closed_at"
        ] = now_iso()

        update_learning(
            signal,
            result
        )

        changed = True

        print(
            "SIGNAL RESULT:",
            signal.get(
                "symbol"
            ),
            signal.get(
                "direction"
            ),
            "=>",
            result
        )

    if changed:

        save_signals(
            signals
        )

    return signals


# ============================================================
# Statistics
# ============================================================

def calculate_statistics(
    signals
):

    stats = {

        "total":
            0,

        "tp1":
            0,

        "tp2":
            0,

        "sl":
            0,

        "ambiguous":
            0,

        "unknown":
            0,

        "open":
            0,
    }

    for signal in signals:

        stats[
            "total"
        ] += 1

        status = signal.get(
            "status"
        )

        result = signal.get(
            "result",
            "unknown"
        )

        if status == "OPEN":

            stats[
                "open"
            ] += 1

        if result in [
            "tp1",
            "tp2",
            "sl",
            "ambiguous",
            "unknown",
        ]:

            stats[
                result
            ] += 1

    completed = (

        stats["tp1"]
        +
        stats["tp2"]
        +
        stats["sl"]
    )

    successes = (

        stats["tp1"]
        +
        stats["tp2"]
    )

    if completed > 0:

        stats[
            "success_rate"
        ] = round(

            successes
            / completed
            * 100.0,

            2
        )

    else:

        stats[
            "success_rate"
        ] = 0.0

    return stats


# ============================================================
# Telegram Formatting
# ============================================================

def direction_fa(
    direction
):

    if direction == "BUY":
        return "🟢 خرید / لانگ"

    if direction == "SELL":
        return "🔴 فروش / شورت"

    return "⚪ انتظار"


def format_signal(
    signal
):

    direction = signal.get(
        "direction",
        "WAIT"
    )

    symbol = signal.get(
        "symbol",
        "UNKNOWN"
    )

    score = safe_float(
        signal.get(
            "score"
        )
    )

    confidence = safe_float(
        signal.get(
            "confidence"
        )
    )

    entry = safe_float(
        signal.get(
            "entry"
        )
    )

    tp1 = safe_float(
        signal.get(
            "tp1"
        )
    )

    tp2 = safe_float(
        signal.get(
            "tp2"
        )
    )

    sl = safe_float(
        signal.get(
            "sl"
        )
    )

    leverage = signal.get(
        "suggested_leverage",
        1
    )

    historical = (
        signal.get(
            "historical_score"
        )
    )

    if historical is None:

        historical_text = (
            "هنوز داده کافی ندارد"
        )

    else:

        historical_text = (
            f"{historical:.1f}%"
        )

    text = (

        "🚨 سیگنال جدید Tabdeal\n\n"

        f"🪙 نماد: {symbol}\n"

        f"📌 جهت: "
        f"{direction_fa(direction)}\n\n"

        f"📊 امتیاز تحلیل: "
        f"{score:.1f}%\n"

        f"🧠 اعتماد ترکیبی: "
        f"{confidence:.1f}%\n"

        f"📚 سابقه الگو: "
        f"{historical_text}\n\n"

        f"💰 ورود: {entry:.8f}\n"

        f"🎯 TP1: {tp1:.8f}\n"

        f"🎯 TP2: {tp2:.8f}\n"

        f"🛑 SL: {sl:.8f}\n\n"

        f"⚙️ اهرم تحلیلی پیشنهادی: "
        f"{leverage}x\n\n"

        "⏱ تحلیل: 5m + 15m\n"

        "🤖 حالت: فقط تحلیل\n"

        "⚠️ این پیام دستور معامله خودکار نیست."
    )

    return text


def format_result(
    signal
):

    symbol = signal.get(
        "symbol",
        "UNKNOWN"
    )

    direction = signal.get(
        "direction",
        "WAIT"
    )

    result = signal.get(
        "result",
        "unknown"
    )

    result_text = {

        "tp1":
            "🎯 TP1 موفق شد",

        "tp2":
            "🏆 TP2 موفق شد",

        "sl":
            "🛑 حد ضرر فعال شد",

        "ambiguous":
            "⚠️ نتیجه نامشخص",

        "unknown":
            "❔ نتیجه هنوز مشخص نیست",
    }.get(
        result,
        "❔ نتیجه نامشخص"
    )

    return (

        "📊 نتیجه سیگنال Tabdeal\n\n"

        f"🪙 {symbol}\n"

        f"📌 {direction_fa(direction)}\n\n"

        f"{result_text}\n\n"

        f"💰 ورود: "
        f"{safe_float(signal.get('entry')):.8f}\n"

        f"🎯 TP1: "
        f"{safe_float(signal.get('tp1')):.8f}\n"

        f"🎯 TP2: "
        f"{safe_float(signal.get('tp2')):.8f}\n"

        f"🛑 SL: "
        f"{safe_float(signal.get('sl')):.8f}"
    )


def format_statistics(
    stats
):

    return (

        "📈 آمار عملکرد ربات\n\n"

        f"📊 کل سیگنال‌ها: "
        f"{stats.get('total', 0)}\n"

        f"🟢 TP1: "
        f"{stats.get('tp1', 0)}\n"

        f"🏆 TP2: "
        f"{stats.get('tp2', 0)}\n"

        f"🔴 شکست / SL: "
        f"{stats.get('sl', 0)}\n"

        f"⚪ باز: "
        f"{stats.get('open', 0)}\n\n"

        f"🎯 درصد موفقیت فعلی: "
        f"{stats.get('success_rate', 0):.2f}%\n\n"

        "⏱ تایم‌فریم: 5m + 15m\n"

        "🤖 حالت: تحلیل فقط"
            )# ============================================================
# Main Scan
# ============================================================

def scan_market():

    print()
    print("=" * 60)
    print("شروع اسکن Futures تبدیل")
    print("=" * 60)

    # اطمینان از فعال بودن WebSocketها
    start_websocket_workers()

    # کمی زمان برای دریافت اولین داده‌ها
    print("Waiting for live Tabdeal Futures data...")

    time.sleep(10)

    signals = get_signals()

    # --------------------------------------------------------
    # بررسی سیگنال‌های قبلی
    # --------------------------------------------------------

    print()
    print("=== بررسی نتایج سیگنال‌های قبلی ===")

    signals = update_open_signals()

    # --------------------------------------------------------
    # تحلیل نمادها
    # --------------------------------------------------------

    new_signals = []

    print()
    print("=== شروع تحلیل بازار ===")

    for index, symbol in enumerate(
        SYMBOLS,
        start=1
    ):

        symbol = normalize_symbol(
            symbol
        )

        print()
        print(
            f"[{index}/{len(SYMBOLS)}] "
            f"Analyzing {symbol}"
        )

        try:

            analysis = analyze_symbol(
                symbol
            )

            if analysis is None:

                print(
                    "No sufficient live data."
                )

                continue

            print(
                "Direction:",
                analysis.get(
                    "direction"
                )
            )

            print(
                "Score:",
                analysis.get(
                    "score"
                )
            )

            print(
                "5m candles:",
                analysis.get(
                    "candles_5m"
                )
            )

            print(
                "15m candles:",
                analysis.get(
                    "candles_15m"
                )
            )

            print(
                "Live trades:",
                analysis.get(
                    "trade_count"
                )
            )

            signal = build_signal(
                analysis
            )

            if signal is None:

                print(
                    "No signal."
                )

                continue

            fingerprint = signal.get(
                "fingerprint"
            )

            if is_duplicate_signal(
                signals,
                fingerprint
            ):

                print(
                    "Duplicate signal ignored."
                )

                continue

            signals.append(
                signal
            )

            new_signals.append(
                signal
            )

            print(
                "NEW SIGNAL:",
                signal.get(
                    "symbol"
                ),
                signal.get(
                    "direction"
                ),
                signal.get(
                    "score"
                )
            )

        except Exception as exc:

            print(
                f"ANALYSIS ERROR [{symbol}]:",
                exc
            )

    # --------------------------------------------------------
    # ذخیره سیگنال‌ها
    # --------------------------------------------------------

    save_signals(
        signals
    )

    # --------------------------------------------------------
    # ارسال سیگنال‌های جدید
    # --------------------------------------------------------

    for signal in new_signals:

        message = format_signal(
            signal
        )

        sent = send_telegram(
            message
        )

        if sent:

            print(
                "Telegram signal sent:",
                signal.get(
                    "symbol"
                )
            )

        else:

            print(
                "Telegram signal failed:",
                signal.get(
                    "symbol"
                )
            )

    # --------------------------------------------------------
    # آمار
    # --------------------------------------------------------

    stats = calculate_statistics(
        signals
    )

    print()
    print(
        "=== SCAN SUMMARY ==="
    )

    print(
        "Symbols:",
        len(SYMBOLS)
    )

    print(
        "New signals:",
        len(new_signals)
    )

    print(
        "Open signals:",
        stats.get(
            "open",
            0
        )
    )

    print(
        "TP1:",
        stats.get(
            "tp1",
            0
        )
    )

    print(
        "TP2:",
        stats.get(
            "tp2",
            0
        )
    )

    print(
        "SL:",
        stats.get(
            "sl",
            0
        )
    )

    print(
        "Success rate:",
        stats.get(
            "success_rate",
            0
        ),
        "%"
    )

    print()
    print(
        "SCAN FINISHED"
    )


# ============================================================
# Program Entry
# ============================================================

if __name__ == "__main__":

    try:

        print(
            "🤖 Tabdeal Futures Signal Bot"
        )

        print(
            "Mode: ANALYSIS ONLY"
        )

        print(
            "Symbols:",
            ", ".join(
                SYMBOLS
            )
        )

        print(
            "Timeframes: 5m + 15m"
        )

        scan_market()

    except KeyboardInterrupt:

        print(
            "Bot stopped."
        )

    except Exception as exc:

        print(
            "FATAL ERROR:",
            exc
        )

        raise
