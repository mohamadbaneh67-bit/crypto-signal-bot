import os
import json
import time
import hashlib
import requests
from datetime import datetime, timezone

# ============================================================
# TABDEAL PROFESSIONAL MARGIN / FUTURES ANALYSIS BOT
# فقط تحلیل - بدون باز کردن معامله
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ------------------------------------------------------------
# Tabdeal API
# ------------------------------------------------------------

EXCHANGE_INFO_URLS = [
    "https://api1.tabdeal.org/r/fapi/v1/exchangeInfo",
    "https://api1.tabdeal.org/fapi/v1/exchangeInfo",
]

KLINE_URLS = [
    "https://api1.tabdeal.org/r/fapi/v1/klines",
    "https://api1.tabdeal.org/fapi/v1/klines",
]

TICKER_URLS = [
    "https://api1.tabdeal.org/r/fapi/v1/ticker/price",
    "https://api1.tabdeal.org/fapi/v1/ticker/price",
]

# ------------------------------------------------------------
# Files
# ------------------------------------------------------------

RAW_FILE = "tabdeal_margin_exchange_info.json"
LEARNING_FILE = "tabdeal_learning.json"
SIGNALS_FILE = "tabdeal_signals.json"

# ------------------------------------------------------------
# Settings
# ------------------------------------------------------------

TIMEFRAMES = {
    "5m": 5,
    "15m": 15,
}

KLINE_LIMIT = 120

# حداقل امتیاز برای تولید سیگنال
MIN_SIGNAL_SCORE = 70

# فاصله زمانی برای جلوگیری از تکرار همان سیگنال
DEDUP_MINUTES = 60

# تارگت‌ها بر اساس ATR
TP1_ATR = 1.0
TP2_ATR = 2.0
SL_ATR = 1.2

# حداکثر تعداد سیگنال‌های ذخیره‌شده
MAX_STORED_SIGNALS = 3000

# ------------------------------------------------------------
# HTTP session
# ------------------------------------------------------------

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Tabdeal-Analysis-Bot/1.0",
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
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"JSON LOAD ERROR [{filename}]:", e)
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
        print(f"JSON SAVE ERROR [{filename}]:", e)
        return False


# ============================================================
# Telegram
# ============================================================

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    try:
        response = SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
            },
            timeout=20,
        )

        if response.ok:
            return True

        print("Telegram HTTP ERROR:", response.status_code)
        print(response.text[:500])

    except Exception as e:
        print("Telegram error:", e)

    return False


# ============================================================
# API Request
# ============================================================

def api_get(urls, params=None):
    last_error = None

    for url in urls:
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
                    last_error = f"JSON error: {e}"
            else:
                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )

        except Exception as e:
            last_error = str(e)

    print("API ERROR:", last_error)
    return None


# ============================================================
# Symbol Discovery
# ============================================================

def collect_symbols(obj, found=None):
    if found is None:
        found = set()

    if isinstance(obj, dict):

        # حالت استاندارد Binance-compatible
        symbol = obj.get("symbol")

        if isinstance(symbol, str):
            symbol = symbol.strip().upper()

            if symbol:
                found.add(symbol)

        for key, value in obj.items():

            key_lower = str(key).lower()

            if key_lower in {
                "symbol",
                "tabdealsymbol",
                "pair",
                "market",
                "market_symbol",
                "instrument",
                "instrumentid",
            }:

                if isinstance(value, str):
                    value = value.strip().upper()

                    if value:
                        found.add(value)

            collect_symbols(value, found)

    elif isinstance(obj, list):

        for item in obj:
            collect_symbols(item, found)

    return found


def get_symbols():
    print("\n=== دریافت نمادهای Tabdeal ===")

    data = api_get(EXCHANGE_INFO_URLS)

    if data is None:
        print("Exchange info unavailable.")
        return []

    symbols = set()

    # اگر ساختار استاندارد باشد
    if isinstance(data, dict):

        raw_symbols = data.get("symbols")

        if isinstance(raw_symbols, list):

            for item in raw_symbols:

                if not isinstance(item, dict):
                    continue

                symbol = item.get("symbol")

                if isinstance(symbol, str):
                    symbols.add(symbol.strip().upper())

    # fallback
    symbols = collect_symbols(data, symbols)

    # حذف موارد غیرمناسب
    cleaned = set()

    for symbol in symbols:

        symbol = symbol.strip().upper()

        if not symbol:
            continue

        # برای جلوگیری از اضافه شدن مقادیر غیرنماد
        if len(symbol) < 5:
            continue

        # فقط نمادهای بازار USDT
        if symbol.endswith("_USDT") or symbol.endswith("USDT"):
            cleaned.add(symbol)

    symbols = sorted(cleaned)

    print("TOTAL SYMBOLS:", len(symbols))

    return symbols


