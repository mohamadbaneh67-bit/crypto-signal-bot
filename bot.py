import os
import json
import time
import hashlib
import requests
from datetime import datetime, timezone

# ============================================================
# ربات تحلیل حرفه‌ای Futures تبدیل (Tabdeal)
# فقط تحلیل - بدون اجرای معامله
#
# ارزها:
# BTC / ETH / ADA / DOGE / SOL
#
# تایم‌فریم:
# 5m + 15m
#
# خروجی:
# BUY / SELL / WAIT
# Entry / TP1 / TP2 / SL
# نوسان
# اهرم تحلیلی
# علت سیگنال
# نتیجه و یادگیری
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ------------------------------------------------------------
# فقط همین 5 ارز
# ------------------------------------------------------------

TARGET_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "SOLUSDT",
]

# ------------------------------------------------------------
# Tabdeal Futures API
# ------------------------------------------------------------

EXCHANGE_INFO_URLS = [
    "https://api1.tabdeal.org/r/fapi/v1/exchangeInfo",
    "https://api1.tabdeal.org/fapi/v1/exchangeInfo",
]

KLINE_URLS = [
    "https://api1.tabdeal.org/r/fapi/v1/klines",
    "https://api1.tabdeal.org/fapi/v1/klines",
]

# ------------------------------------------------------------
# فایل‌های ذخیره‌سازی
# ------------------------------------------------------------

LEARNING_FILE = "tabdeal_learning.json"
SIGNALS_FILE = "tabdeal_signals.json"

# ------------------------------------------------------------
# تنظیمات
# ------------------------------------------------------------

TIMEFRAMES = ["5m", "15m"]

KLINE_LIMIT = 150

# حداقل امتیاز
MIN_SIGNAL_SCORE = 70

# حداقل اختلاف BUY و SELL
MIN_DIRECTION_GAP = 8

# جلوگیری از تکرار سیگنال
DEDUP_MINUTES = 90

# اهداف بر اساس ATR
TP1_ATR = 1.0
TP2_ATR = 2.0
SL_ATR = 1.2

MAX_STORED_SIGNALS = 3000

# ------------------------------------------------------------
# اهرم تحلیلی
#
# این عدد اهرم واقعی حساب تبدیل نیست.
# فقط از فاصله SL برای مدیریت ریسک تخمین زده می‌شود.
# ------------------------------------------------------------

MAX_ANALYTICAL_LEVERAGE = 20

