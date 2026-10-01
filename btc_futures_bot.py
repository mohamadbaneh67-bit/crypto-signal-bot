import os
import json
import time
import hashlib
import threading
import requests
import websocket

from datetime import datetime, timezone


# =========================================================
# BTCUSDT TABDEAL FUTURES SIGNAL BOT
# =========================================================

SYMBOL = "BTCUSDT"

# ---------------------------------------------------------
# Futures WebSocket رسمی تبدیل
# ---------------------------------------------------------

WS_URL = "wss://api1.tabdeal.org/special_margin/stream/"

WS_STREAM = "btcusdt@depth@2000ms"

# ---------------------------------------------------------
# Telegram
# ---------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ---------------------------------------------------------
# فایل‌های ذخیره اطلاعات
# ---------------------------------------------------------

SNAPSHOT_FILE = "btc_futures_snapshots.json"
SIGNALS_FILE = "btc_futures_signals.json"

# ---------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------

MAX_SNAPSHOTS = 50000
MAX_SIGNALS = 3000

COLLECTION_SECONDS = 20
WS_TIMEOUT = 10

MIN_SCORE = 72.0
DUPLICATE_MINUTES = 60

# ---------------------------------------------------------
# تایم‌فریم‌ها
# ---------------------------------------------------------

TIMEFRAMES = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}

# ---------------------------------------------------------
# وزن تایم‌فریم‌ها
# ---------------------------------------------------------

TF_WEIGHT = {
    "1w": 2.0,
    "1d": 2.0,
    "4h": 1.7,
    "1h": 1.5,
    "30m": 1.2,
    "15m": 1.0,
    "5m": 0.8,
}


# =========================================================
# ابزارهای عمومی
# =========================================================

def now_utc():
    return datetime.now(timezone.utc).isoformat()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


# =========================================================
# JSON
# =========================================================

def load_json_file(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(filename, data):
    temp_file = filename + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(temp_file, filename)


# =========================================================
# پردازش Order Book
# =========================================================

def process_orderbook(data):
    if not isinstance(data, dict):
        return None

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    if not bids or not asks:
        return None

    try:
        best_bid = safe_float(bids[0][0])
        best_ask = safe_float(asks[0][0])

        if best_bid <= 0 or best_ask <= 0:
            return None

        bid_qty = sum(
            safe_float(item[1])
            for item in bids
            if len(item) >= 2
        )

        ask_qty = sum(
            safe_float(item[1])
            for item in asks
            if len(item) >= 2
        )

        total_qty = bid_qty + ask_qty

        if total_qty > 0:
            imbalance = (
                (bid_qty - ask_qty)
                / total_qty
            )
        else:
            imbalance = 0.0

        mid_price = (
            best_bid + best_ask
        ) / 2

        return {
            "symbol": SYMBOL,
            "timestamp": time.time(),
            "datetime": now_utc(),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "price": mid_price,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "imbalance": imbalance,
        }

    except Exception as e:
        print("خطا در پردازش Order Book:", e)
        return None


# =========================================================
# دریافت Futures از WebSocket
# =========================================================

def collect_snapshots():
    snapshots = []
    stop_event = threading.Event()
    message_count = 0
    reconnect_count = 0

    def on_message(ws, message):
        nonlocal message_count

        message_count += 1

        try:
            print(
                f"WebSocket message #{message_count}:",
                message[:1000] if isinstance(message, str) else message
            )

            data = json.loads(message)

            # پاسخ Subscribe
            if isinstance(data, dict) and "result" in data:
                print("پاسخ Subscribe:", data)
                return

            # پیام خطای سرور
            if isinstance(data, dict) and "error" in data:
                print("خطای سرور WebSocket:", data)
                return

            snapshot = process_orderbook(data)

            if snapshot:
                snapshots.append(snapshot)

                print(
                    "داده Futures دریافت شد | "
                    f"Price: {snapshot['price']} | "
                    f"Imbalance: {snapshot['imbalance']:.4f}"
                )

        except Exception as e:
            print("خطا در پردازش پیام WebSocket:", repr(e))

    def on_error(ws, error):
        print("WebSocket error:", repr(error))

    def on_close(ws, close_status_code, close_msg):
        print(
            "WebSocket بسته شد:",
            close_status_code,
            close_msg,
        )

    def on_open(ws):
        print("اتصال Futures WebSocket برقرار شد.")

        subscribe_message = {
            "method": "SUBSCRIBE",
            "params": [WS_STREAM],
            "id": 1,
        }

        try:
            ws.send(json.dumps(subscribe_message))
            print(
                "Subscribe ارسال شد:",
                WS_STREAM
            )
        except Exception as e:
            print(
                "خطا هنگام ارسال Subscribe:",
                repr(e)
            )

    def run_connection():
        nonlocal reconnect_count

        while not stop_event.is_set():

            reconnect_count += 1

            print(
                f"تلاش اتصال WebSocket شماره "
                f"{reconnect_count}"
            )

            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )

            try:
                ws.run_forever(
                    ping_interval=30,
                    ping_timeout=10,
                )

            except Exception as e:
                print(
                    "خطای اجرای WebSocket:",
                    repr(e)
                )

            if stop_event.is_set():
                break

            print(
                "اتصال قطع شد؛ "
                "تلاش برای اتصال مجدد..."
            )

            time.sleep(2)

    thread = threading.Thread(
        target=run_connection,
        daemon=True,
    )

    thread.start()

    start_time = time.time()

    while (
        time.time() - start_time
        < COLLECTION_SECONDS
    ):
        time.sleep(1)

        # اگر داده گرفتیم، اتصال را حفظ می‌کنیم
        # تا پایان زمان جمع‌آوری

    stop_event.set()

    try:
        print("در حال بستن WebSocket...")
    except Exception:
        pass

    thread.join(timeout=5)

    print(
        "تعداد پیام‌های خام دریافت‌شده:",
        message_count
    )

    print(
        "تعداد Snapshot دریافت‌شده:",
        len(snapshots)
    )

    return snapshots


