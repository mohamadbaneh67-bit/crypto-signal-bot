import os
import time
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

# ================= CONFIG =================

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"

# فقط 5 ارز
TOP5 = ["BTC", "ETH", "BNB", "SOL", "XRP"]

# اول USDT، اگر موجود نبود IRT
PREFERRED_QUOTES = ["USDT", "IRT"]

MAX_SIGNALS = 2
MIN_SIGNAL_SCORE = 85

# حداقل ATR نسبت به قیمت
MIN_ATR_PERCENT = 0.12

# حداقل حرکت اخیر برای اینکه بازار خیلی بی‌حرکت نباشد
MIN_RECENT_MOVE_PERCENT = 0.10

TRADE_LIMIT = 1000
REQUEST_TIMEOUT = 20
REQUEST_SLEEP = 0.20

HISTORY_FILE = "signals_history.json"
HISTORY_MAX = 500
SIGNAL_TTL_HOURS = 24

SESSION = requests.Session()


# ================= TABDEAL API =================

def tabdeal_get(endpoint, params=None):
    r = SESSION.get(
        f"{TABDEAL_BASE}/{endpoint}",
        params=params or {},
        timeout=REQUEST_TIMEOUT
    )
    r.raise_for_status()
    return r.json()


def get_tabdeal_markets():
    data = tabdeal_get("exchangeInfo")

    if isinstance(data, dict):
        markets = data.get(
            "symbols",
            data.get("data", data.get("result", []))
        )
    else:
        markets = data

    if not isinstance(markets, list):
        return {}

    result = {}

    for m in markets:
        if not isinstance(m, dict):
            continue

        status = str(
            m.get("status", "TRADING")
        ).upper()

        if status != "TRADING":
            continue

        base = m.get("baseAsset") or m.get("base")
        quote = m.get("quoteAsset") or m.get("quote")

        symbol = (
            m.get("symbol")
            or m.get("pair")
            or m.get("market")
        )

        tabdeal_symbol = (
            m.get("tabdealSymbol")
            or m.get("tabdeal_symbol")
            or symbol
        )

        if not base or not quote or not symbol:
            continue

        base = str(base).upper()
        quote = str(quote).upper()
        symbol = str(symbol).upper()
        tabdeal_symbol = str(tabdeal_symbol).upper()

        if base not in TOP5:
            continue

        if quote not in PREFERRED_QUOTES:
            continue

        result[(base, quote)] = {
            "symbol": symbol,
            "tabdeal_symbol": tabdeal_symbol,
            "base": base,
            "quote": quote
        }

    return result


def choose_market(markets, base):
    # اول USDT
    for quote in PREFERRED_QUOTES:
        m = markets.get((base, quote))
        if m:
            return m

    return None


def get_trades(symbol, tabdeal_symbol=None):
    candidates = []

    for x in [symbol, tabdeal_symbol]:
        if x:
            x = str(x).upper()
            if x not in candidates:
                candidates.append(x)

    last_error = None

    for candidate in candidates:
        try:
            data = tabdeal_get(
                "trades",
                {
                    "symbol": candidate,
                    "limit": TRADE_LIMIT
                }
            )

            if isinstance(data, dict):
                data = data.get(
                    "data",
                    data.get("result", [])
                )

            if isinstance(data, list):
                return data

        except Exception as e:
            last_error = e

    if last_error:
        raise last_error

    return []


# ================= TRADE DATA =================

def get_value(t, *keys):
    for key in keys:
        if t.get(key) is not None:
            return t[key]
    return None


def trade_time_ms(t):
    value = get_value(
        t,
        "time",
        "timestamp",
        "T",
        "createdAt",
        "created_at"
    )

    if value is None:
        return None

    try:
        value = float(value)

        if value < 10_000_000_000:
            return int(value * 1000)

        return int(value)

    except Exception:
        return None