# حداکثر زیان فرضی روی مارجین در صورت رسیدن به SL
TARGET_MARGIN_LOSS_AT_SL = 5.0

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Tabdeal-Analysis-Bot/3.0",
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
                "text": text[:3900],
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
# API
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
                    last_error = (
                        f"JSON error: {e}"
                    )

            else:

                last_error = (
                    f"HTTP {response.status_code}: "
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
# Symbols
# ============================================================

def normalize_symbol(symbol):

    return (
        str(symbol)
        .replace("_", "")
        .replace("-", "")
        .upper()
        )# ============================================================
# دریافت کندل‌ها
# ============================================================

def get_klines(symbol, interval, limit=KLINE_LIMIT):

    api_symbol = normalize_symbol(symbol)

    params = {
        "symbol": api_symbol,
        "interval": interval,
        "limit": limit,
    }

    data = api_get(
        KLINE_URLS,
        params=params
    )

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
# اندیکاتورها
# ============================================================

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

    rs = avg_gain / avg_loss

    return (
        100
        - (100 / (1 + rs))
    )


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        high = current["high"]
        low = current["low"]
        previous_close = previous["close"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return (
        sum(trs[-period:])
        / period
    )


def volume_ratio(
    candles,
    period=20
):

    if len(candles) < period + 1:
        return 1.0

    recent = candles[-1]["volume"]

    previous = [
        candle["volume"]
        for candle
        in candles[
            -period - 1:-1
        ]
    ]

    if not previous:
        return 1.0

    average = (
        sum(previous)
        / len(previous)
    )

    if average <= 0:
        return 1.0

    return recent / average


# ============================================================
# تحلیل یک تایم‌فریم
# ============================================================

def analyze_timeframe(candles):

    if len(candles) < 60:
        return None

    closes = [
        candle["close"]
        for candle in candles
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

    ema12 = ema(
        closes,
        12
    )

    ema26 = ema(
        closes,
        26
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

    if any(
        value is None
        for value in [
            ema9,
            ema21,
            ema50,
            ema12,
            ema26,
            rsi_value,
            atr_value,
        ]
    ):
        return None

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
        and current_price > ema21
    )

    bearish = (
        ema9 < ema21
        and ema21 < ema50
        and current_price < ema21
    )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    buy_score = 0
    buy_reasons = []

    if current_price > ema9:
        buy_score += 15
        buy_reasons.append(
            "قیمت بالای EMA9"
        )

    if ema9 > ema21:
        buy_score += 15
        buy_reasons.append(
            "EMA9 بالای EMA21"
        )

    if ema21 > ema50:
        buy_score += 15
        buy_reasons.append(
            "روند میان‌مدت صعودی"
        )

    if rsi_value >= 50:
        buy_score += 10

    if 50 <= rsi_value <= 68:
        buy_score += 10
        buy_reasons.append(
            "RSI مناسب خرید"
        )

    if macd_value > 0:
        buy_score += 10
        buy_reasons.append(
            "MACD مثبت"
        )

    if vol_ratio >= 1.2:
        buy_score += 10
        buy_reasons.append(
            "افزایش حجم"
        )

    if bullish:
        buy_score += 15
        buy_reasons.append(
            "تأیید روند صعودی"
        )

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    sell_score = 0
    sell_reasons = []

    if current_price < ema9:
        sell_score += 15
        sell_reasons.append(
            "قیمت زیر EMA9"
        )

    if ema9 < ema21:
        sell_score += 15
        sell_reasons.append(
            "EMA9 زیر EMA21"
        )

    if ema21 < ema50:
        sell_score += 15
        sell_reasons.append(
            "روند میان‌مدت نزولی"
        )

    if rsi_value <= 50:
        sell_score += 10

    if 32 <= rsi_value <= 50:
        sell_score += 10
        sell_reasons.append(
            "RSI مناسب فروش"
        )

    if macd_value < 0:
        sell_score += 10
        sell_reasons.append(
            "MACD منفی"
        )

    if vol_ratio >= 1.2:
        sell_score += 10
        sell_reasons.append(
            "افزایش حجم"
        )

    if bearish:
        sell_score += 15
        sell_reasons.append(
            "تأیید روند نزولی"
        )

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
        "buy_reasons": buy_reasons,
        "sell_reasons": sell_reasons,
    }


# ============================================================
# تحلیل همزمان 5 و 15 دقیقه
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

    if not candles_5m:
        return None

    if not candles_15m:
        return None

    analysis_5m = analyze_timeframe(
        candles_5m
    )

    analysis_15m = analyze_timeframe(
        candles_15m
    )

    if not analysis_5m:
        return None

    if not analysis_15m:
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

    difference = abs(
        buy_score
        - sell_score
    )

    if (
        buy_score >= MIN_SIGNAL_SCORE
        and
        buy_score > sell_score
        and
        difference >= MIN_DIRECTION_GAP
    ):

        direction = "BUY"
        score = buy_score

    elif (
        sell_score >= MIN_SIGNAL_SCORE
        and
        sell_score > buy_score
        and
        difference >= MIN_DIRECTION_GAP
    ):

        direction = "SELL"
        score = sell_score

    else:

        direction = "WAIT"

        score = max(
            buy_score,
            sell_score
        )

    # ATR تایم 5 دقیقه برای نقطه ورود
    price = analysis_5m["price"]
    atr_value = analysis_5m["atr"]

    return {
        "symbol": symbol,
        "direction": direction,
        "score": round(
            score,
            2
        ),
        "buy_score": round(
            buy_score,
            2
        ),
        "sell_score": round(
            sell_score,
            2
        ),
        "price": price,
        "atr": atr_value,
        "5m": analysis_5m,
        "15m": analysis_15m,
        "candles_5m": candles_5m,
        "candles_15m": candles_15m,
    }


# ============================================================
# تشخیص الگوی بازار
# ============================================================

def create_pattern_fingerprint(
    analysis
):

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

        (
            "5M_BULL"
            if a5["bullish"]
            else
            "5M_BEAR"
            if a5["bearish"]
            else
            "5M_MIXED"
        ),

        (
            "15M_BULL"
            if a15["bullish"]
            else
            "15M_BEAR"
            if a15["bearish"]
            else
            "15M_MIXED"
        ),

        bucket_rsi(
            a5["rsi"]
        ),

        bucket_rsi(
            a15["rsi"]
        ),

        bucket_volume(
            a5["volume_ratio"]
        ),

        (
            "MACD_POS"
            if a5["macd"] > 0
            else
            "MACD_NEG"
        ),
    ]

    raw = "|".join(parts)

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:20]# ============================================================
# سیستم یادگیری
# ============================================================

