import os
import json
import time
import hashlib
import requests
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXCHANGE_INFO_URL = "https://api1.tabdeal.org/fapi/v1/exchangeInfo"
DEPTH_URL = "https://api1.tabdeal.org/fapi/v1/depth"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "SOLUSDT"]

TIMEFRAMES = {
    "5m": 5,
    "15m": 15,
}

SNAPSHOT_FILE = "tabdeal_market_history.json"
LEARNING_FILE = "tabdeal_learning.json"
SIGNALS_FILE = "tabdeal_signals.json"

MIN_SIGNAL_SCORE = 70
DEDUP_MINUTES = 60

TP1_ATR = 1.0
TP2_ATR = 2.0
SL_ATR = 1.2

MAX_STORED_SIGNALS = 3000
MAX_SNAPSHOTS_PER_SYMBOL = 2500

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Tabdeal-Analysis-Bot/2.0",
    "Accept": "application/json",
})


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
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:

        print(
            f"JSON LOAD ERROR [{filename}]:",
            e
        )

        return default


def save_json(filename, data):

    try:

        with open(filename, "w", encoding="utf-8") as f:

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

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

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

        if response.ok:
            return True

        print(
            "Telegram HTTP ERROR:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:

        print(
            "Telegram error:",
            e
        )

    return False


# ============================================================
# API
# ============================================================

def api_get(url, params=None):

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

    except Exception as e:

        print(
            "API ERROR:",
            e
        )

    return None


def normalize_symbol(symbol):

    return (
        symbol
        .replace("_", "")
        .replace("-", "")
        .upper()
    )


def get_exchange_info():

    return api_get(
        EXCHANGE_INFO_URL
    )


# ============================================================
# Order Book / Market Data
# ============================================================

def get_current_market(symbol):

    data = api_get(

        DEPTH_URL,

        {
            "symbol": normalize_symbol(symbol),
            "limit": 20,
        },
    )

    if not isinstance(data, dict):
        return None

    bids = data.get("bids") or []
    asks = data.get("asks") or []

    if not bids or not asks:
        return None

    try:

        best_bid = safe_float(
            bids[0][0]
        )

        best_ask = safe_float(
            asks[0][0]
        )

        bid_qty = sum(
            safe_float(x[1])
            for x in bids[:20]
        )

        ask_qty = sum(
            safe_float(x[1])
            for x in asks[:20]
        )

    except Exception:

        return None

    if best_bid <= 0 or best_ask <= 0:
        return None

    price = (
        best_bid +
        best_ask
    ) / 2.0

    spread_pct = (

        (best_ask - best_bid)
        / price
        * 100.0

        if price
        else 0.0
    )

    total_qty = (
        bid_qty +
        ask_qty
    )

    imbalance = (

        (bid_qty - ask_qty)
        / total_qty

        if total_qty > 0
        else 0.0
    )

    return {

        "timestamp":
            time.time(),

        "iso":
            now_iso(),

        "price":
            price,

        "best_bid":
            best_bid,

        "best_ask":
            best_ask,

        "spread_pct":
            spread_pct,

        "bid_qty":
            bid_qty,

        "ask_qty":
            ask_qty,

        "imbalance":
            imbalance,
    }


# ============================================================
# History
# ============================================================

def candle_bucket(
    timestamp,
    minutes
):

    seconds = minutes * 60

    return int(
        timestamp // seconds
    ) * seconds


def load_market_history():

    data = load_json(
        SNAPSHOT_FILE,
        {}
    )

    if not isinstance(data, dict):

        data = {}

    for symbol in SYMBOLS:

        if not isinstance(
            data.get(symbol),
            list
        ):

            data[symbol] = []

    return data


def save_market_history(history):

    for symbol in SYMBOLS:

        history[symbol] = (
            history.get(symbol, [])
            [-MAX_SNAPSHOTS_PER_SYMBOL:]
        )

    save_json(
        SNAPSHOT_FILE,
        history
    )


def add_snapshot(
    history,
    symbol,
    market
):

    history.setdefault(
        symbol,
        []
    ).append(market)

    history[symbol] = (
        history[symbol]
        [-MAX_SNAPSHOTS_PER_SYMBOL:]
    )


def build_candles(
    snapshots,
    minutes
):

    buckets = {}

    for s in snapshots:

        ts = safe_float(
            s.get("timestamp")
        )

        price = safe_float(
            s.get("price")
        )

        if ts <= 0 or price <= 0:
            continue

        bucket = candle_bucket(
            ts,
            minutes
        )

        buckets.setdefault(
            bucket,
            []
        ).append(s)

    candles = []

    for bucket in sorted(buckets):

        rows = buckets[bucket]

        prices = [
            safe_float(
                x.get("price")
            )
            for x in rows
        ]

        prices = [
            p for p in prices
            if p > 0
        ]

        if not prices:
            continue

        candles.append({

            "open_time":
                int(bucket * 1000),

            "open":
                prices[0],

            "high":
                max(prices),

            "low":
                min(prices),

            "close":
                prices[-1],

            "volume":
                sum(
                    abs(
                        safe_float(
                            x.get(
                                "imbalance"
                            )
                        )
                    )
                    for x in rows
                ),

            "samples":
                len(rows),
        })

    return candles


# ============================================================
# Indicators
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
        2 / (period + 1)
    )

    result = (
        sum(values[:period])
        / period
    )

    for price in values[period:]:

        result = (
            (price - result)
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
            - values[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
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
        avg_gain /
        avg_loss
    )

    return (
        100 -
        (100 / (1 + rs))
    )def atr(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(

            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            ),
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(
        trs[-period:]
    ) / period


def orderbook_imbalance(
    candles
):

    if not candles:
        return 0.0

    return safe_float(
        candles[-1].get(
            "book_imbalance"
        ),
        0.0
    )


def attach_latest_imbalance(
    candles,
    snapshots,
    minutes
):

    if not candles or not snapshots:
        return candles

    latest = snapshots[-1]

    value = safe_float(
        latest.get("imbalance")
    )

    bucket = candle_bucket(
        safe_float(
            latest.get("timestamp")
        ),
        minutes
    )

    for candle in reversed(candles):

        if candle["open_time"] == int(
            bucket * 1000
        ):

            candle[
                "book_imbalance"
            ] = value

            break

    return candles


# ============================================================
# Timeframe Analysis
# ============================================================

def analyze_timeframe(
    candles
):

    if len(candles) < 55:
        return None

    closes = [
        c["close"]
        for c in candles
    ]

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

    if any(
        v is None
        for v in [
            ema9,
            ema21,
            ema50,
            rsi_value,
            atr_value,
        ]
    ):
        return None

    ema12 = ema(
        closes,
        12
    )

    ema26 = ema(
        closes,
        26
    )

    if (
        ema12 is not None
        and ema26 is not None
    ):

        macd_value = (
            ema12 - ema26
        )

    else:

        macd_value = 0.0

    imbalance = (
        orderbook_imbalance(
            candles
        )
    )

    atr_pct = (

        atr_value
        / current_price
        * 100

        if current_price
        else 0.0
    )

    bullish = (

        ema9 > ema21
        > ema50

        and current_price
        > ema21
    )

    bearish = (

        ema9 < ema21
        < ema50

        and current_price
        < ema21
    )

    # --------------------------------------------------------
    # BUY SCORE
    # --------------------------------------------------------

    buy_score = 0

    if current_price > ema9:
        buy_score += 15

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

    if imbalance >= 0.15:
        buy_score += 10

    if bullish:
        buy_score += 15

    # --------------------------------------------------------
    # SELL SCORE
    # --------------------------------------------------------

    sell_score = 0

    if current_price < ema9:
        sell_score += 15

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

    if imbalance <= -0.15:
        sell_score += 10

    if bearish:
        sell_score += 15

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

        "buy_score":
            buy_score,

        "sell_score":
            sell_score,

        "bullish":
            bullish,

        "bearish":
            bearish,
    }


# ============================================================
# Multi Timeframe
# ============================================================

def analyze_symbol(
    symbol,
    history
):

    snapshots = history.get(
        symbol,
        []
    )

    candles_5m = build_candles(
        snapshots,
        5
    )

    candles_15m = build_candles(
        snapshots,
        15
    )

    candles_5m = (
        attach_latest_imbalance(
            candles_5m,
            snapshots,
            5
        )
    )

    candles_15m = (
        attach_latest_imbalance(
            candles_15m,
            snapshots,
            15
        )
    )

    a5 = analyze_timeframe(
        candles_5m
    )

    a15 = analyze_timeframe(
        candles_15m
    )

    if not a5 or not a15:
        return None

    buy_score = (

        a5["buy_score"] * 0.45

        +

        a15["buy_score"] * 0.55
    )

    sell_score = (

        a5["sell_score"] * 0.45

        +

        a15["sell_score"] * 0.55
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
        a5["atr_pct"],
        a15["atr_pct"]
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

        "price":
            a5["price"],

        "atr":
            a5["atr"],

        "volatility_pct":
            round(
                volatility,
                3
            ),

        "suggested_leverage":
            leverage,

        "5m":
            a5,

        "15m":
            a15,

        "candles_5m":
            candles_5m,

        "candles_15m":
            candles_15m,
    }


# ============================================================
# Analytical Leverage
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


# ============================================================
# Pattern Fingerprint
# ============================================================

def create_pattern_fingerprint(
    analysis
):

    a5 = analysis["5m"]
    a15 = analysis["15m"]

    def rsi_bucket(v):

        if v < 35:
            return "LOW"

        if v < 45:
            return "WEAK"

        if v < 55:
            return "MID"

        if v < 65:
            return "STRONG"

        return "HIGH"

    parts = [

        analysis["direction"],

        (
            "5B"
            if a5["bullish"]
            else
            "5S"
            if a5["bearish"]
            else
            "5M"
        ),

        (
            "15B"
            if a15["bullish"]
            else
            "15S"
            if a15["bearish"]
            else
            "15M"
        ),

        rsi_bucket(
            a5["rsi"]
        ),

        rsi_bucket(
            a15["rsi"]
        ),

        (
            "BOOK+"
            if a5["imbalance"] > 0.15
            else
            "BOOK-"
            if a5["imbalance"] < -0.15
            else
            "BOOK0"
        ),

        (
            "MACD+"
            if a5["macd"] > 0
            else
            "MACD-"
        ),
    ]

    return hashlib.sha256(
        "|".join(parts).encode()
    ).hexdigest()[:20]


# ============================================================
# Learning
# ============================================================

def get_learning_data():

    data = load_json(

        LEARNING_FILE,

        {
            "patterns": {},

            "stats": {
                "total": 0,
                "tp1": 0,
                "tp2": 0,
                "sl": 0,
                "ambiguous": 0,
                "unknown": 0,
            },
        }
    )

    data.setdefault(
        "patterns",
        {}
    )

    data.setdefault(
        "stats",
        {
            "total": 0,
            "tp1": 0,
            "tp2": 0,
            "sl": 0,
            "ambiguous": 0,
            "unknown": 0,
        }
    )

    return data


def historical_pattern_score(
    pattern_id
):

    history = (
        get_learning_data()
        ["patterns"]
        .get(
            pattern_id,
            {}
        )
    )

    total = sum(
        history.get(k, 0)
        for k in [
            "tp1",
            "tp2",
            "sl",
            "ambiguous"
        ]
    )

    if total < 3:
        return None

    return round(

        (
            history.get(
                "tp1",
                0
            )

            +

            history.get(
                "tp2",
                0
            )
        )

        / total
        * 100,

        2
    )


def update_learning(
    signal,
    result
):

    learning = (
        get_learning_data()
    )

    pid = signal[
        "pattern_id"
    ]

    pattern = (
        learning["patterns"]
        .setdefault(

            pid,

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

    pattern["total"] += 1

    pattern[
        result
        if result in pattern
        else "unknown"
    ] += 1

    learning["stats"][
        "total"
    ] += 1

    learning["stats"][
        result
        if result in learning["stats"]
        else "unknown"
    ] += 1

    save_json(
        LEARNING_FILE,
        learning
    )


def load_signals():

    data = load_json(
        SIGNALS_FILE,
        []
    )

    return (
        data
        if isinstance(
            data,
            list
        )
        else []
    )


def save_signals(
    signals
):

    save_json(
        SIGNALS_FILE,
        signals[
            -MAX_STORED_SIGNALS:
        ]
    )


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
            "status"
        ) not in [
            "OPEN",
            "TP1"
        ]:
            continue

        if (
            signal.get(
                "symbol"
            ) != symbol
        ):
            continue

        if (
            signal.get(
                "direction"
            ) != direction
        ):
            continue

        if (
            signal.get(
                "pattern_id"
            ) != pattern_id
        ):
            continue

        age = (

            now
            - safe_float(
                signal.get(
                    "created_timestamp"
                )
            )

        ) / 60

        if age < DEDUP_MINUTES:
            return True

    return False


# ============================================================
# Signal
# ============================================================

def create_signal(
    analysis
):

    if (
        analysis["direction"]
        == "WAIT"
        or
        analysis["atr"] <= 0
    ):
        return None

    price = analysis[
        "price"
    ]

    atr_value = analysis[
        "atr"
    ]

    direction = analysis[
        "direction"
    ]

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

    pattern_id = (
        create_pattern_fingerprint(
            analysis
        )
    )

    return {

        "id":
            hashlib.sha256(

                (
                    analysis["symbol"]
                    +
                    direction
                    +
                    str(time.time())
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

        "entry":
            price,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "stop_loss":
            stop_loss,

        "pattern_id":
            pattern_id,

        "historical_pattern_score":
            historical_pattern_score(
                pattern_id
            ),

        "suggested_leverage":
            analysis[
                "suggested_leverage"
            ],

        "volatility_pct":
            analysis[
                "volatility_pct"
            ],

        "status":
            "OPEN",

        "result":
            None,
            }def signal_to_message(
    signal
):

    history = signal[
        "historical_pattern_score"
    ]

    history_text = (

        f"{history}%"

        if history is not None

        else

        "هنوز داده کافی ندارد"
    )

    return (

        "🚨 سیگنال تحلیل Tabdeal\n\n"

        f"🪙 {signal['symbol']}\n"

        f"📊 جهت: "
        f"{signal['direction']}\n"

        f"🎯 امتیاز: "
        f"{signal['score']}%\n"

        f"📈 نوسان: "
        f"{signal['volatility_pct']}%\n"

        f"⚡ اهرم پیشنهادی تحلیلی: "
        f"{signal['suggested_leverage']}x\n\n"

        f"💰 ورود: "
        f"{signal['entry']:.8g}\n"

        f"🎯 TP1: "
        f"{signal['tp1']:.8g}\n"

        f"🎯 TP2: "
        f"{signal['tp2']:.8g}\n"

        f"🛑 SL: "
        f"{signal['stop_loss']:.8g}\n\n"

        f"🧠 سابقه الگو: "
        f"{history_text}\n"

        "⚠️ فقط تحلیل است؛ "
        "معامله خودکار فعال نیست."
    )


def get_result_price(
    symbol
):

    market = get_current_market(
        symbol
    )

    if market:

        return market[
            "price"
        ]

    return None


def evaluate_signal(
    signal
):

    price = get_result_price(
        signal["symbol"]
    )

    if price is None:
        return None

    if signal[
        "direction"
    ] == "BUY":

        if price <= signal[
            "stop_loss"
        ]:

            return "sl"

        if price >= signal[
            "tp2"
        ]:

            return "tp2"

        if price >= signal[
            "tp1"
        ]:

            return "tp1"

    else:

        if price >= signal[
            "stop_loss"
        ]:

            return "sl"

        if price <= signal[
            "tp2"
        ]:

            return "tp2"

        if price <= signal[
            "tp1"
        ]:

            return "tp1"

    return None


def update_signal_status(
    signal,
    result
):

    old = signal.get(
        "result"
    )

    if result == "tp1":

        signal[
            "status"
        ] = "TP1"

    elif result in [
        "tp2",
        "sl",
        "ambiguous"
    ]:

        signal[
            "status"
        ] = "CLOSED"

    signal[
        "result"
    ] = result

    return old != result


def is_final_result(
    result
):

    return result in [
        "tp2",
        "sl",
        "ambiguous"
    ]


def statistics():

    signals = load_signals()

    counts = {

        "tp1": 0,
        "tp2": 0,
        "sl": 0,
        "ambiguous": 0,
        "open": 0,
    }

    for s in signals:

        result = s.get(
            "result"
        )

        if result in counts:

            counts[
                result
            ] += 1

        elif s.get(
            "status"
        ) in [
            "OPEN",
            "TP1"
        ]:

            counts[
                "open"
            ] += 1

    completed = (

        counts["tp2"]

        +

        counts["sl"]

        +

        counts["ambiguous"]
    )

    accuracy = (

        round(
            counts["tp2"]
            / completed
            * 100,
            2
        )

        if completed

        else

        None
    )

    return {

        "total_completed":
            completed,

        "tp1":
            counts["tp1"],

        "tp2":
            counts["tp2"],

        "sl":
            counts["sl"],

        "ambiguous":
            counts["ambiguous"],

        "open":
            counts["open"],

        "accuracy":
            accuracy,
    }


# ============================================================
# Previous Signals
# ============================================================

def process_previous_signals(
    signals
):

    for signal in signals:

        if signal.get(
            "status"
        ) not in [
            "OPEN",
            "TP1"
        ]:

            continue

        try:

            result = evaluate_signal(
                signal
            )

            if result and update_signal_status(
                signal,
                result
            ):

                if is_final_result(
                    result
                ):

                    update_learning(
                        signal,
                        result
                    )

                label = {

                    "tp1":
                        "🎯 TP1",

                    "tp2":
                        "✅ TP2 / نتیجه نهایی",

                    "sl":
                        "🛑 SL / نتیجه نهایی",

                    "ambiguous":
                        "⚠️ نامشخص",

                }.get(
                    result,
                    result.upper()
                )

                send_telegram(

                    "📌 نتیجه سیگنال Tabdeal\n\n"

                    f"🪙 "
                    f"{signal['symbol']}\n"

                    f"📊 "
                    f"{signal['direction']}\n"

                    f"نتیجه: {label}"
                )

        except Exception as e:

            print(
                "SIGNAL RESULT ERROR:",
                signal.get("symbol"),
                e
            )


# ============================================================
# Main Scanner
# ============================================================

def scan_market():

    print(
        "=" * 60
    )

    print(
        "TABDEAL 5-COIN FUTURES ANALYSIS BOT"
    )

    print(
        "DATA: TABDEAL FUTURES ORDER BOOK"
    )

    print(
        "=" * 60
    )

    info = get_exchange_info()

    if info is None:

        send_telegram(
            "❌ API اطلاعات بازار "
            "فیوچرز تبدیل در دسترس نیست."
        )

        return

    history = load_market_history()

    signals = load_signals()

    process_previous_signals(
        signals
    )

    new_signals = []

    enough = 0

    for symbol in SYMBOLS:

        try:

            market = get_current_market(
                symbol
            )

            if not market:

                print(
                    "NO MARKET DATA:",
                    symbol
                )

                continue

            add_snapshot(
                history,
                symbol,
                market
            )

            save_market_history(
                history
            )

            analysis = analyze_symbol(
                symbol,
                history
            )

            if not analysis:

                print(
                    symbol,
                    "داده تاریخی کافی ندارد."
                )

                continue

            enough += 1

            print(

                symbol,

                "5m",
                analysis["5m"][
                    "buy_score"
                ],
                "/",
                analysis["5m"][
                    "sell_score"
                ],

                "15m",
                analysis["15m"][
                    "buy_score"
                ],
                "/",
                analysis["15m"][
                    "sell_score"
                ],

                "FINAL",
                analysis[
                    "direction"
                ],

                analysis[
                    "score"
                ],

                "LEV",
                analysis[
                    "suggested_leverage"
                ]
            )

            if (
                analysis["direction"]
                == "WAIT"
            ):

                continue

            signal = create_signal(
                analysis
            )

            if not signal:
                continue

            if is_duplicate_signal(

                signals,

                symbol,

                signal[
                    "direction"
                ],

                signal[
                    "pattern_id"
                ]

            ):

                print(
                    "DUPLICATE:",
                    symbol
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

            time.sleep(
                0.2
            )

        except Exception as e:

            print(
                "ANALYSIS ERROR:",
                symbol,
                e
            )

    save_market_history(
        history
    )

    save_signals(
        signals
    )

    stats = statistics()

    print(
        "MARKET SCAN FINISHED"
    )

    print(
        "Symbols:",
        len(SYMBOLS)
    )

    print(
        "Analyzable now:",
        enough
    )

    print(
        "New signals:",
        len(new_signals)
    )

    print(
        "Accuracy:",
        stats["accuracy"]
    )

    if enough == 0:

        send_telegram(

            "⏳ ربات Tabdeal اجرا شد، "
            "اما هنوز داده تاریخی کافی "
            "برای تحلیل 5m/15m ندارد.\n\n"

            "داده‌های واقعی تبدیل ذخیره "
            "می‌شوند و با اجرای‌های بعدی، "
            "تاریخچه ساخته می‌شود."
        )

    elif stats[
        "total_completed"
    ] > 0:

        acc = (

            f"{stats['accuracy']}%"

            if stats["accuracy"]
            is not None

            else

            "نداریم"
        )

        send_telegram(

            "📊 گزارش یادگیری Tabdeal\n\n"

            f"🪙 ارزهای ثابت: "
            f"{len(SYMBOLS)}\n"

            f"🆕 سیگنال جدید: "
            f"{len(new_signals)}\n"

            f"🎯 TP1: "
            f"{stats['tp1']}\n"

            f"✅ TP2: "
            f"{stats['tp2']}\n"

            f"🛑 SL: "
            f"{stats['sl']}\n"

            f"⚠️ نامشخص: "
            f"{stats['ambiguous']}\n"

            f"📈 دقت ثبت‌شده: "
            f"{acc}\n"

            "⚠️ اهرم‌های نمایش‌داده‌شده "
            "پیشنهادی تحلیلی هستند."
        )


# ============================================================
# Entry Point
# ============================================================

def main():

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

            "❌ خطای اصلی ربات Tabdeal\n\n"
            +
            str(e)[:1000]
        )

        raise


if __name__ == "__main__":

    main()