def trades_to_candles(trades, minutes):
    rows = []

    for t in trades:
        if not isinstance(t, dict):
            continue

        price = get_value(t, "price", "p")
        qty = get_value(
            t,
            "qty",
            "quantity",
            "q",
            "amount",
            "volume"
        )

        ts = trade_time_ms(t)

        if price is None or ts is None:
            continue

        try:
            rows.append(
                (
                    pd.to_datetime(
                        ts,
                        unit="ms",
                        utc=True
                    ),
                    float(price),
                    float(qty or 0)
                )
            )
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=["time", "price", "volume"]
    )

    df = (
        df.sort_values("time")
        .set_index("time")
    )

    candles = df["price"].resample(
        f"{minutes}min"
    ).ohlc()

    candles["volume"] = df["volume"].resample(
        f"{minutes}min"
    ).sum()

    candles = candles.dropna(
        subset=["open", "high", "low", "close"]
    )

    return candles.reset_index()


# ================= INDICATORS =================

def ema(series, period):
    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(
        lower=0
    ).ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    loss = (
        -delta.clip(upper=0)
    ).ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = gain / loss.replace(0, np.nan)

    return 100 - (
        100 / (1 + rs)
    )


def atr(df, period=14):
    previous_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()


def add_indicators(df):
    df = df.copy()

    df["ema20"] = ema(
        df["close"], 20
    )

    df["ema50"] = ema(
        df["close"], 50
    )

    df["rsi"] = rsi(
        df["close"]
    )

    df["atr"] = atr(df)

    macd_line = (
        ema(df["close"], 12)
        - ema(df["close"], 26)
    )

    df["macd_hist"] = (
        macd_line
        - ema(macd_line, 9)
    )

    df["vol_ma"] = (
        df["volume"].rolling(20).mean()
    )

    return df


# ================= ANALYSIS =================

def analyze_market(market, trades):
    d5 = trades_to_candles(trades, 5)
    d15 = trades_to_candles(trades, 15)

    if len(d5) < 60 or len(d15) < 30:
        return None

    d5 = add_indicators(d5)
    d15 = add_indicators(d15)

    # آخرین کندل کامل‌شده
    a5 = d5.iloc[-2]
    a15 = d15.iloc[-2]

    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []

    # ---------- 15 دقیقه ----------

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
            "RSI مثبت"
        )

    if a15["rsi"] < 50:
        short_score += 15
        short_reasons.append(
            "RSI منفی"
        )

    if a15["macd_hist"] > 0:
        long_score += 20
        long_reasons.append(
            "MACD مثبت"
        )

    if a15["macd_hist"] < 0:
        short_score += 20
        short_reasons.append(
            "MACD منفی"
        )

    # ---------- 5 دقیقه ----------

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

    if (
        pd.notna(a5["vol_ma"])
        and a5["volume"] > a5["vol_ma"] * 1.2
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

    # ---------- نوسان ----------

    entry = float(a5["close"])
    av = float(a5["atr"])

    if not np.isfinite(av) or av <= 0:
        return None

    atr_percent = (
        av / entry
    ) * 100

    if atr_percent < MIN_ATR_PERCENT:
        return None

    # ---------- حرکت اخیر ----------

    previous_close = float(
        d5.iloc[-7]["close"]
    )

    recent_move = (
        abs(entry - previous_close)
        / previous_close
    ) * 100

    if recent_move < MIN_RECENT_MOVE_PERCENT:
        return None

    # ---------- شکست کوتاه‌مدت ----------

    recent_high = float(
        d5.iloc[-7:-2]["high"].max()
    )

    recent_low = float(
        d5.iloc[-7:-2]["low"].min()
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

    # ---------- انتخاب جهت ----------

    if (
        long_score >= MIN_SIGNAL_SCORE
        and long_score > short_score
    ):
        direction = "LONG"
        score = min(100, int(long_score))
        reasons = long_reasons

    elif (
        short_score >= MIN_SIGNAL_SCORE
        and short_score > long_score
    ):
        direction = "SHORT"
        score = min(100, int(short_score))
        reasons = short_reasons

    else:
        return None

    # ---------- سطوح معامله ----------

    if direction == "LONG":
        stop = entry - 1.2 * av
        risk = entry - stop
        tp1 = entry + 1.5 * risk
        tp2 = entry + 2.5 * risk

    else:
        stop = entry + 1.2 * av
        risk = stop - entry
        tp1 = entry - 1.5 * risk
        tp2 = entry - 2.5 * risk

    timestamps = [
        trade_time_ms(t)
        for t in trades
        if isinstance(t, dict)
    ]

    timestamps = [
        x for x in timestamps
        if x is not None
    ]

    signal_time_ms = (
        max(timestamps)
        if timestamps
        else int(
            datetime.now(
                timezone.utc
            ).timestamp() * 1000
        )
    )

    return {
        "base": market["base"],
        "quote": market["quote"],
        "symbol": market["symbol"],
        "tabdeal_symbol": market["tabdeal_symbol"],
        "direction": direction,
        "score": score,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "atr_percent": atr_percent,
        "recent_move": recent_move,
        "signal_time_ms": signal_time_ms,
        "reasons": reasons
    }


# ================= HISTORY =================

def load_history():
    try:
        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):
        pass

    return []


def save_history(history):
    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            history[-HISTORY_MAX:],
            f,
            ensure_ascii=False,
            indent=2
        )


