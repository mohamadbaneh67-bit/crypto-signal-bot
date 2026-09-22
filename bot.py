import os
import time
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN در GitHub Secrets تنظیم نشده است."
    )

if not CHAT_ID:
    raise RuntimeError(
        "TELEGRAM_CHAT_ID در GitHub Secrets تنظیم نشده است."
    )


TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"

TOP5 = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "XRP",
]

PREFERRED_QUOTES = [
    "USDT"
]

TRADE_LIMIT = 1000
REQUEST_TIMEOUT = 20
REQUEST_SLEEP = 0.20

MIN_SIGNAL_SCORE = 85
MAX_SIGNALS = 2

MIN_ATR_PERCENT = 0.12
MIN_RECENT_MOVE_PERCENT = 0.10

HISTORY_FILE = "signals_history.json"
HISTORY_MAX = 500

SESSION = requests.Session()


def tabdeal_get(endpoint, params=None):
    response = SESSION.get(
        f"{TABDEAL_BASE}/{endpoint}",
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def telegram_send(text):
    response = SESSION.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text,
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()


def get_value(obj, *keys):
    if not isinstance(obj, dict):
        return None

    for key in keys:
        value = obj.get(key)

        if value is not None:
            return value

    return None


def as_float(value):
    try:
        if value is None or value == "":
            return None

        return float(value)

    except (TypeError, ValueError):
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

    if value < 10_000_000_000:
        value *= 1000

    return int(value)


def extract_list(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in (
            "symbols",
            "data",
            "result",
            "items",
        ):
            value = data.get(key)

            if isinstance(value, list):
                return value

    return []


def get_markets():
    data = tabdeal_get(
        "exchangeInfo"
    )

    raw_markets = extract_list(
        data
    )

    markets = {}

    for item in raw_markets:

        if not isinstance(item, dict):
            continue

        status = str(
            item.get(
                "status",
                "TRADING",
            )
        ).upper()

        if status not in (
            "TRADING",
            "ENABLED",
            "ACTIVE",
        ):
            continue

        base = (
            item.get("baseAsset")
            or item.get("base")
        )

        quote = (
            item.get("quoteAsset")
            or item.get("quote")
        )

        symbol = (
            item.get("symbol")
            or item.get("pair")
            or item.get("market")
        )

        tabdeal_symbol = (
            item.get("tabdealSymbol")
            or item.get("tabdeal_symbol")
            or symbol
        )

        if not base or not quote or not symbol:
            continue

        base = str(base).upper()
        quote = str(quote).upper()
        symbol = str(symbol).upper()
        tabdeal_symbol = str(
            tabdeal_symbol
        ).upper()

        if base not in TOP5:
            continue

        if quote not in PREFERRED_QUOTES:
            continue

        markets[
            (base, quote)
        ] = {
            "base": base,
            "quote": quote,
            "symbol": symbol,
            "tabdeal_symbol": tabdeal_symbol,
        }

    return markets


def choose_market(markets, base):

    for quote in PREFERRED_QUOTES:

        market = markets.get(
            (base, quote)
        )

        if market:
            return market

    return None


def get_trades(market):

    candidates = []

    for symbol in (
        market.get("symbol"),
        market.get("tabdeal_symbol"),
    ):

        if symbol:
            symbol = str(
                symbol
            ).upper()

            if symbol not in candidates:
                candidates.append(
                    symbol
                )

    last_error = None

    for symbol in candidates:

        try:

            data = tabdeal_get(
                "trades",
                {
                    "symbol": symbol,
                    "limit": TRADE_LIMIT,
                },
            )

            trades = extract_list(
                data
            )

            if trades:
                return trades

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    return []


def trades_to_candles(
    trades,
    minutes,
):

    rows = []

    for trade in trades:

        if not isinstance(
            trade,
            dict,
        ):
            continue

        price = as_float(
            get_value(
                trade,
                "price",
                "p",
            )
        )

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

        timestamp = trade_time_ms(
            trade
        )

        if (
            price is None
            or timestamp is None
        ):
            continue

        rows.append(
            (
                pd.to_datetime(
                    timestamp,
                    unit="ms",
                    utc=True,
                ),
                price,
                quantity or 0.0,
            )
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=[
            "time",
            "price",
            "volume",
        ],
    )

    df = df.sort_values(
        "time"
    )

    df = df.set_index(
        "time"
    )

    candles = df[
        "price"
    ].resample(
        f"{minutes}min"
    ).ohlc()

    candles["volume"] = df[
        "volume"
    ].resample(
        f"{minutes}min"
    ).sum()

    candles = candles.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    return candles.reset_index()


def ema(
    series,
    period,
):

    return series.ewm(
        span=period,
        adjust=False,
    ).mean()


def rsi(
    series,
    period=14,
):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    ).ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    loss = (
        -delta.clip(
            upper=0
        )
    ).ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    rs = gain / loss.replace(
        0,
        np.nan,
    )

    return 100 - (
        100 / (1 + rs)
    )


def atr(
    df,
    period=14,
):

    previous_close = (
        df["close"].shift(1)
    )

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (
                df["high"]
                - previous_close
            ).abs(),
            (
                df["low"]
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()


def add_indicators(df):

    df = df.copy()

    df["ema20"] = ema(
        df["close"],
        20,
    )

    df["ema50"] = ema(
        df["close"],
        50,
    )

    df["rsi"] = rsi(
        df["close"]
    )

    df["atr"] = atr(
        df
    )

    macd_line = (
        ema(
            df["close"],
            12,
        )
        -
        ema(
            df["close"],
            26,
        )
    )

    df["macd_hist"] = (
        macd_line
        -
        ema(
            macd_line,
            9,
        )
    )

    df["vol_ma"] = (
        df["volume"].rolling(
            20
        ).mean()
    )

    return df


def analyze_market(
    market,
    trades,
):

    candles_5 = trades_to_candles(
        trades,
        5,
    )

    candles_15 = trades_to_candles(
        trades,
        15,
    )

    if len(candles_5) < 60:
        return None

    if len(candles_15) < 30:
        return None

    candles_5 = add_indicators(
        candles_5
    )

    candles_15 = add_indicators(
        candles_15
    )

    # آخرین کندل ممکن است هنوز کامل نشده باشد.
    a5 = candles_5.iloc[-2]
    a15 = candles_15.iloc[-2]

    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []

    # =========================
    # 15 دقیقه
    # =========================

    if a15["ema20"] > a15["ema50"]:

        long_score += 20

        long_reasons.append(
            "روند 15 دقیقه صعودی"
        )

    if a15["ema20"] < a15["ema50"]:

        short_score += 20

        short_reasons.append(
            "روند 15 دقیقه نزولی"
        )

    if a15["rsi"] > 50:

        long_score += 15

        long_reasons.append(
            "RSI 15 دقیقه مثبت"
        )

    if a15["rsi"] < 50:

        short_score += 15

        short_reasons.append(
            "RSI 15 دقیقه منفی"
        )

    if a15["macd_hist"] > 0:

        long_score += 20

        long_reasons.append(
            "MACD 15 دقیقه مثبت"
        )

    if a15["macd_hist"] < 0:

        short_score += 20

        short_reasons.append(
            "MACD 15 دقیقه منفی"
        )

    # =========================
    # 5 دقیقه
    # =========================

    if a5["close"] > a5["ema20"]:

        long_score += 15

        long_reasons.append(
            "قیمت 5 دقیقه بالای EMA20"
        )

    if a5["close"] < a5["ema20"]:

        short_score += 15

        short_reasons.append(
            "قیمت 5 دقیقه زیر EMA20"
        )

    if a5["rsi"] >= 52:

        long_score += 15

        long_reasons.append(
            "مومنتوم 5 دقیقه‌ای صعودی"
        )

    if a5["rsi"] <= 48:

        short_score += 15

        short_reasons.append(
            "مومنتوم 5 دقیقه‌ای نزولی"
        )

    # =========================
    # حجم
    # =========================

    if (
        pd.notna(a5["vol_ma"])
        and a5["vol_ma"] > 0
        and a5["volume"]
        > a5["vol_ma"] * 1.2
    ):

        if a5["close"] > a5["open"]:

            long_score += 15

            long_reasons.append(
                "حجم بالاتر از میانگین"
            )

        elif a5["close"] < a5["open"]:

            short_score += 15

            short_reasons.append(
                "حجم بالاتر از میانگین"
            )

    # =========================
    # ATR
    # =========================

    entry = as_float(
        a5["close"]
    )

    current_atr = as_float(
        a5["atr"]
    )

    if entry is None:
        return None

    if current_atr is None:
        return None

    if not np.isfinite(
        current_atr
    ):
        return None

    if current_atr <= 0:
        return None

    atr_percent = (
        current_atr / entry
    ) * 100

    if (
        atr_percent
        < MIN_ATR_PERCENT
    ):
        return None

    # =========================
    # حرکت اخیر
    # =========================

    previous_close = as_float(
        candles_5.iloc[-7][
            "close"
        ]
    )

    if previous_close is None:
        return None

    recent_move = (
        abs(
            entry
            - previous_close
        )
        / previous_close
    ) * 100

    if (
        recent_move
        < MIN_RECENT_MOVE_PERCENT
    ):
        return None

    # =========================
    # شکست کوتاه مدت
    # =========================

    recent_high = float(
        candles_5.iloc[-7:-2][
            "high"
        ].max()
    )

    recent_low = float(
        candles_5.iloc[-7:-2][
            "low"
        ].min()
    )

    if entry > recent_high:

        long_score += 10

        long_reasons.append(
            "شکست سقف کوتاه‌مدت"
        )

    if entry < recent_low:

        short_score += 10

        short_reasons.append(
            "شکست کف کوتاه‌مدت"
        )

    # =========================
    # تعیین جهت
    # =========================

    if (
        long_score
        >= MIN_SIGNAL_SCORE
        and long_score
        > short_score
    ):

        direction = "LONG"

        score = min(
            100,
            int(long_score),
        )

        reasons = long_reasons

    elif (
        short_score
        >= MIN_SIGNAL_SCORE
        and short_score
        > long_score
    ):

        direction = "SHORT"

        score = min(
            100,
            int(short_score),
        )

        reasons = short_reasons

    else:

        return None

    # =========================
    # حد ضرر و اهداف
    # =========================

    if direction == "LONG":

        stop = (
            entry
            - 1.2 * current_atr
        )

        risk = (
            entry - stop
        )

        tp1 = (
            entry
            + 1.5 * risk
        )

        tp2 = (
            entry
            + 2.5 * risk
        )

    else:

        stop = (
            entry
            + 1.2 * current_atr
        )

        risk = (
            stop - entry
        )

        tp1 = (
            entry
            - 1.5 * risk
        )

        tp2 = (
            entry
            - 2.5 * risk
        )

    timestamps = [
        trade_time_ms(trade)
        for trade in trades
        if isinstance(
            trade,
            dict,
        )
    ]

    timestamps = [
        value
        for value in timestamps
        if value is not None
    ]

    if timestamps:

        signal_time_ms = max(
            timestamps
        )

    else:

        signal_time_ms = int(
            datetime.now(
                timezone.utc
            ).timestamp()
            * 1000
        )

    return {
        "base": market["base"],
        "quote": market["quote"],
        "symbol": market["symbol"],
        "tabdeal_symbol": market[
            "tabdeal_symbol"
        ],
        "direction": direction,
        "score": score,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "atr_percent": atr_percent,
        "recent_move": recent_move,
        "signal_time_ms": signal_time_ms,
        "reasons": reasons,
    }


def load_history():

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(
                file
            )

        if isinstance(
            data,
            list,
        ):
            return data

    except (
        FileNotFoundError,
        json.JSONDecodeError,
    ):

        pass

    return []


def save_history(history):

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            history[-HISTORY_MAX:],
            file,
            ensure_ascii=False,
            indent=2,
        )


def evaluate_open_signals(
    history,
    markets,
):

    completed = []

    for signal in history:

        if signal.get(
            "status"
        ) != "OPEN":
            continue

        base = signal.get(
            "base"
        )

        quote = signal.get(
            "quote"
        )

        market = markets.get(
            (
                base,
                quote,
            )
        )

        if not market:
            continue

        try:

            trades = get_trades(
                market
            )

        except Exception as exc:

            print(
                f"{base}: خطا در بررسی نتیجه: {exc}"
            )

            continue

        if not trades:
            continue

        signal_time = int(
            signal[
                "signal_time_ms"
            ]
        )

        timestamps = [
            trade_time_ms(trade)
            for trade in trades
            if isinstance(
                trade,
                dict,
            )
        ]

        timestamps = [
            value
            for value in timestamps
            if value is not None
        ]

        if not timestamps:
            continue

        oldest_trade = min(
            timestamps
        )

        newest_trade = max(
            timestamps
        )

        # اگر قدیمی‌ترین معامله فعلی
        # بعد از زمان سیگنال باشد،
        # داده کافی برای بررسی کامل نداریم.

        if (
            oldest_trade
            > signal_time
        ):

            signal["status"] = (
                "UNKNOWN"
            )

            signal["result"] = (
                "نامشخص"
            )

            signal["note"] = (
                "داده 1000 معامله اخیر "
                "برای بررسی کل بازه کافی نبود."
            )

            completed.append(
                signal
            )

            continue

        direction = signal.get(
            "direction"
        )

        stop = as_float(
            signal.get("stop")
        )

        tp1 = as_float(
            signal.get("tp1")
        )

        tp2 = as_float(
            signal.get("tp2")
        )

        result = None

        ordered_trades = sorted(
            trades,
            key=lambda trade:
                trade_time_ms(
                    trade
                ) or 0,
        )

        for trade in ordered_trades:

            timestamp = trade_time_ms(
                trade
            )

            price = as_float(
                get_value(
                    trade,
                    "price",
                    "p",
                )
            )

            if (
                timestamp is None
                or price is None
                or timestamp
                <= signal_time
            ):
                continue

            if direction == "LONG":

                if price <= stop:

                    result = (
                        "SL",
                        price,
                        timestamp,
                    )

                    break

                if price >= tp2:

                    result = (
                        "TP2",
                        price,
                        timestamp,
                    )

                    break

                if price >= tp1:

                    result = (
                        "TP1",
                        price,
                        timestamp,
                    )

                    break

            else:

                if price >= stop:

                    result = (
                        "SL",
                        price,
                        timestamp,
                    )

                    break

                if price <= tp2:

                    result = (
                        "TP2",
                        price,
                        timestamp,
                    )

                    break

                if price <= tp1:

                    result = (
                        "TP1",
                        price,
                        timestamp,
                    )

                    break

        if result:

            status, hit_price, hit_time = (
                result
            )

            signal["status"] = (
                status
            )

            signal["hit_price"] = (
                hit_price
            )

            signal[
                "closed_time_ms"
            ] = hit_time

            if status in (
                "TP1",
                "TP2",
            ):

                signal["result"] = (
         
