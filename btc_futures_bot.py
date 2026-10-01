import os
import json
import time
import hashlib
import requests
from datetime import datetime, timezone


# ==========================================
# تنظیمات اصلی ربات
# ==========================================

SYMBOL = "BTCUSDT"

API_BASE = "https://api1.tabdeal.org"

DEPTH_PATH = "/r/fapi/v1/depth"
EXCHANGE_INFO_PATH = "/r/fapi/v1/exchangeInfo"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SNAPSHOT_FILE = "btc_futures_snapshots.json"
SIGNALS_FILE = "btc_futures_signals.json"


# ==========================================
# تنظیمات جمع‌آوری داده
# ==========================================

MAX_SNAPSHOTS = 50000
MAX_SIGNALS = 3000

COLLECTION_SECONDS = 20
REQUEST_TIMEOUT = 15


# ==========================================
# تایم‌فریم‌ها
# ==========================================

TIMEFRAMES = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}


# ==========================================
# وزن تایم‌فریم‌ها
# ==========================================

TF_WEIGHT = {
    "1w": 2.0,
    "1d": 2.0,
    "4h": 1.7,
    "1h": 1.5,
    "30m": 1.2,
    "15m": 1.0,
    "5m": 0.8,
}


# ==========================================
# تنظیمات سیگنال
# ==========================================

MIN_SCORE = 72.0

DUPLICATE_MINUTES = 60


# ==========================================
# توابع عمومی
# ==========================================

def now_utc():
    return datetime.now(timezone.utc).isoformat()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def api_get(path, params=None):
    url = API_BASE + path

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ==========================================
# بررسی بازار Futures
# ==========================================

def check_futures_market():

    data = api_get(
        EXCHANGE_INFO_PATH,
        {"symbol": SYMBOL},
    )

    return data


# ==========================================
# دریافت Order Book
# ==========================================

def get_depth_snapshot():

    data = api_get(
        DEPTH_PATH,
        {
            "symbol": SYMBOL,
            "limit": 50,
        },
    )

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    if not bids or not asks:
        raise ValueError("Order Book خالی است")

    best_bid = safe_float(bids[0][0])
    best_ask = safe_float(asks[0][0])

    bid_qty = sum(
        safe_float(item[1])
        for item in bids
    )

    ask_qty = sum(
        safe_float(item[1])
        for item in asks
    )

    total_qty = bid_qty + ask_qty

    if total_qty <= 0:
        imbalance = 0.0
    else:
        imbalance = (
            (bid_qty - ask_qty)
            / total_qty
        )

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
}# ==========================================
# ذخیره و خواندن اطلاعات
# ==========================================

def load_json_file(filename, default):

    if not os.path.exists(filename):
        return default

    try:
        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception:
        return default


def save_json_file(filename, data):

    temp_file = filename + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        temp_file,
        filename,
    )


# ==========================================
# جمع‌آوری Snapshot های بازار
# ==========================================

def collect_snapshots():

    snapshots = []

    start_time = time.time()

    while time.time() - start_time < COLLECTION_SECONDS:

        try:

            snapshot = get_depth_snapshot()

            snapshots.append(snapshot)

        except Exception as e:

            print(
                "خطا در دریافت داده:",
                e,
            )

        time.sleep(5)

    return snapshots


# ==========================================
# اضافه کردن Snapshot های جدید
# ==========================================

def append_snapshots(new_snapshots):

    old_snapshots = load_json_file(
        SNAPSHOT_FILE,
        [],
    )

    if not isinstance(old_snapshots, list):
        old_snapshots = []

    old_snapshots.extend(
        new_snapshots
    )

    # حذف داده‌های خیلی قدیمی
    if len(old_snapshots) > MAX_SNAPSHOTS:

        old_snapshots = old_snapshots[
            -MAX_SNAPSHOTS:
        ]

    save_json_file(
        SNAPSHOT_FILE,
        old_snapshots,
    )

    return old_snapshots


# ==========================================
# ساخت کندل از Snapshot ها
# ==========================================

def build_candles(snapshots, timeframe_seconds):

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


# ==========================================
# ساخت تمام تایم‌فریم‌ها
# ==========================================

def build_all_timeframes(snapshots):

    result = {}

    for name, seconds in TIMEFRAMES.items():

        result[name] = build_candles(
            snapshots,
            seconds,
        )

    return result# ==========================================
# EMA
# ==========================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        result = (
            (price - result)
            * multiplier
            + result
        )

    return result


