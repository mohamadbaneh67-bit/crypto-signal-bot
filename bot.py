"""
ربات تحلیل سیگنال Tabdeal - نسخه اصلاح‌شده
------------------------------------------
ویژگی‌ها:
- فقط داده عمومی خود Tabdeal؛ بدون Binance
- دریافت معاملات اخیر Tabdeal و ساخت کندل 5 دقیقه‌ای
- تحلیل 5 دقیقه و 15 دقیقه
- ذخیره آرشیو محلی برای ساخته‌شدن سابقه در اجرای مداوم
- بک‌تست داخلی برای سنجش نرخ برد سیگنال‌ها
- ارسال سیگنال خرید/فروش به Telegram
- پیگیری نتیجه سیگنال‌های باز
- بدون انجام معامله خودکار

نکته مهم برای GitHub Actions:
فایل‌های candle_store.json و signals_history.json باید بین اجرای Jobها
حفظ شوند (مثلاً با commit خودکار در workflow). در غیر این صورت هر اجرای
GitHub Actions از صفر شروع می‌کند و سابقه ساخته نمی‌شود.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests


# =========================
# CONFIG
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError("متغیر TELEGRAM_BOT_TOKEN در GitHub Secrets تنظیم نشده است.")
if not CHAT_ID:
    raise RuntimeError("متغیر TELEGRAM_CHAT_ID در GitHub Secrets تنظیم نشده است.")

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"
REQUEST_TIMEOUT = 20
SESSION = requests.Session()

# ارزهایی که فعلاً بررسی می‌شوند
TOP5 = ["BTC", "ETH", "BNB", "SOL", "XRP"]
QUOTE = "USDT"

# داده خام
TRADE_LIMIT = 1000
BASE_CANDLE_MINUTES = 5

# آرشیو
CANDLE_STORE_FILE = "candle_store.json"
SIGNALS_HISTORY_FILE = "signals_history.json"
STORE_MAX_DAYS = 60

# حداقل سابقه لازم برای شروع تحلیل
# برای اندیکاتورهای 15 دقیقه‌ای چند روز کافی است.
MIN_HISTORY_DAYS = 3

# بک‌تست
BACKTEST_HORIZON_BARS = 48       # 48 کندل 15 دقیقه‌ای = 12 ساعت
BACKTEST_MIN_TRADES = 10
BACKTEST_MIN_WINRATE = 0.55

# حداقل امتیاز سیگنال
SIGNAL_SCORE_MIN = 65

# هر سیگنال حداکثر چند ساعت باز بماند
SIGNAL_TTL_HOURS = 24

# حداکثر تعداد سیگنال ذخیره‌شده
SIGNALS_HISTORY_MAX = 500


# =========================
# HELPERS
# =========================

def utc_now():
    return datetime.now(timezone.utc)


def as_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def get_value(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return None


def trade_time_ms(trade):
    value = get_value(
        trade,
        "time",
        "timestamp",
        "T",
        "createdAt",
        "created_at",
        "timeMs",
    )
    value = as_float(value)
    if value is None:
        return None

    # اگر timestamp بر حسب ثانیه بود، به میلی‌ثانیه تبدیل کن.
    if value < 10_000_000_000:
        value *= 1000

    return int(value)


def fmt_price(value):
    value = float(value)
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    if value >= 0.01:
        return f"{value:,.6f}"
    return f"{value:.10f}"


# =========================
# TABDEAL API
# =========================

def tabdeal_get(endpoint, params=None):
    url = f"{TABDEAL_BASE}/{endpoint.lstrip('/')}"
    response = SESSION.get(
        url,
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def unwrap_data(data):
    """پاسخ‌های مختلف API را به لیست تبدیل می‌کند."""
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("data", "result", "symbols", "trades"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    return []


def get_tabdeal_markets():
    data = tabdeal_get("exchangeInfo")
    markets = unwrap_data(data)

    result = {}

    for market in markets:
        if not isinstance(market, dict):
            continue

        status = str(market.get("status", "TRADING")).upper()
        if status != "TRADING":
            continue

        base = str(market.get("baseAsset") or "").upper()
        quote = str(market.get("quoteAsset") or "").upper()
        symbol = str(market.get("symbol") or "").upper()
        tabdeal_symbol = str(
            market.get("tabdealSymbol") or symbol
        ).upper()

        if base and quote and symbol:
            result[(base, quote)] = {
                "symbol": symbol,
                "tabdeal_symbol": tabdeal_symbol,
            }

    return result


def get_trades(symbol, tabdeal_symbol=None):
    """
    معاملات اخیر عمومی Tabdeal.
    طبق API عمومی، داده‌ها recent trades هستند و آرشیو تاریخی
    از خود API دریافت نمی‌شود؛ بنابراین آرشیو باید با اجرای منظم ساخته شود.
    """
    candidates = []

    for value in (symbol, tabdeal_symbol):
        if value:
            value = str(value).upper()
            if value not in candidates:
                candidates.append(value)

    last_error = None

    for candidate in candidates:
        try:
            data = tabdeal_get(
                "trades",
                {
                    "symbol": candidate,
                    "limit": TRADE_LIMIT,
                },
            )
            trades = unwrap_data(data)

            if isinstance(trades, list):
                return trades

        except requests.RequestException as exc:
            last_error = exc
            continue

    if last_error:
        raise last_error

    return []


def get_last_price(symbol, tabdeal_symbol=None):
    try:
        trades = get_trades(symbol, tabdeal_symbol)
    except requests.RequestException:
        return None

    values = []

    for trade in trades:
        if not isinstance(trade, dict):
            continue

        ts = trade_time_ms(trade)
        price = as_float(get_value(trade, "price", "p"))

        if ts is not None and price is not None and price > 0:
            values.append((ts, price))

    if not values:
        return None

    values.sort(key=lambda x: x[0])
    return values[-1][1]


# =========================
# TRADE -> 5M CANDLES
# =========================

def trades_to_candles(trades, minutes=5):
    rows = []

    for trade in trades:
        if not isinstance(trade, dict):
            continue

        price = as_float(get_value(trade, "price", "p"))
        quantity = as_float(
            get_value(
                trade,
                "qty",
                "quantity",
                "q",
                "amount",
                "volume",
            )
        )
        timestamp = trade_time_ms(trade)

        if price is None or timestamp is None or price <= 0:
            continue

        try:
            rows.append(
                (
                    pd.to_datetime(timestamp, unit="ms", utc=True),
                    price,
                    quantity or 0.0,
                )
            )
        except (ValueError, TypeError):
            continue

    if not rows:
        return pd.DataFrame(
            columns=["time", "open", "high", "low", "close", "volume"]
        )

    df = pd.DataFrame(
        rows,
        columns=["time", "price", "volume"],
    )

    df = (
        df.sort_values("time")
        .drop_duplicates(subset=["time", "price", "volume"])
        .set_index("time")
    )

    candles = df["price"].resample(f"{minutes}min").ohlc()
    candles["volume"] = df["volume"].resample(f"{minutes}min").sum()

    candles = candles.dropna(
        subset=["open", "high", "low", "close"]
    )

    return candles.reset_index()


# =========================
# LOCAL STORAGE
# =========================

def load_store():
    try:
        with open(CANDLE_STORE_FILE, "r", encoding="utf-8") as file:
            raw = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    store = {}

    for base, rows in raw.items():
        if not isinstance(rows, list) or not rows:
            store[base] = empty_candles()
            continue

        try:
            df = pd.DataFrame(rows)

            required = {"time", "open", "high", "low", "close", "volume"}
            if not required.issubset(df.columns):
                store[base] = empty_candles()
                continue

            df["time"] = pd.to_datetime(
                df["time"],
                utc=True,
                errors="coerce",
            )

            for column in ["open", "high", "low", "close", "volume"]:
                df[column] = pd.to_numeric(
                    df[column],
                    errors="coerce",
                )

            df = (
                df.dropna(subset=["time", "open", "high", "low", "close"])
                .sort_values("time")
                .drop_duplicates(subset="time", keep="last")
                .reset_index(drop=True)
            )

            store[str(base).upper()] = df

        except Exception:
            store[str(base).upper()] = empty_candles()

    return store


def save_store(store):
    raw = {}

    for base, df in store.items():
        if df is None or df.empty:
            raw[base] = []
            continue

        out = df.copy()
        out["time"] = out["time"].astype(str)
        raw[base] = out.to_dict("records")

    temp_file = f"{CANDLE_STORE_FILE}.tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(raw, file, ensure_ascii=False)

    os.replace(temp_file, CANDLE_STORE_FILE)


def empty_candles():
    return pd.DataFrame(
        columns=["time", "open", "high", "low", "close", "volume"]
    )


def merge_into_store(existing, new_candles):
    if existing is None or existing.empty:
        merged = new_candles.copy()
    elif new_candles is None or new_candles.empty:
        merged = existing.copy()
    else:
        merged = pd.concat(
            [existing, new_candles],
            ignore_index=True,
        )

    if merged.empty:
        return empty_candles()

    merged["time"] = pd.to_datetime(
        merged["time"],
        utc=True,
        errors="coerce",
    )

    for column in ["open", "high", "low", "close", "volume"]:
        merged[column] = pd.to_numeric(
            merged[column],
            errors="coerce",
        )

    merged = (
        merged.dropna(subset=["time", "open", "high", "low", "close"])
        .drop_duplicates(subset="time", keep="last")
        .sort_values("time")
    )

    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(
        days=STORE_MAX_DAYS
    )

    return merged[
        merged["time"] >= cutoff
    ].reset_index(drop=True)


# =========================
# INDICATORS
# =========================

def ema(series, period):
    return series.ewm(
        span=period,
        adjust=False,
        min_periods=period,
    ).mean()


def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    return 100 - (100 / (1 + rs))


def atr(df, period=14):
    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def add_indicators(df):
    result = df.copy()

    result["ema20"] = ema(result["close"], 20)
    result["ema50"] = ema(result["close"], 50)
    result["rsi"] = rsi(result["close"])
    result["atr"] = atr(result)

    macd_line = (
        ema(result["close"], 12)
        - ema(result["close"], 26)
    )

    macd_signal = ema(macd_line, 9)
    result["macd_hist"] = macd_line - macd_signal

    result["vol_ma"] = (
        result["volume"]
        .rolling(20, min_periods=20)
        .mean()
    )

    return result


def resample_ohlc(df5m, minutes):
    if df5m is None or df5m.empty:
        return empty_candles()

    df = df5m.copy()
    df["time"] = pd.to_datetime(
        df["time"],
        utc=True,
        errors="coerce",
    )

    df = df.dropna(subset=["time"]).sort_values("time")

    data = df.set_index("time")

    candles = data["close"].resample(
        f"{minutes}min"
    ).ohlc()

    candles["volume"] = data["volume"].resample(
        f"{minutes}min"
    ).sum()

    return (
        candles.dropna(
            subset=["open", "high", "low", "close"]
        )
        .reset_index()
    )


def build_timeframes(df5m):
    """
    تایم‌فریم‌های مورد استفاده:
    - 5 دقیقه برای ورود و مومنتوم
    - 15 دقیقه برای روند
    """
    m5 = add_indicators(
        resample_ohlc(df5m, 5)
    )

    m15 = add_indicators(
        resample_ohlc(df5m, 15)
    )

    if m5.empty or m15.empty:
        return pd.DataFrame()

    m15 = m15.add_prefix("m15_").rename(
        columns={"m15_time": "time"}
    )

    merged = pd.merge_asof(
        m5.sort_values("time"),
        m15[
            [
                "time",
                "m15_ema20",
                "m15_ema50",
                "m15_rsi",
                "m15_macd_hist",
            ]
        ].sort_values("time"),
        on="time",
        direction="backward",
    )

    return merged


# =========================
# SIGNAL LOGIC
# =========================

def evaluate_row(row):
    required = [
        "m15_ema20",
        "m15_ema50",
        "m15_rsi",
        "m15_macd_hist",
        "ema20",
        "ema50",
        "rsi",
        "macd_hist",
        "vol_ma",
        "atr",
        "close",
        "open",
        "volume",
    ]

    if any(
        pd.isna(row.get(column))
        for column in required
    ):
        return None

    m15_up = row["m15_ema20"] > row["m15_ema50"]
    m15_down = row["m15_ema20"] < row["m15_ema50"]

    buy_score = 0
    sell_score = 0
    buy_reasons = []
    sell_reasons = []

    # 15 دقیقه
    if m15_up:
        buy_score += 20
        buy_reasons.append("روند ۱۵ دقیقه‌ای صعودی")

    if m15_down:
        sell_score += 20
        sell_reasons.append("روند ۱۵ دقیقه‌ای نزولی")

    # RSI 15m
    if row["m15_rsi"] > 50:
        buy_score += 15
        buy_reasons.append("RSI ۱۵ دقیقه‌ای مثبت")
    elif row["m15_rsi"] < 50:
        sell_score += 15
        sell_reasons.append("RSI ۱۵ دقیقه‌ای منفی")

    # MACD 15m
    if row["m15_macd_hist"] > 0:
        buy_score += 15
        buy_reasons.append("MACD ۱۵ دقیقه‌ای مثبت")
    elif row["m15_macd_hist"] < 0:
        sell_score += 15
        sell_reasons.append("MACD ۱۵ دقیقه‌ای منفی")

    # 5m
    if row["close"] > row["ema20"]:
        buy_score += 15
        buy_reasons.append("قیمت ۵ دقیقه‌ای بالای EMA20")
    elif row["close"] < row["ema20"]:
        sell_score += 15
        sell_reasons.append("قیمت ۵ دقیقه‌ای زیر EMA20")

    if row["rsi"] >= 52:
        buy_score += 10
        buy_reasons.append("مومنتوم ۵ دقیقه‌ای صعودی")
    elif row["rsi"] <= 48:
        sell_score += 10
        sell_reasons.append("مومنتوم ۵ دقیقه‌ای نزولی")

    if row["macd_hist"] > 0:
        buy_score += 10
        buy_reasons.append("MACD ۵ دقیقه‌ای مثبت")
    elif row["macd_hist"] < 0:
        sell_score += 10
        sell_reasons.append("MACD ۵ دقیقه‌ای منفی")

    # حجم
    if (
        pd.notna(row["vol_ma"])
        and row["vol_ma"] > 0
        and row["volume"] > row["vol_ma"] * 1.2
    ):
        if row["close"] > row["open"]:
            buy_score += 15
            buy_reasons.append("افزایش حجم همراه با کندل صعودی")
        elif row["close"] < row["open"]:
            sell_score += 15
            sell_reasons.append("افزایش حجم همراه با کندل نزولی")

    if buy_score >= SIGNAL_SCORE_MIN and buy_score > sell_score:
        return "BUY", buy_score, buy_reasons

    if sell_score >= SIGNAL_SCORE_MIN and sell_score > buy_score:
        return "SELL", sell_score, sell_reasons

    return None


# =========================
# LEVELS
# =========================

def build_trade_levels(entry, atr_value, direction):
    entry = float(entry)
    atr_value = float(atr_value)

    if atr_value <= 0:
        return None

    risk = 1.2 * atr_value

    if direction == "BUY":
        stop = entry - risk
        tp1 = entry + 1.5 * risk
        tp2 = entry + 2.5 * risk
    else:
        stop = entry + risk
        tp1 = entry - 1.5 * risk
        tp2 = entry - 2.5 * risk

    return stop, tp1, tp2


# =========================
# BACKTEST
# =========================

def run_backtest(merged):
    if merged is None or len(merged) < 100:
        return {
            "total_signals": 0,
            "resolved": 0,
            "wins": 0,
            "losses": 0,
            "winrate": 0.0,
        }

    rows = merged.to_dict("records")
    trades = []

    i = 0
    n = len(rows)
    busy_until = -1

    while i < n:
        if i <= busy_until:
            i += 1
            continue

        row = rows[i]
        signal = evaluate_row(row)

        if signal is None:
            i += 1
            continue

        direction, _, _ = signal
        entry = as_float(row["close"])
        atr_value = as_float(row["atr"])

        if entry is None or atr_value is None or atr_value <= 0:
            i += 1
            continue

        levels = build_trade_levels(
            entry,
            atr_value,
            direction,
        )

        if levels is None:
            i += 1
            continue

        stop, tp1, tp2 = levels

        outcome = "EXPIRED"
        end = min(
            i + BACKTEST_HORIZON_BARS,
            n - 1,
        )

        for k in range(i + 1, end + 1):
            high = as_float(rows[k]["high"])
            low = as_float(rows[k]["low"])

            if high is None or low is None:
                continue

            if direction == "BUY":
                # در صورت برخورد همزمان، SL را محافظه‌کارانه در نظر می‌گیریم.
                if low <= stop:
                    outcome = "SL"
                    break
                if high >= tp2:
                    outcome = "TP2"
                    break
                if high >= tp1:
                    outcome = "TP1"
                    break

            else:
                if high >= stop:
                    outcome = "SL"
                    break
                if low <= tp2:
                    outcome = "TP2"
                    break
                if low <= tp1:
                    outcome = "TP1"
                    break

        trades.append(outcome)
        busy_until = end
        i += 1

    wins = sum(
        1 for item in trades
        if item in ("TP1", "TP2")
    )

    losses = sum(
        1 for item in trades
        if item == "SL"
    )

    resolved = wins + losses

    return {
        "total_signals": len(trades),
        "resolved": resolved,
        "wins": wins,
        "losses": losses,
        "winrate": (
            round(wins / resolved, 3)
            if resolved
            else 0.0
        ),
    }


# =========================
# TELEGRAM
# =========================

def send_telegram(message):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = SESSION.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()


def make_signal_message(
    base,
    direction,
    entry,
    stop,
    tp1,
    tp2,
    score,
    reasons,
    backtest,
    history_days,
):
    direction_text = (
        "خرید (BUY)"
        if direction == "BUY"
        else "فروش (SELL)"
    )

    reasons_text = "\n".join(
        f"✅ {reason}"
        for reason in reasons
   