# ============================================================
# Kline
# ============================================================

def normalize_symbol(symbol):
    """
    Tabdeal ممکن است بعضی جاها BTC_USDT
    و بعضی APIها BTCUSDT بخواهند.
    """

    return symbol.replace("_", "").replace("-", "").upper()


def get_klines(symbol, interval, limit=KLINE_LIMIT):

    api_symbol = normalize_symbol(symbol)

    params = {
        "symbol": api_symbol,
        "interval": interval,
        "limit": limit,
    }

    data = api_get(KLINE_URLS, params=params)

    if not isinstance(data, list):
        return []

    candles = []

    for row in data:

        if not isinstance(row, list):
            continue

        if len(row) < 6:
            continue

        try:
            candles.append({
                "open_time": int(row[0]),
                "open": safe_float(row[1]),
                "high": safe_float(row[2]),
                "low": safe_float(row[3]),
                "close": safe_float(row[4]),
                "volume": safe_float(row[5]),
            })

        except Exception:
            continue

    return candles


# ============================================================
# Indicator Calculations
# ============================================================

def sma(values, period):
    if len(values) < period:
        return None

    return sum(values[-period:]) / period


def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (
            (price - result) * multiplier
        ) + result

    return result


def rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            (avg_gain * (period - 1)) +
            gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1)) +
            losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        high = current["high"]
        low = current["low"]
        previous_close = previous["close"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


def volume_ratio(candles, period=20):

    if len(candles) < period + 1:
        return 1.0

    recent = candles[-1]["volume"]

    previous = [
        candle["volume"]
        for candle in candles[-period - 1:-1]
    ]

    average = sum(previous) / len(previous)

    if average <= 0:
        return 1.0

    return recent / average


# ============================================================
# Market Analysis
# ============================================================

def analyze_timeframe(candles):

    if len(candles) < 60:
        return None

    closes = [
        candle["close"]
        for candle in candles
    ]

    current_price = closes[-1]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)

    rsi_value = rsi(closes, 14)
    atr_value = atr(candles, 14)

    vol_ratio = volume_ratio(candles, 20)

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

    # --------------------------
    # MACD
    # --------------------------

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)

    macd_value = 0.0

    if ema12 is not None and ema26 is not None:
        macd_value = ema12 - ema26

    # --------------------------
    # روند
    # --------------------------

    bullish = (
        ema9 > ema21 > ema50
        and current_price > ema21
    )

    bearish = (
        ema9 < ema21 < ema50
        and current_price < ema21
    )

    # --------------------------
    # امتیاز خرید
    # --------------------------

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

    if vol_ratio >= 1.2:
        buy_score += 10

    if bullish:
        buy_score += 15

    # --------------------------
    # امتیاز فروش
    # --------------------------

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

    if vol_ratio >= 1.2:
        sell_score += 10

    if bearish:
        sell_score += 15

    return {
        "price": current_price,
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "rsi": rsi_value,
        "atr": atr_value,
        "volume_ratio": vol_ratio,
        "macd": macd_value,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "bullish": bullish,
        "bearish": bearish,
    }


# ============================================================
# Multi-Timeframe Analysis
# ============================================================

def analyze_symbol(symbol):

    candles_5m = get_klines(
        symbol,
        "5m",
        KLINE_LIMIT
    )

    time.sleep(0.15)

    candles_15m = get_klines(
        symbol,
        "15m",
        KLINE_LIMIT
    )

    if not candles_5m or not candles_15m:
        return None

    analysis_5m = analyze_timeframe(candles_5m)
    analysis_15m = analyze_timeframe(candles_15m)

    if not analysis_5m or not analysis_15m:
        return None

    buy_score = (
        analysis_5m["buy_score"] * 0.45
        +
        analysis_15m["buy_score"] * 0.55
    )

    sell_score = (
        analysis_5m["sell_score"] * 0.45
        +
        analysis_15m["sell_score"] * 0.55
    )

    if buy_score >= MIN_SIGNAL_SCORE:
        direction = "BUY"
        score = buy_score

    elif sell_score >= MIN_SIGNAL_SCORE:
        direction = "SELL"
        score = sell_score

    else:
        direction = "WAIT"
        score = max(
            buy_score,
            sell_score
        )

    return {
        "symbol": symbol,
        "direction": direction,
        "score": round(score, 2),
        "price": analysis_5m["price"],
        "atr": analysis_5m["atr"],
        "5m": analysis_5m,
        "15m": analysis_15m,
        "candles_5m": candles_5m,
        "candles_15m": candles_15m,
    }