# ================= LIVE RESULT CHECK =================

def evaluate_open_signals(history, markets):
    completed = []

    for signal in history:

        if signal.get("status") != "OPEN":
            continue

        base = signal.get("base")
        quote = signal.get("quote")

        market = markets.get(
            (base, quote)
        )

        if not market:
            continue

        try:
            trades = get_trades(
                market["symbol"],
                market["tabdeal_symbol"]
            )
        except Exception:
            continue

        signal_time = int(
            signal["signal_time_ms"]
        )

        direction = signal["direction"]

        result = None

        for trade in sorted(
            trades,
            key=lambda x: trade_time_ms(x) or 0
        ):

            ts = trade_time_ms(trade)
            price = get_value(
                trade,
                "price",
                "p"
            )

            if (
                ts is None
                or price is None
                or ts <= signal_time
            ):
                continue

            try:
                price = float(price)
            except Exception:
                continue

            if direction == "LONG":

                if price <= signal["stop"]:
                    result = (
                        "SL",
                        price,
                        ts
                    )
                    break

                if price >= signal["tp2"]:
                    result = (
                        "TP2",
                        price,
                        ts
                    )
                    break

                if price >= signal["tp1"]:
                    result = (
                        "TP1",
                        price,
                        ts
                    )
                    break

            else:

                if price >= signal["stop"]:
                    result = (
                        "SL",
                        price,
                        ts
                    )
                    break

                if price <= signal["tp2"]:
                    result = (
                        "TP2",
                        price,
                        ts
                    )
                    break

                if price <= signal["tp1"]:
                    result = (
                        "TP1",
                        price,
                        ts
                    )
                    break

        if result:

            status, hit_price, hit_time = result

            signal["status"] = status
            signal["hit_price"] = hit_price
            signal["closed_time_ms"] = hit_time

            if status in ("TP1", "TP2"):
                signal["result"] = "درست"
            else:
                signal["result"] = "نادرست"

            completed.append(signal)

            continue

        age = (
            datetime.now(timezone.utc)
            - datetime.fromtimestamp(
                signal_time / 1000,
                tz=timezone.utc
            )
        )

        if age > timedelta(
            hours=SIGNAL_TTL_HOURS
        ):

            signal["status"] = "EXPIRED"
            signal["result"] = "نامشخص"

            completed.append(signal)

    return completed


# ================= STATISTICS =================

def calculate_stats(history):
    finished = [
        x for x in history
        if x.get("status") in (
            "TP1",
            "TP2",
            "SL"
        )
    ]

    wins = sum(
        1
        for x in finished
        if x.get("result") == "درست"
    )

    losses = sum(
        1
        for x in finished
        if x.get("result") == "نادرست"
    )

    total = wins + losses

    winrate = (
        (wins / total) * 100
        if total
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "winrate": winrate
    }


# ================= MESSAGE =================