# ==========================================
# RSI
# ==========================================

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
            losses.append(abs(change))

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
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# ==========================================
# ATR
# ==========================================

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
            abs(high - previous_close),
            abs(low - previous_close),
        )

        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    return (
        sum(true_ranges[-period:])
        / period
    )


# ==========================================
# MACD Histogram
# ==========================================

def macd_histogram(values):

    if len(values) < 35:
        return None

    ema12 = ema(
        values,
        12,
    )

    ema26 = ema(
        values,
        26,
    )

    if ema12 is None or ema26 is None:
        return None

    macd_value = ema12 - ema26

    return macd_value


# ==========================================
# تحلیل یک تایم‌فریم
# ==========================================

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
        safe_float(c["close"])
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
        closes,
    )

    score_long = 0.0
    score_short = 0.0

    reasons_long = []
    reasons_short = []


    # --------------------------------------
    # EMA20 / EMA50
    # --------------------------------------

    if ema20 is not None:

        if current_price > ema20:

            score_long += 20
            reasons_long.append(
                "قیمت بالای EMA20"
            )

        elif current_price < ema20:

            score_short += 20
            reasons_short.append(
                "قیمت زیر EMA20"
            )


    if (
        ema20 is not None
        and ema50 is not None
    ):

        if ema20 > ema50:

            score_long += 25
            reasons_long.append(
                "روند EMA صعودی"
            )

        elif ema20 < ema50:

            score_short += 25
            reasons_short.append(
                "روند EMA نزولی"
            )


    # --------------------------------------
    # RSI
    # --------------------------------------

    if current_rsi is not None:

        if 50 < current_rsi < 70:

            score_long += 15
            reasons_long.append(
                f"RSI صعودی ({current_rsi:.1f})"
            )

        elif 30 < current_rsi < 50:

            score_short += 15
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


    # --------------------------------------
    # MACD
    # --------------------------------------

    if macd is not None:

        if macd > 0:

            score_long += 15
            reasons_long.append(
                "MACD مثبت"
            )

        elif macd < 0:

            score_short += 15
            reasons_short.append(
                "MACD منفی"
            )


    # --------------------------------------
    # نتیجه تایم‌فریم
    # --------------------------------------

    if score_long > score_short:

        direction = "LONG"
        score = score_long
        reasons = reasons_long

    elif score_short > score_long:

        direction = "SHORT"
        score = score_short
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
        "score": round(score, 2),
        "price": current_price,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": current_rsi,
        "atr": current_atr,
        "macd": macd,
        "reasons": reasons,
        "candles": len(candles),
          }# ==========================================
# ترکیب تحلیل تایم‌فریم‌ها
# ==========================================