# ============================================================
# Pattern Fingerprint
# ============================================================

def create_pattern_fingerprint(analysis):

    a5 = analysis["5m"]
    a15 = analysis["15m"]

    def bucket_rsi(value):
        if value < 35:
            return "RSI_LOW"
        if value < 45:
            return "RSI_WEAK"
        if value < 55:
            return "RSI_MID"
        if value < 65:
            return "RSI_STRONG"
        return "RSI_HIGH"

    def bucket_volume(value):
        if value < 0.8:
            return "VOL_LOW"
        if value < 1.2:
            return "VOL_NORMAL"
        if value < 2:
            return "VOL_HIGH"
        return "VOL_SPIKE"

    parts = [
        analysis["direction"],

        "5M_BULL" if a5["bullish"] else
        "5M_BEAR" if a5["bearish"] else
        "5M_MIXED",

        "15M_BULL" if a15["bullish"] else
        "15M_BEAR" if a15["bearish"] else
        "15M_MIXED",

        bucket_rsi(a5["rsi"]),
        bucket_rsi(a15["rsi"]),

        bucket_volume(a5["volume_ratio"]),

        "MACD_POS" if a5["macd"] > 0 else "MACD_NEG",
    ]

    raw = "|".join(parts)

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:20]


# ============================================================
# Learning System
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

    if "patterns" not in data:
        data["patterns"] = {}

    if "stats" not in data:
        data["stats"] = {
            "total": 0,
            "tp1": 0,
            "tp2": 0,
            "sl": 0,
            "ambiguous": 0,
            "unknown": 0,
        }

    return data