# =========================================================
# ذخیره Snapshot
# =========================================================

def append_snapshots(new_snapshots):

    old_snapshots = load_json_file(
        SNAPSHOT_FILE,
        [],
    )

    if not isinstance(old_snapshots, list):
        old_snapshots = []

    old_snapshots.extend(new_snapshots)

    if len(old_snapshots) > MAX_SNAPSHOTS:
        old_snapshots = old_snapshots[
            -MAX_SNAPSHOTS:
        ]

    save_json_file(
        SNAPSHOT_FILE,
        old_snapshots,
    )

    return old_snapshots


# =========================================================
# ساخت کندل
# =========================================================

def build_candles(
    snapshots,
    timeframe_seconds,
):

    if not snapshots:
        return []

    buckets = {}

    for item in snapshots:

        timestamp = safe_float(
            item.get("timestamp")
        )

        price = safe_float(
            item.get("price")
        )

        if timestamp <= 0 or price <= 0:
            continue

        bucket = (
            int(timestamp)
            // timeframe_seconds
        ) * timeframe_seconds

        if bucket not in buckets:

            buckets[bucket] = {
                "timestamp": bucket,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
                "count": 0,
            }

        candle = buckets[bucket]

        candle["high"] = max(
            candle["high"],
            price,
        )

        candle["low"] = min(
            candle["low"],
            price,
        )

        candle["close"] = price

        candle["count"] += 1

    candles = list(
        buckets.values()
    )

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    return candles


def build_all_timeframes(snapshots):

    result = {}

    for timeframe, seconds in TIMEFRAMES.items():

        result[timeframe] = build_candles(
            snapshots,
            seconds,
        )

    return result


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = (
        sum(values[:period])
        / period
    )

    for price in values[period:]:

        result = (
            (price - result)
            * multiplier
            + result
        )

    return result


# =========================================================
# RSI
# =========================================================

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

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
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
        len(gains),
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

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# =========================================================
# ATR
# =========================================================

