"""
ربات تحلیل سیگنال Tabdeal
- فقط داده عمومی Tabdeal
- ساخت کندل 5 دقیقه‌ای از معاملات اخیر
- تحلیل 5 و 15 دقیقه
- ذخیره سابقه کندل و سیگنال
- بک‌تست داخلی
- ارسال سیگنال به Telegram
- بدون معامله خودکار
"""

import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError("متغیر TELEGRAM_BOT_TOKEN در GitHub Secrets تنظیم نشده است.")
if not CHAT_ID:
    raise RuntimeError("متغیر TELEGRAM_CHAT_ID در GitHub Secrets تنظیم نشده است.")

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"
REQUEST_TIMEOUT = 20
SESSION = requests.Session()

TOP5 = ["BTC", "ETH", "BNB", "SOL", "XRP"]
QUOTE = "USDT"
TRADE_LIMIT = 1000
BASE_CANDLE_MINUTES = 5

CANDLE_STORE_FILE = "candle_store.json"
SIGNALS_HISTORY_FILE = "signals_history.json"
STORE_MAX_DAYS = 60

BACKTEST_HORIZON_BARS = 48
BACKTEST_MIN_TRADES = 10
SIGNAL_SCORE_MIN = 65
SIGNAL_TTL_HOURS = 24
SIGNALS_HISTORY_MAX = 500


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
        trade, "time", "timestamp", "T",
        "createdAt", "created_at", "timeMs"
    )
    value = as_float(value)
    if value is None:
        return None
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


def empty_candles():
    return pd.DataFrame(
        columns=["time", "open", "high", "low", "close", "volume"]
    )


def tabdeal_get(endpoint, params=None):
    url = f"{TABDEAL_BASE}/{endpoint.lstrip('/')}"
    response = SESSION.get(
        url, params=params or {}, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def unwrap_data(data):
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
                {"symbol": candidate, "limit": TRADE_LIMIT},
            )
            trades = unwrap_data(data)
            if isinstance(trades, list):
                return trades
        except requests.RequestException as exc:
            last_error = exc

    if last_error:
        raise last_error

    return []