def get_learning_data():

    default = {
        "patterns": {},
        "stats": {
            "total": 0,
            "tp1": 0,
            "tp2": 0,
            "sl": 0,
            "ambiguous": 0,
            "unknown": 0,
        }
    }

    data = load_json(
        LEARNING_FILE,
        default
    )

    if not isinstance(data, dict):
        return default

    if "patterns" not in data:
        data["patterns"] = {}

    if "stats" not in data:
        data["stats"] = default["stats"].copy()

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

    history = get_pattern_history(
        pattern_id
    )

    completed = (
        history.get("tp1", 0)
        +
        history.get("tp2", 0)
        +
        history.get("sl", 0)
        +
        history.get("ambiguous", 0)
    )

    if completed < 3:
        return None

    successful = (
        history.get("tp1", 0)
        +
        history.get("tp2", 0)
    )

    return round(
        successful
        / completed
        * 100,
        2
    )


def update_learning(
    signal,
    result
):

    learning = get_learning_data()

    pattern_id = signal[
        "pattern_id"
    ]

    if pattern_id not in learning[
        "patterns"
    ]:

        learning["patterns"][
            pattern_id
        ] = {
            "total": 0,
            "tp1": 0,
            "tp2": 0,
            "sl": 0,
            "ambiguous": 0,
            "unknown": 0,
        }

    pattern = learning[
        "patterns"
    ][pattern_id]

    pattern["total"] += 1

    if result in pattern:
        pattern[result] += 1
    else:
        pattern["unknown"] += 1

    learning["stats"]["total"] += 1

    if result in learning[
        "stats"
    ]:

        learning["stats"][
            result
        ] += 1

    else:

        learning["stats"][
            "unknown"
        ] += 1

    save_json(
        LEARNING_FILE,
        learning
    )


# ============================================================
# اهرم تحلیلی
# ============================================================

def estimate_leverage(
    entry,
    stop_loss,
    score,
    atr_value
):

    if entry <= 0:
        return 1

    distance_percent = (
        abs(entry - stop_loss)
        / entry
        * 100
    )

    if distance_percent <= 0:
        return 1

    # اهرم بر اساس فاصله حد ضرر
    leverage = (
        TARGET_MARGIN_LOSS_AT_SL
        / distance_percent
    )

    # تعدیل بر اساس قدرت سیگنال
    if score >= 90:
        leverage *= 1.20

    elif score >= 80:
        leverage *= 1.10

    elif score < 75:
        leverage *= 0.85

    leverage = max(
        1,
        min(
            leverage,
            MAX_ANALYTICAL_LEVERAGE
        )
    )

    # گرد کردن به اعداد کاربردی
    choices = [
        1, 2, 3, 5,
        7, 10, 12,
        15, 20
    ]

    selected = min(
        choices,
        key=lambda x:
        abs(x - leverage)
    )

    return selected


# ============================================================
# نوسان بازار
# ============================================================

def volatility_level(
    price,
    atr_value
):

    if price <= 0 or atr_value <= 0:
        return {
            "level": "UNKNOWN",
            "percent": 0.0
        }

    atr_percent = (
        atr_value
        / price
        * 100
    )

    if atr_percent < 0.30:
        level = "LOW"

    elif atr_percent < 0.70:
        level = "NORMAL"

    elif atr_percent < 1.20:
        level = "HIGH"

    else:
        level = "VERY_HIGH"

    return {
        "level": level,
        "percent": round(
            atr_percent,
            3
        )
    }