def fmt(value):
    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:,.4f}"

    if value >= 0.01:
        return f"{value:,.6f}"

    return f"{value:.10f}"


def make_signal_message(signal):
    reasons = "\n".join(
        "✅ " + x
        for x in signal["reasons"]
    )

    when = datetime.fromtimestamp(
        signal["signal_time_ms"] / 1000,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    return (
        "📢 سیگنال تحلیل تبدیل\n\n"
        f"🪙 {signal['base']}/{signal['quote']}\n"
        f"📌 جهت: "
        f"{'خرید (LONG)' if signal['direction'] == 'LONG' else 'فروش (SHORT)'}\n"
        "⏱ تایم‌فریم: 5m + 15m\n\n"
        f"📍 ورود: {fmt(signal['entry'])}\n"
        f"🛑 حد ضرر: {fmt(signal['stop'])}\n"
        f"🎯 TP1: {fmt(signal['tp1'])}\n"
        f"🎯 TP2: {fmt(signal['tp2'])}\n"
        f"📊 امتیاز: {signal['score']}/100\n"
        f"📈 ATR: {signal['atr_percent']:.2f}%\n"
        f"📉 حرکت اخیر: {signal['recent_move']:.2f}%\n"
        f"🕐 زمان: {when}\n\n"
        f"دلایل:\n{reasons}\n\n"
        "⚠️ فقط تحلیل است؛ معامله خودکار انجام نمی‌شود."
    )


def make_result_message(signal):
    labels = {
        "TP1": "✅ درست — TP1",
        "TP2": "✅ درست — TP2",
        "SL": "❌ نادرست — حد ضرر",
        "EXPIRED": "⚪ نامشخص — منقضی شد"
    }

    return (
        "📊 نتیجه سیگنال\n\n"
        f"🪙 {signal['base']}/{signal['quote']}\n"
        f"📌 جهت: "
        f"{'خرید' if signal['direction'] == 'LONG' else 'فروش'}\n"
        f"{labels.get(signal['status'], signal['status'])}\n"
        f"💵 ورود: {fmt(signal['entry'])}\n"
        f"🎯 قیمت نتیجه: "
        f"{fmt(signal.get('hit_price', signal['entry']))}"
    )


def make_stats_message(history):
    stats = calculate_stats(history)

    return (
        "📊 آمار عملکرد ربات\n\n"
        f"🔢 معاملات نتیجه‌دار: {stats['total']}\n"
        f"✅ درست: {stats['wins']}\n"
        f"❌ نادرست: {stats['losses']}\n"
        f"📈 نرخ برد: {stats['winrate']:.1f}%\n\n"
        "⚠️ این آمار از سیگنال‌های واقعی ثبت‌شده "
        "روی داده‌های تبدیل محاسبه می‌شود."
    )


# ================= TELEGRAM =================

def send_telegram(text):
    r = SESSION.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=REQUEST_TIMEOUT
    )

    r.raise_for_status()


# ================= MAIN =================

def main():

    print("شروع اسکن تبدیل...")

    markets = get_tabdeal_markets()

    history = load_history()

    # بررسی نتایج سیگنال‌های قبلی
    completed = evaluate_open_signals(
        history,
        markets
    )

    for signal in completed:

        try:
            send_telegram(
                make_result_message(signal)
            )
        except Exception as e:
            print(
                "خطا در ارسال نتیجه:",
                e
            )

    save_history(history)

    # جلوگیری از چند سیگنال همزمان برای یک ارز
    open_bases = {
        x.get("base")
        for x in history
        if x.get("status") == "OPEN"
    }

    signals = []

    # فقط 5 ارز
    for base in TOP5:

        if base in open_bases:
            continue

        market = choose_market(
            markets,
            base
        )

        if not market:
            print(
                f"{base}: بازار مناسب در تبدیل پیدا نشد."
            )
            continue

        try:

            trades = get_trades(
                market["symbol"],
                market["tabdeal_symbol"]
            )

            if not trades:
                continue

            signal = analyze_market(
                market,
                trades
            )

          