def trades_to_candles(trades, minutes=5):
    rows = []

    for trade in trades:
        if not isinstance(trade, dict):
            continue

        price = as_float(get_value(trade, "price", "p"))
        quantity = as_float(
            get_value(
                trade, "qty", "quantity", "q",
                "amount", "volume"
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
        return empty_candles()

    df = pd.DataFrame(
        rows, columns=["time", "price", "volume"]
    )

    df = (
        df.sort_values("time")
        .drop_duplicates(
            subset=["time", "price", "volume"]
        )
        .set_index("time")
    )

    candles = df["price"].resample(
        f"{minutes}min"
    ).ohlc()

    candles["volume"] = df["volume"].resample(
        f"{minutes}min"
    ).sum()

    return (
        candles.dropna(
            subset=["open", "high", "low", "close"]
        ).reset_index()
    )


def load_store():
    try:
        with open(
            CANDLE_STORE_FILE, "r", encoding="utf-8"
        ) as file:
            raw = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    store = {}

    for base, rows in raw.items():
        if not isinstance(rows, list) or not rows:
            store[str(base).upper()] = empty_candles()
            continue

        try:
            df = pd.DataFrame(rows)
            required = {
                "time", "open", "high",
                "low", "close", "volume"
            }

            if not required.issubset(df.columns):
                store[str(base).upper()] = empty_candles()
                continue

            df["time"] = pd.to_datetime(
                df["time"], utc=True, errors="coerce"
            )

            for column in [
                "open", "high", "low",
                "close", "volume"
            ]:
                df[column] = pd.to_numeric(
                    df[column], errors="coerce"
                )

            df = (
                df.dropna(
                    subset=[
                        "time", "open", "high",
                        "low", "close"
                    ]
                )
                .sort_values("time")
                .drop_duplicates(
                    subset="time", keep="last"
                )
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

    with open(
        temp_file, "w", encoding="utf-8"
    ) as file:
        json.dump(
            raw, file, ensure_ascii=False
        )

    os.replace(temp_file, CANDLE_STORE_FILE)


def load_signal_history():
    try:
        with open(
            SIGNALS_HISTORY_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    return data if isinstance(data, list) else []


def save_signal_history(history):
    history = history[-SIGNALS_HISTORY_MAX:]
    temp_file = f"{SIGNALS_HISTORY_FILE}.tmp"

    with open(
        temp_file, "w", encoding="utf-8"
    ) as file:
        json.dump(
            history,
            file,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(temp_file, SIGNALS_HISTORY_FILE)


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

    for column in [
        "open", "high", "low",
        "close", "volume"
    ]:
        merged[column] = pd.to_numeric(
            merged[column], errors="coerce"
        )

    merged = (
        merged.dropna(
            subset=[
                "time", "open", "high",
                "low", "close"
            ]
        )
        .drop_duplicates(
            subset="time", keep="last"
        )
        .sort_values("time")
    )

    cutoff = (
        pd.Timestamp.now(tz="UTC")
        - pd.Timedelta(days=STORE_MAX_DAYS)
    )

    return merged[
        merged["time"] >= cutoff
    ].reset_index(drop=True)


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

    rs = avg_gain / avg_loss.replace(
        0, np.nan
    )

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

    result["ema20"] = ema(
        result["close"], 20
    )
    result["ema50"] = ema(
        result["close"], 50
    )
    result["rsi"] = rsi(
        result["close"]
    )
    result["atr"] = atr(result)

    macd_line = (
        ema(result["close"], 12)
        - ema(result["close"], 26)
    )

    macd_signal = ema(
        macd_line, 9
    )

    result["macd_hist"] = (
        macd_line - macd_signal
    )

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

    df = df.dropna(
        subset=["time"]
    ).sort_values("time")

    data = df.set_index("time")

    candles = data["close"].resample(
        f"{minutes}min"
    ).ohlc()

    candles["volume"] = (
        data["volume"].resample(
            f"{minutes}min"
        ).sum()
    )

    return (
        candles.dropna(
            subset=[
                "open", "high",
                "low", "close"
            ]
        ).reset_index()
    )


def build_timeframes(df5m):
    m5 = add_indicators(
        resample_ohlc(df5m, 5)
    )

    m15 = add_indicators(
        resample_ohlc(df5m, 15)
    )

    if m5.empty or m15.empty:
        return pd.DataFrame()

    m15 = m15.add_prefix(
        "m15_"
    ).rename(
        columns={"m15_time": "time"}
    )

    return pd.merge_asof(
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

    m15_up = (
        row["m15_ema20"]
        > row["m15_ema50"]
    )

    m15_down = (
        row["m15_ema20"]
        < row["m15_ema50"]
    )

    buy_score = 0
    sell_score = 0
    buy_reasons = []
    sell_reasons = []

    if m15_up:
        buy_score += 20
        buy_reasons.append(
            "روند ۱۵ دقیقه‌ای صعودی"
        )

    if m15_down:
        sell_score += 20
        sell_reasons.append(
            "روند ۱۵ دقیقه‌ای نزولی"
        )

    if row["m15_rsi"] > 50:
        buy_score += 15
        buy_reasons.append(
            "RSI ۱۵ دقیقه‌ای مثبت"
        )
    elif row["m15_rsi"] < 50:
        sell_score += 15
        sell_reasons.append(
            "RSI ۱۵ دقیقه‌ای منفی"
        )

    if row["m15_macd_hist"] > 0:
        buy_score += 15
        buy_reasons.append(
            "MACD ۱۵ دقیقه‌ای مثبت"
        )
    elif row["m15_macd_hist"] < 0:
        sell_score += 15
        sell_reasons.append(
            "MACD ۱۵ دقیقه‌ای منفی"
        )

    if row["close"] > row["ema20"]:
        buy_score += 15
        buy_reasons.append(
            "قیمت ۵ دقیقه‌ای بالای EMA20"
        )
    elif row["close"] < row["ema20"]:
        sell_score += 15
        sell_reasons.append(
            "قیمت ۵ دقیقه‌ای زیر EMA20"
        )

    if row["rsi"] >= 52:
        buy_score += 10
        buy_reasons.append(
            "مومنتوم ۵ دقیقه‌ای صعودی"
        )
    elif row["rsi"] <= 48:
        sell_score += 10
        sell_reasons.append(
            "مومنتوم ۵ دقیقه‌ای نزولی"
        )

    if row["macd_hist"] > 0:
        buy_score += 10
        buy_reasons.append(
            "MACD ۵ دقیقه‌ای مثبت"
        )
    elif row["macd_hist"] < 0:
        sell_score += 10
        sell_reasons.append(
            "MACD ۵ دقیقه‌ای منفی"
        )

    if (
        pd.notna(row["vol_ma"])
        and row["vol_ma"] > 0
        and row["volume"]
        > row["vol_ma"] * 1.2
    ):
        if row["close"] > row["open"]:
            buy_score += 15
            buy_reasons.append(
                "افزایش حجم همراه با کندل صعودی"
            )
        elif row["close"] < row["open"]:
            sell_score += 15
            sell_reasons.append(
                "افزایش حجم همراه با کندل نزولی"
            )

    if (
        buy_score >= SIGNAL_SCORE_MIN
        and buy_score > sell_score
    ):
        return (
            "BUY",
            buy_score,
            buy_reasons,
        )

    if (
        sell_score >= SIGNAL_SCORE_MIN
        and sell_score > buy_score
    ):
        return (
            "SELL",
            sell_score,
            sell_reasons,
        )

    return None


def build_trade_levels(
    entry,
    atr_value,
    direction,
):
    entry = float(entry)
    atr_value = float(atr_value)

    if atr_value <= 0:
        return None

    risk = 1.2 * atr_value

    if direction == "BUY":
        return (
            entry - risk,
            entry + 1.5 * risk,
            entry + 2.5 * risk,
        )

    return (
        entry + risk,
        entry - 1.5 * risk,
        entry - 2.5 * risk,
    )


def run_backtest(merged):
    if merged is None or len(merged) < 100:
        return {
            "total_signals": 0,
            "resolved": 0,
            "wins": 0,
            "losses": 0,
            "winrate": 0.0,
        }

    rows = merged.to_dict(
        "records"
    )

    trades = []
    i = 0
    n = len(rows)
    busy_until = -1

    while i < n:
        if i <= busy_until:
            i += 1
            continue

        signal = evaluate_row(
            rows[i]
        )

        if signal is None:
            i += 1
            continue

        direction, _, _ = signal

        entry = as_float(
            rows[i]["close"]
        )

        atr_value = as_float(
            rows[i]["atr"]
        )

        if (
            entry is None
            or atr_value is None
            or atr_value <= 0
        ):
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

        for k in range(
            i + 1,
            end + 1
        ):
            high = as_float(
                rows[k]["high"]
            )

            low = as_float(
                rows[k]["low"]
            )

            if (
                high is None
                or low is None
            ):
                continue

            if direction == "BUY":
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
        1
        for item in trades
        if item in ("TP1", "TP2")
    )

    losses = sum(
        1
        for item in trades
        if item == "SL"
    )

    resolved = wins + losses

    return {
        "total_signals": len(trades),
        "resolved": resolved,
        "wins": wins,
        "losses": losses,
        "winrate": (
            round(
                wins / resolved,
                3,
            )
            if resolved
            else 0.0
        ),
    }


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
    )

    total = backtest.get(
        "total_signals", 0
    )

    resolved = backtest.get(
        "resolved", 0
    )

    winrate = (
        backtest.get(
            "winrate", 0.0
        ) * 100
    )

    if resolved >= BACKTEST_MIN_TRADES:
        bt_status = (
            f"بک‌تست: {resolved} معامله تعیین‌تکلیف‌شده، "
            f"نرخ برد {winrate:.1f}%"
        )
    else:
        bt_status = (
            f"بک‌تست: سابقه کافی نیست "
            f"({resolved} معامله تعیین‌تکلیف‌شده)"
        )

    return (
        f"📊 سیگنال Tabdeal\n\n"
        f"ارز: {base}/{QUOTE}\n"
        f"نوع: {direction_text}\n"
        f"امتیاز: {score}/100\n\n"
        f"ورود: {fmt_price(entry)}\n"
        f"حد ضرر: {fmt_price(stop)}\n"
        f"حد سود ۱: {fmt_price(tp1)}\n"