def combine_analysis(analyses, orderbook):

    if not analyses:
        return {
            "direction": "WAIT",
            "score": 0.0,
            "reason": "تحلیل تایم‌فریم‌ها موجود نیست",
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

            long_score += score * weight

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

            short_score += score * weight

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


    # ======================================
    # بررسی Order Book
    # ======================================

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


    # ======================================
    # اگر داده کافی نیست
    # ======================================

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


    # ======================================
    # بررسی اختلاف دو طرف
    # ======================================

    total_score = (
        long_score
        + short_score
    )

    if total_score <= 0:

        return {
            "direction": "WAIT",
            "score": 0.0,
            "reason": (
                "قدرت سیگنال کافی نیست"
            ),
            "long_score": 0.0,
            "short_score": 0.0,
        }


    # --------------------------------------
    # LONG
    # --------------------------------------

    if long_score > short_score:

        difference = (
            long_score
            - short_score
        )

        confidence = (
            difference
            / total_score
        ) * 100

        # اگر اختلاف خیلی کم باشد
        # وارد معامله نمی‌شویم

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
            "direction": "LONG",
            "score": round(
                confidence,
                2,
            ),
            "reason": " | ".join(
                long_reasons
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


    # --------------------------------------
    # SHORT
    # --------------------------------------

    if short_score > long_score:

        difference = (
            short_score
            - long_score
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
        "reason": (
            "بازار جهت مشخصی ندارد"
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


# ==========================================
# محاسبه Entry / Stop / TP
# ==========================================

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

        tp1 = entry + (
            risk * 1.0
        )

        tp2 = entry + (
            risk * 2.0
        )

    elif direction == "SHORT":

        stop = entry + risk

        tp1 = entry - (
            risk * 1.0
        )

        tp2 = entry - (
            risk * 2.0
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
  }# ==========================================
# ساخت شناسه یکتا برای سیگنال
# ==========================================

def signal_fingerprint(signal):

    raw = "|".join([
        str(signal.get("symbol", "")),
        str(signal.get("direction", "")),
        str(signal.get("entry", "")),
        str(signal.get("stop", "")),
        str(signal.get("tp1", "")),
        str(signal.get("tp2", "")),
    ])

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ==========================================
# بررسی سیگنال تکراری
# ==========================================

def is_duplicate_signal(signal, history):

    fingerprint = signal_fingerprint(
        signal
    )

    now = time.time()

    for old in reversed(history):

        if old.get("fingerprint") != fingerprint:
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


# ==========================================
# ذخیره سیگنال
# ==========================================

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


# ==========================================
# ساخت سیگنال نهایی
# ==========================================

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


    # ======================================
    # پیدا کردن ATR تایم‌فریم 5 دقیقه
    # ======================================

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

    # اگر ATR پنج دقیقه‌ای موجود نبود
    # از 15 دقیقه استفاده می‌کنیم

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


    signal = {
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

    return signal


# ==========================================
# فرمت قیمت
# ==========================================

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


# ==========================================
# ساخت پیام تلگرام
# ==========================================

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

    return message# ==========================================
# ارسال پیام به تلگرام
# ==========================================

def send_telegram_message(message):

    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN تنظیم نشده")
        return False

    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID تنظیم نشده")
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
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return True

    except Exception as e:

        print(
            "خطا در ارسال تلگرام:",
            e,
        )

        return False


# ==========================================
# اجرای یک مرحله تحلیل
# ==========================================

def run_analysis():

    print(
        "شروع تحلیل BTCUSDT Futures..."
    )

    # --------------------------------------
    # بررسی بازار
    # --------------------------------------

    try:

        market = check_futures_market()

        print(
            "اتصال به Futures تبدیل برقرار است."
        )

        print(
            "Market:",
            market,
        )

    except Exception as e:

        print(
            "خطا در اتصال به Futures:",
            e,
        )

        return


    # --------------------------------------
    # جمع‌آوری داده جدید
    # --------------------------------------

    print(
        "در حال جمع‌آوری داده بازار..."
    )

    new_snapshots = collect_snapshots()

    print(
        "تعداد داده جدید:",
        len(new_snapshots),
    )

    if not new_snapshots:

        print(
            "داده‌ای دریافت نشد."
        )

        return


    # --------------------------------------
    # ذخیره داده‌ها
    # --------------------------------------

    all_snapshots = append_snapshots(
        new_snapshots
    )

    print(
        "کل Snapshot های ذخیره‌شده:",
        len(all_snapshots),
    )


    # --------------------------------------
    # ساخت تایم‌فریم‌ها
    # --------------------------------------

    timeframe_candles = (
        build_all_timeframes(
            all_snapshots
        )
    )


    # --------------------------------------
    # تحلیل هر تایم‌فریم
    # --------------------------------------

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


    # --------------------------------------
    # دریافت آخرین Order Book
    # --------------------------------------

    try:

        orderbook = get_depth_snapshot()

    except Exception as e:

        print(
            "خطا در دریافت Order Book:",
            e,
        )

        return


    # --------------------------------------
    # ترکیب تحلیل‌ها
    # --------------------------------------

    combined = combine_analysis(
        analyses,
        orderbook,
    )


    print(
        "جهت نهایی:",
        combined.get("direction"),
    )

    print(
        "امتیاز:",
        combined.get("score"),
    )


    # --------------------------------------
    # ساخت سیگنال
    # --------------------------------------

    signal = create_signal(
        combined,
        analyses,
        orderbook,
    )


    # --------------------------------------
    # اگر WAIT باشد
    # --------------------------------------

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

        message = build_telegram_message(
            signal
        )

        send_telegram_message(
            message
        )

        return


    # --------------------------------------
    # بررسی تکراری نبودن
    # --------------------------------------

    history = load_json_file(
        SIGNALS_FILE,
        [],
    )

    if is_duplicate_signal(
        signal,
        history,
    ):

        print(
            "این سیگنال قبلاً ثبت شده است."
        )

        return


    # --------------------------------------
    # ذخیره سیگنال جدید
    # --------------------------------------

    save_signal(signal)


    # --------------------------------------
    # ارسال تلگرام
    # --------------------------------------

    message = build_telegram_message(
        signal
    )

    sent = send_telegram_message(
        message
    )


    if sent:

        print(
            "سیگنال با موفقیت به تلگرام ارسال شد."
        )

    else:

        print(
            "سیگنال ساخته شد اما ارسال تلگرام ناموفق بود."
        )


# ==========================================
# نقطه شروع برنامه
# ==========================================

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