# ============================================================
# ساخت سیگنال
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

    elif direction == "SELL":

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

    else:
        return None

    volatility = volatility_level(
        price,
        atr_value
    )

    leverage = estimate_leverage(
        price,
        stop_loss,
        analysis["score"],
        atr_value
    )

    if direction == "BUY":

        reasons = (
            analysis["5m"]["buy_reasons"]
            +
            analysis["15m"]["buy_reasons"]
        )

    else:

        reasons = (
            analysis["5m"]["sell_reasons"]
            +
            analysis["15m"]["sell_reasons"]
        )

    # حذف علت‌های تکراری
    reasons = list(
        dict.fromkeys(reasons)
    )

    return {

        "id": hashlib.sha256(
            (
                analysis["symbol"]
                + direction
                + str(time.time())
            ).encode()
        ).hexdigest()[:16],

        "created_at": now_iso(),

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

        "entry":
            price,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "stop_loss":
            stop_loss,

        "atr":
            atr_value,

        "volatility":
            volatility,

        "analytical_leverage":
            leverage,

        "pattern_id":
            pattern_id,

        "historical_pattern_score":
            history_score,

        "reasons":
            reasons,

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

    direction = signal[
        "direction"
    ]

    if direction == "BUY":
        side = "🟢 خرید / Long"

    else:
        side = "🔴 فروش / Short"

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

    reasons = signal.get(
        "reasons",
        []
    )

    if reasons:

        reason_text = "\n".join(
            f"• {reason}"
            for reason in reasons[:8]
        )

    else:

        reason_text = (
            "شرایط تکنیکال همسو شده است."
        )

    volatility = signal.get(
        "volatility",
        {}
    )

    return (
        "🚨 سیگنال تحلیل Futures تبدیل\n\n"

        f"🪙 ارز: {signal['symbol']}\n"
        f"📊 جهت: {side}\n\n"

        f"💰 ورود: {signal['entry']}\n"
        f"🎯 TP1: {signal['tp1']}\n"
        f"🎯 TP2: {signal['tp2']}\n"
        f"🛑 SL: {signal['stop_loss']}\n\n"

        f"📈 امتیاز سیگنال: "
        f"{signal['score']}%\n"

        f"🟢 BUY: "
        f"{signal['buy_score']}%\n"

        f"🔴 SELL: "
        f"{signal['sell_score']}%\n\n"

        f"⚡ اهرم تحلیلی پیشنهادی: "
        f"{signal['analytical_leverage']}x\n"

        f"🌊 نوسان: "
        f"{volatility.get('level', 'UNKNOWN')}\n"

        f"📐 ATR نوسان: "
        f"{volatility.get('percent', 0)}%\n\n"

        f"🧠 سابقه همین الگو: "
        f"{history_text}\n\n"

        "📌 علت سیگنال:\n"
        f"{reason_text}\n\n"

        "⏱ تایم‌فریم‌ها: 5m + 15m\n"

        "⚠️ اهرم بالا ریسک بالایی دارد. "
        "عدد اهرم بالا فقط تحلیل ربات است "
        "و به معنی اهرم واقعی حساب تبدیل نیست.\n\n"

        "🤖 وضعیت: فقط تحلیل؛ "
        "فعلاً هیچ معامله‌ای باز نمی‌شود."
    )


# ============================================================
# ذخیره سیگنال‌ها
# ============================================================

def load_signals():

    data = load_json(
        SIGNALS_FILE,
        []
    )

    if not isinstance(data, list):
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
# بررسی تکراری بودن
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
            "status"
        ) not in [
            "OPEN",
            "TP1",
        ]:

            continue

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
# ارزیابی نتیجه سیگنال
# ============================================================