def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        high = current["high"]
        low = current["low"]
        previous_close = previous["close"]

        tr = max(
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

        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    return (
        sum(true_ranges[-period:])
        / period
    )


# =========================================================
# MACD واقعی‌تر
# =========================================================

def macd_histogram(values):

    if len(values) < 35:
        return None

    ema12_values = []
    ema26_values = []

    for i in range(
        26,
        len(values) + 1,
    ):

        part = values[:i]

        e12 = ema(
            part,
            12,
        )

        e26 = ema(
            part,
            26,
        )

        if e12 is not None and e26 is not None:
            ema12_values.append(e12)
            ema26_values.append(e26)

    if not ema12_values:
        return None

    macd_values = []

    for a, b in zip(
        ema12_values,
        ema26_values,
    ):
        macd_values.append(
            a - b
        )

    if len(macd_values) < 9:
        return macd_values[-1]

    signal_line = ema(
        macd_values,
        9,
    )

    if signal_line is None:
        return macd_values[-1]

    return (
        macd_values[-1]
        - signal_line
    )


# =========================================================
# تحلیل یک تایم‌فریم
# =========================================================

def analyze_timeframe(
    timeframe,
    candles,
):

    if len(candles) < 35:

        return {
            "timeframe": timeframe,
            "direction": "WAIT",
            "score": 0.0,
            "reason": "داده کافی نیست",
            "candles": len(candles),
        }

    closes = [
        safe_float(
            c["close"]
        )
        for c in candles
    ]

    current_price = closes[-1]

    ema20 = ema(
        closes,
        20,
    )

    ema50 = ema(
        closes,
        50,
    )

    current_rsi = rsi(
        closes,
        14,
    )

    current_atr = atr(
        candles,
        14,
    )

    macd = macd_histogram(
        closes
    )

    long_score = 0.0
    short_score = 0.0

    reasons_long = []
    reasons_short = []

    # -----------------------------------------------------
    # EMA20
    # -----------------------------------------------------

    if ema20 is not None:

        if current_price > ema20:

            long_score += 20

            reasons_long.append(
                "قیمت بالای EMA20"
            )

        elif current_price < ema20:

            short_score += 20

            reasons_short.append(
                "قیمت زیر EMA20"
            )

    # -----------------------------------------------------
    # EMA20 / EMA50
    # -----------------------------------------------------

    if (
        ema20 is not None
        and ema50 is not None
    ):

        if ema20 > ema50:

            long_score += 25

            reasons_long.append(
                "روند EMA صعودی"
            )

        elif ema20 < ema50:

            short_score += 25

            reasons_short.append(
                "روند EMA نزولی"
            )

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    if current_rsi is not None:

        if (
            current_rsi > 50
            and current_rsi < 70
        ):

            long_score += 15

            reasons_long.append(
                f"RSI صعودی ({current_rsi:.1f})"
            )

        elif (
            current_rsi >= 30
            and current_rsi < 50
        ):

            short_score += 15

            reasons_short.append(
                f"RSI نزولی ({current_rsi:.1f})"
            )

        elif current_rsi >= 70:

            reasons_long.append(
                f"RSI اشباع خرید ({current_rsi:.1f})"
            )

        elif current_rsi <= 30:

            reasons_short.append(
                f"RSI اشباع فروش ({current_rsi:.1f})"
            )

    # -----------------------------------------------------
    # MACD
    # -----------------------------------------------------

    if macd is not None:

        if macd > 0:

            long_score += 15

            reasons_long.append(
                "MACD مثبت"
            )

        elif macd < 0:

            short_score += 15

            reasons_short.append(
                "MACD منفی"
            )

    # -----------------------------------------------------
    # نتیجه
    # -----------------------------------------------------

    if long_score > short_score:

        direction = "LONG"
        score = long_score
        reasons = reasons_long

    elif short_score > long_score:

        direction = "SHORT"
        score = short_score
        reasons = reasons_short

    else:

        direction = "WAIT"
        score = 0.0

        reasons = [
            "قدرت خرید و فروش برابر است"
        ]

    return {
        "timeframe": timeframe,
        "direction": direction,
        "score": round(
            score,
            2,
        ),
        "price": current_price,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": current_rsi,
        "atr": current_atr,
        "macd": macd,
        "reasons": reasons,
        "candles": len(candles),
    }


# =========================================================
# ترکیب تایم‌فریم‌ها
# =========================================================

def combine_analysis(
    analyses,
    orderbook,
):

    if not analyses:

        return {
            "direction": "WAIT",
            "score": 0.0,
            "reason": "تحلیل موجود نیست",
        }

    long_score = 0.0
    short_score = 0.0

    long_reasons = []
    short_reasons = []

    valid_count = 0

    for timeframe, analysis in analyses.items():

        direction = analysis.get(
            "direction",
            "WAIT",
        )

        score = safe_float(
            analysis.get("score")
        )

        weight = TF_WEIGHT.get(
            timeframe,
            1.0,
        )

        if direction == "LONG":

            long_score += (
                score * weight
            )

            long_reasons.append(
                f"{timeframe}: "
                + "، ".join(
                    analysis.get(
                        "reasons",
                        [],
                    )
                )
            )

            valid_count += 1

        elif direction == "SHORT":

            short_score += (
                score * weight
            )

            short_reasons.append(
                f"{timeframe}: "
                + "، ".join(
                    analysis.get(
                        "reasons",
                        [],
                    )
                )
            )

            valid_count += 1

    # -----------------------------------------------------
    # Order Book
    # -----------------------------------------------------

    imbalance = safe_float(
        orderbook.get(
            "imbalance"
        )
    )

    if imbalance > 0.15:

        long_score += 10

        long_reasons.append(
            "قدرت بیشتر سفارش‌های خرید"
        )

    elif imbalance < -0.15:

        short_score += 10

        short_reasons.append(
            "قدرت بیشتر سفارش‌های فروش"
        )

    # -----------------------------------------------------
    # داده ناکافی
    # -----------------------------------------------------

    if valid_count < 3:

        return {
            "direction": "WAIT",
            "score": 0.0,
            "reason": (
                "برای تصمیم‌گیری "
                "داده کافی نیست"
            ),
            "long_score": round(
                long_score,
                2,
            ),
            "short_score": round(
                short_score,
                2,
            ),
        }

    total_score = (
        long_score
        + short_score
    )

    if total_score <= 0:

        return {
            "direction": "WAIT",
            "score": 0.0,
            "reason": "قدرت سیگنال کافی نیست",
            "long_score": 0.0,
            "short_score": 0.0,
        }

    # -----------------------------------------------------
    # LONG
    # -----------------------------------------------------

    if long_score > short_score:

        difference = (
            long_score
            - short_score
        )

        confidence = (
            difference
            / total_score
        ) * 100

        if confidence < 15:

            return {
                "direction": "WAIT",
                "score": round(
                    confidence,
                    2,
                ),
                "reason": (
                    "تایم‌فریم‌ها "
                    "با یکدیگر اختلاف دارند"
                ),
                "long_score": round(
                    long_score,
                    2,
                ),
                "short_score": round(
                    short_score,
         2,
    ),
}

        return {
            "direction": "SHORT",
            "score": round(
                confidence,
                2,
            ),
            "reason": " | ".join(
                short_reasons
            ),
            "long_score": round(
                long_score,
                2,
            ),
            "short_score": round(
                short_score,
                2,
            ),
        }

    return {
        "direction": "WAIT",
        "score": 0.0,
        "reason": "بازار جهت مشخصی ندارد",
        "long_score": round(
            long_score,
            2,
        ),
        "short_score": round(
            short_score,
            2,
        ),
    }


# =========================================================
# Entry / Stop / TP
# =========================================================

def calculate_targets(
    direction,
    entry,
    atr_value,
):

    entry = safe_float(entry)
    atr_value = safe_float(
        atr_value
    )

    if entry <= 0 or atr_value <= 0:

        return {
            "entry": entry,
            "stop": None,
            "tp1": None,
            "tp2": None,
        }

    risk = atr_value * 1.2

    if direction == "LONG":

        stop = entry - risk
        tp1 = entry + risk
        tp2 = entry + (
            risk * 2
        )

    elif direction == "SHORT":

        stop = entry + risk
        tp1 = entry - risk
        tp2 = entry - (
            risk * 2
        )

    else:

        return {
            "entry": entry,
            "stop": None,
            "tp1": None,
            "tp2": None,
        }

    return {
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
    }


# =========================================================
# Fingerprint
# =========================================================

def signal_fingerprint(signal):

    raw = "|".join(
        [
            str(signal.get("symbol", "")),
            str(signal.get("direction", "")),
            str(signal.get("entry", "")),
            str(signal.get("stop", "")),
            str(signal.get("tp1", "")),
            str(signal.get("tp2", "")),
        ]
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# =========================================================
# Duplicate
# =========================================================

def is_duplicate_signal(
    signal,
    history,
):

    fingerprint = signal_fingerprint(
        signal
    )

    now = time.time()

    for old in reversed(history):

        if (
            old.get("fingerprint")
            != fingerprint
        ):
            continue

        old_time = safe_float(
            old.get("timestamp")
        )

        if old_time <= 0:
            continue

        age_minutes = (
            now - old_time
        ) / 60

        if age_minutes < DUPLICATE_MINUTES:
            return True

    return False


# =========================================================
# Save signal
# =========================================================

def save_signal(signal):

    history = load_json_file(
        SIGNALS_FILE,
        [],
    )

    if not isinstance(history, list):
        history = []

    signal["fingerprint"] = (
        signal_fingerprint(signal)
    )

    signal["timestamp"] = time.time()
    signal["created_at"] = now_utc()

    history.append(signal)

    if len(history) > MAX_SIGNALS:

        history = history[
            -MAX_SIGNALS:
        ]

    save_json_file(
        SIGNALS_FILE,
        history,
    )

    return signal


# =========================================================
# Create signal
# =========================================================

def create_signal(
    combined,
    analyses,
    orderbook,
):

    direction = combined.get(
        "direction",
        "WAIT",
    )

    score = safe_float(
        combined.get("score")
    )

    if direction == "WAIT":

        return {
            "status": "WAIT",
            "symbol": SYMBOL,
            "direction": "WAIT",
            "score": round(
                score,
                2,
            ),
            "reason": combined.get(
                "reason",
                "سیگنال معتبر پیدا نشد",
            ),
            "created_at": now_utc(),
        }

    if score < MIN_SCORE:

        return {
            "status": "WAIT",
            "symbol": SYMBOL,
            "direction": "WAIT",
            "score": round(
                score,
                2,
            ),
            "reason": (
                "امتیاز سیگنال "
                "به حداقل لازم نرسید"
            ),
            "created_at": now_utc(),
        }

    entry_analysis = analyses.get(
        "5m",
        {},
    )

    entry = safe_float(
        orderbook.get("price")
    )

    atr_value = safe_float(
        entry_analysis.get("atr")
    )

    if atr_value <= 0:

        atr_value = safe_float(
            analyses.get(
                "15m",
                {},
            ).get("atr")
        )

    targets = calculate_targets(
        direction,
        entry,
        atr_value,
    )

    return {
        "status": "SIGNAL",
        "symbol": SYMBOL,
        "direction": direction,
        "score": round(
            score,
            2,
        ),
        "entry": targets["entry"],
        "stop": targets["stop"],
        "tp1": targets["tp1"],
        "tp2": targets["tp2"],
        "atr": atr_value,
        "reason": combined.get(
            "reason",
            "",
        ),
        "long_score": combined.get(
            "long_score",
            0,
        ),
        "short_score": combined.get(
            "short_score",
            0,
        ),
        "orderbook_imbalance": orderbook.get(
            "imbalance",
            0,
        ),
        "timeframes": analyses,
        "created_at": now_utc(),
    }


# =========================================================
# Price format
# =========================================================

def format_price(value):

    value = safe_float(value)

    if value <= 0:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:,.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.10f}"


# =========================================================
# Telegram message
# =========================================================

def build_telegram_message(signal):

    direction = signal.get(
        "direction",
        "WAIT",
    )

    if direction == "LONG":
        title = "🟢 LONG"

    elif direction == "SHORT":
        title = "🔴 SHORT"

    else:
        title = "🟡 WAIT"

    message = (
        "📊 BTCUSDT Futures - Tabdeal\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{title}\n"
        f"امتیاز: {signal.get('score', 0):.1f}\n"
    )

    if direction != "WAIT":

        message += (
            "━━━━━━━━━━━━━━━━━━\n"
            f"Entry: {format_price(signal.get('entry'))}\n"
            f"SL: {format_price(signal.get('stop'))}\n"
            f"TP1: {format_price(signal.get('tp1'))}\n"
            f"TP2: {format_price(signal.get('tp2'))}\n"
            "━━━━━━━━━━━━━━━━━━\n"
        )

    message += (
        "دلیل تحلیل:\n"
        f"{signal.get('reason', 'ندارد')}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"زمان: {signal.get('created_at', '-')}"
    )

    return message


# =========================================================
# Telegram
# =========================================================

def send_telegram_message(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "TELEGRAM_BOT_TOKEN تنظیم نشده"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "TELEGRAM_CHAT_ID تنظیم نشده"
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        response.raise_for_status()

        print(
            "پیام تلگرام ارسال شد."
        )

        return True

    except Exception as e:

        print(
            "خطا در ارسال تلگرام:",
            e,
        )

        return False


# =========================================================
# Main analysis
# =========================================================

def run_analysis():

    print(
        "شروع تحلیل BTCUSDT Futures..."
    )

    print(
        "اتصال به Futures WebSocket تبدیل..."
    )

    # -----------------------------------------------------
    # دریافت داده
    # -----------------------------------------------------

    new_snapshots = collect_snapshots()

    if not new_snapshots:

        print(
            "هیچ داده‌ای از Futures دریافت نشد."
        )

        send_telegram_message(
            "⚠️ BTCUSDT Futures Tabdeal\n"
            "داده بازار از WebSocket دریافت نشد."
        )

        return

    print(
        "تعداد داده جدید:",
        len(new_snapshots),
    )

    # -----------------------------------------------------
    # ذخیره
    # -----------------------------------------------------

    all_snapshots = append_snapshots(
        new_snapshots
    )

    print(
        "کل Snapshot ها:",
        len(all_snapshots),
    )

    # -----------------------------------------------------
    # ساخت تایم‌فریم
    # -----------------------------------------------------

    timeframe_candles = (
        build_all_timeframes(
            all_snapshots
        )
    )

    # -----------------------------------------------------
    # تحلیل
    # -----------------------------------------------------

    analyses = {}

    for timeframe in TIMEFRAMES:

        candles = timeframe_candles.get(
            timeframe,
            [],
        )

        print(
            f"{timeframe}: "
            f"{len(candles)} candles"
        )

        analyses[timeframe] = (
            analyze_timeframe(
                timeframe,
                candles,
            )
        )

    # -----------------------------------------------------
    # آخرین Order Book
    # -----------------------------------------------------

    latest_orderbook = (
        new_snapshots[-1]
    )

    # -----------------------------------------------------
    # ترکیب
    # -----------------------------------------------------

    combined = combine_analysis(
        analyses,
        latest_orderbook,
    )

    print(
        "جهت نهایی:",
        combined.get(
            "direction"
        ),
    )

    print(
        "امتیاز:",
        combined.get(
            "score"
        ),
    )

    print(
        "Long:",
        combined.get(
            "long_score"
        ),
    )

    print(
        "Short:",
        combined.get(
            "short_score"
        ),
    )

    # -----------------------------------------------------
    # ساخت سیگنال
    # -----------------------------------------------------

    signal = create_signal(
        combined,
        analyses,
        latest_orderbook,
    )

    # -----------------------------------------------------
    # WAIT
    # -----------------------------------------------------

    if signal.get("status") == "WAIT":

        print(
            "سیگنال معتبر پیدا نشد."
        )

        print(
            signal.get(
                "reason",
                "",
            )
        )

        message = (
            build_telegram_message(
                signal
            )
        )

        send_telegram_message(
            message
        )

        return

    # -----------------------------------------------------
    # Duplicate
    # -----------------------------------------------------

    history = load_json_file(
        SIGNALS_FILE,
        [],
    )

    if is_duplicate_signal(
        signal,
        history,
    ):

        print(
            "سیگنال تکراری است."
        )

        return

    # -----------------------------------------------------
    # Save
    # -----------------------------------------------------

    save_signal(signal)

    # -----------------------------------------------------
    # Telegram
    # -----------------------------------------------------

    message = (
        build_telegram_message(
            signal
        )
    )

    sent = send_telegram_message(
        message
    )

    if sent:

        print(
            "سیگنال با موفقیت ارسال شد."
        )

    else:

        print(
            "سیگنال ساخته شد ولی "
            "ارسال تلگرام ناموفق بود."
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "===================================="
    )

    print(
        "BTCUSDT Tabdeal Futures Signal Bot"
    )

    print(
        "===================================="
    )

    try:

        run_analysis()

    except Exception as e:

        print(
            "خطای اصلی ربات:",
            e,
        )


if __name__ == "__main__":
    main()