def get_pattern_history(pattern_id):

    learning = get_learning_data()

    return learning["patterns"].get(
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


def historical_pattern_score(pattern_id):

    history = get_pattern_history(pattern_id)

    total = (
        history.get("tp1", 0)
        +
        history.get("tp2", 0)
        +
        history.get("sl", 0)
        +
        history.get("ambiguous", 0)
    )

    if total < 3:
        return None

    successful = (
        history.get("tp1", 0)
        +
        history.get("tp2", 0)
    )

    return round(
        successful / total * 100,
        2
    )


def update_learning(signal, result):

    learning = get_learning_data()

    pattern_id = signal["pattern_id"]

    if pattern_id not in learning["patterns"]:
        learning["patterns"][pattern_id] = {
            "total": 0,
            "tp1": 0,
            "tp2": 0,
            "sl": 0,
            "ambiguous": 0,
            "unknown": 0,
        }

    pattern = learning["patterns"][pattern_id]

    pattern["total"] += 1

    if result in pattern:
        pattern[result] += 1
    else:
        pattern["unknown"] += 1

    learning["stats"]["total"] += 1

    if result in learning["stats"]:
        learning["stats"][result] += 1
    else:
        learning["stats"]["unknown"] += 1

    save_json(
        LEARNING_FILE,
        learning
    )


# ============================================================
# Duplicate Signal Detection
# ============================================================

def load_signals():

    data = load_json(
        SIGNALS_FILE,
        []
    )

    if not isinstance(data, list):
        return []

    return data


def save_signals(signals):

    if len(signals) > MAX_STORED_SIGNALS:
        signals = signals[-MAX_STORED_SIGNALS:]

    save_json(
        SIGNALS_FILE,
        signals
    )


def is_duplicate_signal(
    signals,
    symbol,
    direction,
    pattern_id
):

    now = datetime.now(timezone.utc).timestamp()

    for signal in reversed(signals):

        if signal.get("status") not in [
            "OPEN",
            "TP1",
        ]:
            continue

        if signal.get("symbol") != symbol:
            continue

        if signal.get("direction") != direction:
            continue

        if signal.get("pattern_id") != pattern_id:
            continue

        created_at = signal.get(
            "created_timestamp",
            0
        )

        age_minutes = (
            now - created_at
        ) / 60

        if age_minutes < DEDUP_MINUTES:
            return True

    return False


# ============================================================
# Create Signal
# ============================================================

def create_signal(analysis):

    direction = analysis["direction"]

    price = analysis["price"]
    atr_value = analysis["atr"]

    if atr_value <= 0:
        return None

    
    pattern_id = create_pattern_fingerprint(
        analysis
    )

    history_score = historical_pattern_score(
        pattern_id
    )
    if direction == "BUY":

        stop_loss = price - (
            atr_value * SL_ATR
        )

        tp1 = price + (
            atr_value * TP1_ATR
        )

        tp2 = price + (
            atr_value * TP2_ATR
        )

    elif direction == "SELL":

        stop_loss = price + (
            atr_value * SL_ATR
        )

        tp1 = price - (
            atr_value * TP1_ATR
        )

        tp2 = price - (
            atr_value * TP2_ATR
        )

    else:
        return None
    return {
        "id": hashlib.sha256(
            (
                analysis["symbol"]
                + direction
                + str(time.time())
            ).encode()
        ).hexdigest()[:16],

        "created_at": now_iso(),
        "created_timestamp": time.time(),

        "symbol": analysis["symbol"],
        "direction": direction,

        "score": analysis["score"],

        "entry": price,
        "tp1": tp1,
        "tp2": tp2,
        "stop_loss": stop_loss,

        "pattern_id": pattern_id,

        "historical_pattern_score":
            history_score,

        "status": "OPEN",

        "result": None,
        "closed_at": None,

        "analysis": {
            "5m": analysis["5m"],
            "15m": analysis["15m"],
        },
    }


# ============================================================
# Signal Message
# ============================================================

def signal_to_message(signal):

    direction_text = (
        "🟢 خرید / LONG"
        if signal["direction"] == "BUY"
        else
        "🔴 فروش / SHORT"
    )

    historical = signal.get(
        "historical_pattern_score"
    )

    if historical is None:
        historical_text = (
            "📚 سابقه این الگو: "
            "هنوز داده کافی ندارد"
        )
    else:
        historical_text = (
            f"📚 سابقه همین الگو: "
            f"{historical}%"
        )

    return (
        "🚨 سیگنال تحلیلی Tabdeal\n\n"
        f"🪙 نماد: {signal['symbol']}\n"
        f"📊 جهت: {direction_text}\n"
        f"🎯 امتیاز تحلیل: {signal['score']}%\n\n"
        f"💰 ورود فرضی: {signal['entry']:.8f}\n"
        f"🎯 TP1: {signal['tp1']:.8f}\n"
        f"🎯 TP2: {signal['tp2']:.8f}\n"
        f"🛑 SL: {signal['stop_loss']:.8f}\n\n"
        f"{historical_text}\n\n"
        "⏱ تحلیل: 5m + 15m\n"
        "⚠️ این فقط سیگنال تحلیلی است؛ "
        "هیچ معامله‌ای توسط ربات انجام نمی‌شود."
    )


# ============================================================
# Result Evaluation
# ============================================================

def evaluate_signal(signal):
                if signal.get("status") == "CLOSED":
        return None

    symbol = signal["symbol"]

    created_timestamp = signal.get(
        "created_timestamp",
        0
    )

    direction = signal["direction"]

    entry = safe_float(
        signal["entry"]
    )

    tp1 = safe_float(
        signal["tp1"]
    )

    tp2 = safe_float(
        signal["tp2"]
    )

    sl = safe_float(
        signal["stop_loss"]
    )

    if entry <= 0:
        return None

    # از زمان ایجاد سیگنال به بعد
    candles = get_klines(
        symbol,
        "5m",
        120
    )

    if not candles:
        return None

    for candle in candles:

        candle_time = candle["open_time"] / 1000

        if candle_time <= created_timestamp:
            continue

        high = candle["high"]
        low = candle["low"]

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        if direction == "BUY":

            hit_sl = low <= sl
            hit_tp2 = high >= tp2
            hit_tp1 = high >= tp1

            # اگر در یک کندل هم SL و هم TP خورده باشد
            # از OHLC نمی‌توان فهمید کدام اول اتفاق افتاده
            if hit_sl and hit_tp2:
                return "ambiguous"

            if hit_sl and hit_tp1:
                return "ambiguous"

            if hit_tp2:
                return "tp2"

            if hit_tp1:
                return "tp1"

            if hit_sl:
                return "sl"

                # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        elif direction == "SELL":

            hit_sl = high >= sl
            hit_tp2 = low <= tp2
            hit_tp1 = low <= tp1

            if hit_sl and hit_tp2:
                return "ambiguous"

            if hit_sl and hit_tp1:
                return "ambiguous"

            if hit_tp2:
                return "tp2"

            if hit_tp1:
                return "tp1"

            if hit_sl:
                return "sl"

    return None


def update_signal_status(signal, result):

    if result == "tp1":
        signal["status"] = "TP1"
        signal["result"] = "tp1"

    elif result == "tp2":
        signal["status"] = "CLOSED"
        signal["result"] = "tp2"
        signal["closed_at"] = now_iso()

    elif result == "sl":
        signal["status"] = "CLOSED"
        signal["result"] = "sl"
        signal["closed_at"] = now_iso()

    elif result == "ambiguous":
        signal["status"] = "CLOSED"
        signal["result"] = "ambiguous"
        signal["closed_at"] = now_iso()

    else:
        return False

    return True


# ============================================================
# Statistics
# ============================================================

def statistics():

    signals = load_signals()

    total = 0
    tp1 = 0
    tp2 = 0
    sl = 0
    ambiguous = 0
    open_count = 0

    for signal in signals:

        result = signal.get("result")

        if result:
            total += 1

        if result == "tp1":
            tp1 += 1

        elif result == "tp2":
            tp2 += 1

        elif result == "sl":
            sl += 1

        elif result == "ambiguous":
            ambiguous += 1

        if signal.get("status") in [
            "OPEN",
            "TP1",
        ]:
            open_count += 1

    completed = (
        tp1
        + tp2
        + sl
        + ambiguous
    )

    success = tp1 + tp2

    accuracy = None

    if completed > 0:
        accuracy = round(
            success / completed * 100,
            2
        )

    return {
        "total_completed": completed,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "ambiguous": ambiguous,
        "open": open_count,
        "accuracy": accuracy,
    }


# ============================================================
# Main Scanner
# ============================================================

def scan_market():

    print("\n")
    print("=" * 60)
    print("TABDEAL PROFESSIONAL MARKET SCANNER")
    print("=" * 60)

    symbols = get_symbols()

    if not symbols:
        print("NO SYMBOLS FOUND")
        return

    print(
        f"\n🔎 {len(symbols)} symbols will be analyzed."
    )

    signals = load_signals()

    new_signals = []

    # --------------------------------------------------------
    # ابتدا نتایج سیگنال‌های قبلی
    # --------------------------------------------------------

    print("\n=== بررسی نتایج سیگنال‌های قبلی ===")

    for signal in signals:

        if signal.get("status") not in [
            "OPEN",
            "TP1",
        ]:
            continue

        try:

            result = evaluate_signal(
                signal
            )

            if result:

                print(
                    "RESULT:",
                    signal["symbol"],
                    signal["direction"],
                    result
                )

                old_result = signal.get(
                    "result"
                )

                changed = update_signal_status(
                    signal,
                    result
                )

                if changed:

                    if old_result != result:
                        update_learning(
                            signal,
                            result
                        )

                    if result == "tp1":
                        send_telegram(
                            "🎯 TP1 فعال شد\n\n"
                            f"🪙 {signal['symbol']}\n"
                            f"📊 {signal['direction']}\n"
                            "ربات همچنان نتیجه نهایی را "
                            "بررسی می‌کند."
                        )

                    elif result == "tp2":
                        send_telegram(
                            "✅ TP2 تکمیل شد\n\n"
                            f"🪙 {signal['symbol']}\n"
                            f"📊 {signal['direction']}\n"
                            "الگو با موفقیت ثبت شد."
                        )

                    elif result == "sl":
                        send_telegram(
                            "🛑 حد ضرر فعال شد\n\n"
                            f"🪙 {signal['symbol']}\n"
                            f"📊 {signal['direction']}\n"
                            "نتیجه برای یادگیری ذخیره شد."
                        )

                    elif result == "ambiguous":
                        send_telegram(
                            "⚠️ نتیجه نامشخص\n\n"
                            f"🪙 {signal['symbol']}\n"
                            "در یک کندل هم TP و هم SL "
                            "قابل مشاهده بوده؛ "
                            "این مورد به عنوان موفقیت "
                            "محسوب نمی‌شود."
                        )

        except Exception as e:

            print(
                "SIGNAL RESULT ERROR:",
                signal.get("symbol"),
                e
            )

    # --------------------------------------------------------
    # تحلیل بازار
    # --------------------------------------------------------

    print("\n=== شروع تحلیل بازار ===")

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        print(
            f"\n[{index}/{len(symbols)}] "
            f"Analyzing {symbol}"
        )

        try:

            analysis = analyze_symbol(
                symbol
            )

            if not analysis:
                print(
                    "No sufficient data:",
                    symbol
                )
                continue

            print(
                "5m:",
                analysis["5m"]["buy_score"],
                "BUY /",
                analysis["5m"]["sell_score"],
                "SELL"
            )

            print(
                "15m:",
                analysis["15m"]["buy_score"],
                "BUY /",
                analysis["15m"]["sell_score"],
                "SELL"
            )

            print(
                "FINAL:",
                analysis["direction"],
                analysis["score"]
            )

            # WAIT = هیچ سیگنالی تولید نمی‌کنیم
            if analysis["direction"] == "WAIT":
                continue

            signal = create_signal(
                analysis
            )

            if not signal:
                continue

            duplicate = is_duplicate_signal(
                signals,
                signal["symbol"],
                signal["direction"],
                signal["pattern_id"]
            )

            if duplicate:

                print(
                    "DUPLICATE SIGNAL IGNORED:",
                    symbol
                )

                continue

            signals.append(signal)
            new_signals.append(signal)

            send_telegram(
                signal_to_message(signal)
            )

            print(
                "NEW SIGNAL:",
                symbol,
                signal["direction"]
            )

            # کمی فاصله برای API
            time.sleep(0.2)

        except Exception as e:

            print(
                "ANALYSIS ERROR:",
                symbol,
                e
            )

    # --------------------------------------------------------
    # ذخیره
    # --------------------------------------------------------

    save_signals(signals)

    stats = statistics()

    print("\n")
    print("=" * 60)
    print("MARKET SCAN FINISHED")
    print("=" * 60)

    print(
        "Symbols:",
        len(symbols)
    )

    print(
        "New signals:",
        len(new_signals)
    )

    print(
        "Completed:",
        stats["total_completed"]
    )

    print(
        "TP1:",
        stats["tp1"]
    )

    print(
        "TP2:",
        stats["tp2"]
    )

    print(
        "SL:",
        stats["sl"]
    )

    print(
        "Ambiguous:",
        stats["ambiguous"]
    )

    print(
        "Open:",
        stats["open"]
    )

    print(
        "Accuracy:",
        stats["accuracy"]
    )

    # --------------------------------------------------------
    # گزارش آماری تلگرام
    # --------------------------------------------------------

    if stats["total_completed"] > 0:

        accuracy_text = (
            f"{stats['accuracy']}%"
            if stats["accuracy"] is not None
            else "نداریم"
        )

        message = (
            "📊 گزارش یادگیری ربات Tabdeal\n\n"
            f"🔎 نمادهای بررسی‌شده: {len(symbols)}\n"
            f"🆕 سیگنال جدید: {len(new_signals)}\n"
            f"📌 نتایج ثبت‌شده: "
            f"{stats['total_completed']}\n\n"
            f"🎯 TP1: {stats['tp1']}\n"
            f"✅ TP2: {stats['tp2']}\n"
            f"🛑 SL: {stats['sl']}\n"
            f"⚠️ نامشخص: {stats['ambiguous']}\n\n"
            f"📈 دقت فعلی ثبت‌شده: "
            f"{accuracy_text}\n\n"
            f"📂 الگوهای ذخیره‌شده در:\n"
            f"{LEARNING_FILE}"
        )

        send_telegram(message)


# ============================================================
# Entry Point
# ============================================================

def main():

    print(
        "\n"
        "====================================================\n"
        " TABDEAL AI-STYLE ANALYSIS / LEARNING BOT\n"
        " Analysis Only - NO TRADE EXECUTION\n"
        "====================================================\n"
    )

    print(
        "Started:",
        now_iso()
    )

    try:

        scan_market()

    except Exception as e:

        print(
            "\nFATAL ERROR:",
            e
        )

        send_telegram(
            "❌ خطای اصلی ربات تحلیل Tabdeal\n\n"
            f"{str(e)[:1000]}"
        )

        raise


if __name__ == "__main__":
    main()