def evaluate_signal(
    signal
):

    symbol = signal[
        "symbol"
    ]

    direction = signal[
        "direction"
    ]

    candles = get_klines(
        symbol,
        "5m",
        100
    )

    if not candles:
        return None

    entry = safe_float(
        signal.get("entry")
    )

    tp1 = safe_float(
        signal.get("tp1")
    )

    tp2 = safe_float(
        signal.get("tp2")
    )

    sl = safe_float(
        signal.get("stop_loss")
    )

    for candle in candles:

        candle_time = (
            candle["open_time"]
            / 1000
        )

        if candle_time <= (
            signal.get(
                "created_timestamp",
                0
            )
        ):
            continue

        high = candle["high"]
        low = candle["low"]

        if direction == "BUY":

            hit_tp2 = high >= tp2
            hit_tp1 = high >= tp1
            hit_sl = low <= sl

        else:

            hit_tp2 = low <= tp2
            hit_tp1 = low <= tp1
            hit_sl = high >= sl

        # اگر در یک کندل TP و SL هر دو لمس شوند
        if (
            hit_sl
            and
            (
                hit_tp1
                or hit_tp2
            )
        ):

            return "ambiguous"

        if hit_tp2:
            return "tp2"

        if hit_tp1:
            return "tp1"

        if hit_sl:
            return "sl"

    return None


# ============================================================
# تغییر وضعیت
# ============================================================

def update_signal_status(
    signal,
    result
):

    if result == "tp1":

        if signal.get(
            "status"
        ) == "OPEN":

            signal["status"] = "TP1"
            signal["result"] = "tp1"
            signal["result_at"] = now_iso()

            return True

        return False

    if result in [
        "tp2",
        "sl",
        "ambiguous",
    ]:

        if signal.get(
            "status"
        ) in [
            "OPEN",
            "TP1",
        ]:

            signal["status"] = (
                result.upper()
            )

            signal["result"] = result

            signal["result_at"] = (
                now_iso()
            )

            return True

    return False


# ============================================================
# آمار
# ============================================================

def statistics():

    signals = load_signals()

    tp1 = 0
    tp2 = 0
    sl = 0
    ambiguous = 0
    open_count = 0

    for signal in signals:

        status = signal.get(
            "status"
        )

        if status == "TP1":
            tp1 += 1

        elif status == "TP2":
            tp2 += 1

        elif status == "SL":
            sl += 1

        elif status == "AMBIGUOUS":
            ambiguous += 1

        elif status == "OPEN":
            open_count += 1

    completed = (
        tp1
        + tp2
        + sl
        + ambiguous
    )

    if completed > 0:

        accuracy = round(
            (
                tp1 + tp2
            )
            / completed
            * 100,
            2
        )

    else:

        accuracy = None

    return {

        "total_completed":
            completed,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "sl":
            sl,

        "ambiguous":
            ambiguous,

        "open":
            open_count,

        "accuracy":
            accuracy,
    }


# ============================================================
# اسکن بازار
# ============================================================

def scan_market():

    print("\n")
    print("=" * 60)
    print(
        "TABDEAL FUTURES "
        "5-SYMBOL SCANNER"
    )
    print("=" * 60)

    symbols = TARGET_SYMBOLS

    print(
        "\nارزهای انتخاب‌شده:"
    )

    for symbol in symbols:
        print(
            " -",
            symbol
        )

    signals = load_signals()

    new_signals = []

    # --------------------------------------------------------
    # بررسی سیگنال‌های قبلی
    # --------------------------------------------------------

    print(
        "\n=== بررسی نتایج قبلی ==="
    )

    for signal in signals:

        if signal.get(
            "status"
        ) not in [
            "OPEN",
            "TP1",
        ]:

            continue

        try:

            result = evaluate_signal(
                signal
            )

            if not result:
                continue

            print(
                "RESULT:",
                signal["symbol"],
                signal["direction"],
                result
            )

            old_result = signal.get(
                "result"
            )

            changed = (
                update_signal_status(
                    signal,
                    result
                )
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
                        "نتیجه نهایی هنوز "
                        "در حال بررسی است."
                    )

                elif result == "tp2":

                    send_telegram(
                        "✅ TP2 تکمیل شد\n\n"
                        f"🪙 {signal['symbol']}\n"
                        f"📊 {signal['direction']}\n"
                        "الگوی موفق ذخیره شد."
                    )

                elif result == "sl":

                    send_telegram(
                        "🛑 حد ضرر فعال شد\n\n"
                        f"🪙 {signal['symbol']}\n"
                        f"📊 {signal['direction']}\n"
                        "نتیجه برای یادگیری ذخیره شد."
        )
