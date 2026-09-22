import os
import json
import time
import base64
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests


# =========================================================
# تنظیمات اصلی
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
GITHUB_REF_NAME = os.getenv("GITHUB_REF_NAME", "main")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN در GitHub Secrets تنظیم نشده است.")

if not CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID در GitHub Secrets تنظیم نشده است.")


TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"

# فقط این 5 ارز
TOP5 = ["BTC", "ETH", "BNB", "SOL", "XRP"]

QUOTE = "USDT"

TRADE_LIMIT = 1000
TIMEOUT = 20

# حداقل امتیاز سیگنال
MIN_SCORE = 85

# حداکثر تعداد سیگنال در هر اجرا
MAX_SIGNALS = 2

# فیلتر ATR
MIN_ATR_PERCENT = 0.12

# حداقل حرکت اخیر
MIN_MOVE_PERCENT = 0.10

# فایل تاریخچه
HISTORY_FILE = "signals_history.json"
HISTORY_MAX = 500


S = requests.Session()


# =========================================================
# API عمومی Tabdeal
# =========================================================

def api(endpoint, params=None):
    url = f"{TABDEAL_BASE}/{endpoint}"

    r = S.get(
        url,
        params=params or {},
        timeout=TIMEOUT,
    )

    r.raise_for_status()
    return r.json()


# =========================================================
# Telegram
# =========================================================

def telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    r = S.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
        },
        timeout=TIMEOUT,
    )

    if not r.ok:
        print("خطای Telegram:")
        print(r.text)

    r.raise_for_status()

    print("✅ پیام Telegram با موفقیت ارسال شد.")


# =========================================================
# ابزارهای داده
# =========================================================

def val(d, *keys):
    if not isinstance(d, dict):
        return None

    for k in keys:
        if d.get(k) is not None:
            return d[k]

    return None


def num(x):
    try:
        if x is None or x == "":
            return None

        return float(x)

    except (TypeError, ValueError):
        return None


def time_ms(t):
    x = num(
        val(
            t,
            "time",
            "timestamp",
            "T",
            "createdAt",
            "created_at",
            "timeMs",
        )
    )

    if x is None:
        return None

    if x < 10_000_000_000:
        x *= 1000

    return int(x)


def items(data):

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        for k in (
            "symbols",
            "data",
            "result",
            "items",
        ):
            if isinstance(data.get(k), list):
                return data[k]

    return []


# =========================================================
# پیدا کردن بازارهای Tabdeal
# =========================================================

def markets():

    out = {}

    try:
        data = api("exchangeInfo")
    except Exception as e:
        print(f"❌ خطا در دریافت exchangeInfo: {e}")
        return out

    all_items = items(data)

    print(f"📡 تعداد بازارهای دریافت‌شده از Tabdeal: {len(all_items)}")

    for m in all_items:

        if not isinstance(m, dict):
            continue

        status = str(
            m.get("status", "TRADING")
        ).upper()

        if status not in (
            "TRADING",
            "ENABLED",
            "ACTIVE",
        ):
            continue

        base = str(
            m.get("baseAsset")
            or m.get("base")
            or ""
        ).upper()

        quote = str(
            m.get("quoteAsset")
            or m.get("quote")
            or ""
        ).upper()

        symbol = str(
            m.get("symbol")
            or m.get("pair")
            or m.get("market")
            or ""
        ).upper()

        td_symbol = str(
            m.get("tabdealSymbol")
            or m.get("tabdeal_symbol")
            or symbol
        ).upper()

        if (
            base in TOP5
            and quote == QUOTE
            and symbol
        ):

            out[(base, quote)] = {
                "base": base,
                "quote": quote,
                "symbol": symbol,
                "tabdeal_symbol": td_symbol,
            }

    return out


# =========================================================
# دریافت معاملات Tabdeal
# =========================================================

def trades(market):

    last_error = None

    symbols = []

    if market.get("symbol"):
        symbols.append(market["symbol"])

    if (
        market.get("tabdeal_symbol")
        and market["tabdeal_symbol"]
        not in symbols
    ):
        symbols.append(market["tabdeal_symbol"])

    for symbol in symbols:

        try:

            print(
                f"📥 دریافت معاملات "
                f"{market['base']}/{market['quote']} "
                f"با symbol={symbol}"
            )

            data = api(
                "trades",
                {
                    "symbol": symbol,
                    "limit": TRADE_LIMIT,
                },
            )

            got = items(data)

            if got:
                print(
                    f"✅ {market['base']}: "
                    f"{len(got)} معامله دریافت شد."
                )

                return got

            print(
                f"⚠️ {market['base']}: "
                f"برای {symbol} معامله‌ای برنگشت."
            )

        except Exception as e:

            last_error = e

            print(
                f"⚠️ خطا در دریافت "
                f"{market['base']} با {symbol}: {e}"
            )

    if last_error:
        raise last_error

    return []


# =========================================================
# ساخت کندل
# =========================================================

def candles(trade_list, minutes):

    rows = []

    for t in trade_list:

        if not isinstance(t, dict):
            continue

        p = num(
            val(
                t,
                "price",
                "p",
            )
        )

        q = (
            num(
                val(
                    t,
                    "qty",
                    "quantity",
                    "q",
                    "amount",
                    "volume",
                )
            )
            or 0.0
        )

        ts = time_ms(t)

        if p is not None and ts is not None:

            rows.append(
                (
                    pd.to_datetime(
                        ts,
                        unit="ms",
                        utc=True,
                    ),
                    p,
                    q,
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

    df = (
        df
        .sort_values("time")
        .set_index("time")
    )

    c = df["price"].resample(
        f"{minutes}min"
    ).ohlc()

    c["volume"] = (
        df["volume"]
        .resample(f"{minutes}min")
        .sum()
    )

    return (
        c
        .dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .reset_index()
    )


# =========================================================
# اندیکاتورها
# =========================================================

def ema(s, n):
    return s.ewm(
        span=n,
        adjust=False,
    ).mean()


def rsi(s, n=14):

    d = s.diff()

    gain = (
        d.clip(lower=0)
        .ewm(
            alpha=1 / n,
            adjust=False,
        )
        .mean()
    )

    loss = (
        (-d.clip(upper=0))
        .ewm(
            alpha=1 / n,
            adjust=False,
        )
        .mean()
    )

    rs = gain / loss.replace(
        0,
        np.nan,
    )

    return 100 - 100 / (1 + rs)


def atr(df, n=14):

    pc = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - pc).abs(),
            (df["low"] - pc).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / n,
        adjust=False,
    ).mean()


def indicators(df):

    d = df.copy()

    d["ema20"] = ema(
        d["close"],
        20,
    )

    d["ema50"] = ema(
        d["close"],
        50,
    )

    d["rsi"] = rsi(
        d["close"]
    )

    d["atr"] = atr(d)

    macd = (
        ema(d["close"], 12)
        - ema(d["close"], 26)
    )

    d["macd_hist"] = (
        macd
        - ema(macd, 9)
    )

    d["vol_ma"] = (
        d["volume"]
        .rolling(20)
        .mean()
    )

    return d


# =========================================================
# تحلیل
# =========================================================

def analyze(
    market,
    trade_list,
    debug=False,
):

    c5 = candles(
        trade_list,
        5,
    )

    c15 = candles(
        trade_list,
        15,
    )

    print(
        f"📊 {market['base']}: "
        f"معاملات={len(trade_list)} | "
        f"کندل 5m={len(c5)} | "
        f"کندل 15m={len(c15)}"
    )

    if len(c5) < 60 or len(c15) < 30:

        print(
            f"⚠️ {market['base']}: "
            f"داده کافی برای تحلیل وجود ندارد."
        )

        return None

    c5 = indicators(c5)
    c15 = indicators(c15)

    # آخرین کندل بسته‌شده
    a5 = c5.iloc[-2]
    a15 = c15.iloc[-2]

    long_score = 0
    short_score = 0

    lr = []
    sr = []

    # -----------------------------
    # روند 15 دقیقه
    # -----------------------------

    if a15["ema20"] > a15["ema50"]:

        long_score += 20

        lr.append(
            "روند 15 دقیقه صعودی"
        )

    if a15["ema20"] < a15["ema50"]:

        short_score += 20

        sr.append(
            "روند 15 دقیقه نزولی"
        )

    # -----------------------------
    # RSI 15 دقیقه
    # -----------------------------

    if a15["rsi"] > 50:

        long_score += 15

        lr.append(
            "RSI 15 دقیقه مثبت"
        )

    if a15["rsi"] < 50:

        short_score += 15

        sr.append(
            "RSI 15 دقیقه منفی"
        )

    # -----------------------------
    # MACD
    # -----------------------------

    if a15["macd_hist"] > 0:

        long_score += 20

        lr.append(
            "MACD 15 دقیقه مثبت"
        )

    if a15["macd_hist"] < 0:

        short_score += 20

        sr.append(
            "MACD 15 دقیقه منفی"
        )

    # -----------------------------
    # EMA20 در 5 دقیقه
    # -----------------------------

    if a5["close"] > a5["ema20"]:

        long_score += 15

        lr.append(
            "قیمت 5 دقیقه بالای EMA20"
        )

    if a5["close"] < a5["ema20"]:

        short_score += 15

        sr.append(
            "قیمت 5 دقیقه زیر EMA20"
        )

    # -----------------------------
    # مومنتوم
    # -----------------------------

    if a5["rsi"] >= 52:

        long_score += 15

        lr.append(
            "مومنتوم 5 دقیقه‌ای صعودی"
        )

    if a5["rsi"] <= 48:

        short_score += 15

        sr.append(
            "مومنتوم 5 دقیقه‌ای نزولی"
        )

    # -----------------------------
    # حجم
    # -----------------------------

    if (
        pd.notna(a5["vol_ma"])
        and a5["vol_ma"] > 0
        and a5["volume"]
        > a5["vol_ma"] * 1.2
    ):

        if a5["close"] > a5["open"]:

            long_score += 15

            lr.append(
                "حجم بالاتر از میانگین"
            )

        elif a5["close"] < a5["open"]:

            short_score += 15

            sr.append(
                "حجم بالاتر از میانگین"
            )

    # -----------------------------
    # ورود و ATR
    # -----------------------------

    entry = num(a5["close"])
    a = num(a5["atr"])

    if entry is None or a is None or a <= 0:

        print(
            f"⚠️ {market['base']}: "
            f"ATR یا قیمت معتبر نیست."
        )

        return None

    atr_pct = a / entry * 100

    if atr_pct < MIN_ATR_PERCENT:

        print(
            f"⚠️ {market['base']}: "
            f"ATR خیلی کم است: "
            f"{atr_pct:.3f}%"
        )

        return None

    # -----------------------------
    # حرکت اخیر
    # -----------------------------

    old = num(
        c5.iloc[-7]["close"]
    )

    if old is None or old == 0:

        print(
            f"⚠️ {market['base']}: "
            f"قیمت قدیمی معتبر نیست."
        )

        return None

    move = (
        abs(entry - old)
        / old
        * 100
    )

    if move < MIN_MOVE_PERCENT:

        print(
            f"⚠️ {market['base']}: "
            f"حرکت اخیر کافی نیست: "
            f"{move:.3f}%"
        )

        return None

    # -----------------------------
    # شکست سقف / کف
    # -----------------------------

    hi = float(
        c5.iloc[-7:-2]["high"].max()
    )

    lo = float(
        c5.iloc[-7:-2]["low"].min()
    )

    if entry > hi:

        long_score += 10

        lr.append(
            "شکست سقف کوتاه‌مدت"
        )

    if entry < lo:

        short_score += 10

        sr.append(
            "شکست کف کوتاه‌مدت"
        )

    print(
        f"📈 {market['base']}: "
        f"LONG={long_score} | "
        f"SHORT={short_score}"
    )

    # -----------------------------
    # انتخاب سیگنال
    # -----------------------------

    if (
        long_score >= MIN_SCORE
        and long_score > short_score
    ):

        direction = "LONG"
        score = min(
            100,
            int(long_score),
        )
        reasons = lr

    elif (
        short_score >= MIN_SCORE
        and short_score > long_score
    ):

        direction = "SHORT"
        score = min(
            100,
            int(short_score),
        )
        reasons = sr

    else:

        print(
            f"❌ {market['base']}: "
            f"سیگنال معتبر پیدا نشد "
            f"(LONG={long_score}, "
            f"SHORT={short_score}, "
            f"حداقل={MIN_SCORE})"
        )

        return None

    # -----------------------------
    # حد ضرر و اهداف
    # -----------------------------

    if direction == "LONG":

        stop = entry - 1.2 * a

        risk = entry - stop

        tp1 = entry + 1.5 * risk

        tp2 = entry + 2.5 * risk

    else:

        stop = entry + 1.2 * a

        risk = stop - entry

        tp1 = entry - 1.5 * risk

        tp2 = entry - 2.5 * risk

    # -----------------------------
    # زمان سیگنال
    # -----------------------------

    times = [
        time_ms(t)
        for t in trade_list
        if isinstance(t, dict)
    ]

    times = [
        x for x in times
        if x is not None
    ]

    signal_time = (
        max(times)
        if times
        else int(
            datetime.now(
                timezone.utc
            ).timestamp() * 1000
        )
    )

    result = {
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
        "atr_percent": atr_pct,
        "recent_move": move,
        "signal_time_ms": signal_time,
        "reasons": reasons,
    }

    print(
        f"🚨 سیگنال پیدا شد: "
        f"{market['base']} "
        f"{direction} "
        f"{score}%"
    )

    return result


# =========================================================
# تاریخچه
# =========================================================

def load_history():

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            x = json.load(f)

            return (
                x
                if isinstance(x, list)
                else []
            )

    except (
        FileNotFoundError,
        json.JSONDecodeError,
    ):

        return []


def save_history_local(history):

    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            history[-HISTORY_MAX:],
            f,
            ensure_ascii=False,
            indent=2,
        )


# =========================================================
# ذخیره دائمی history در GitHub
# =========================================================

def save_history_github():

    if not GITHUB_TOKEN:
        print(
            "⚠️ GITHUB_TOKEN وجود ندارد؛ "
            "history فقط در اجرای فعلی ذخیره شد."
        )
        return False

    if not GITHUB_REPOSITORY:
        print(
            "⚠️ GITHUB_REPOSITORY وجود ندارد."
        )
        return False

    api_url = (
        "https://api.github.com"
        f"/repos/{GITHUB_REPOSITORY}"
        f"/contents/{HISTORY_FILE}"
    )

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:

        # دریافت SHA فایل قبلی
        get_r = S.get(
            api_url,
            headers=headers,
            params={
                "ref": GITHUB_REF_NAME
            },
            timeout=TIMEOUT,
        )

        sha = None

        if get_r.status_code == 200:

            old_data = get_r.json()

            if isinstance(old_data, dict):
                sha = old_data.get("sha")

        elif get_r.status_code != 404:

            print(
                "⚠️ خطا در بررسی history در GitHub:"
            )
            print(get_r.text)

            return False

        # خواندن فایل محلی
        with open(
            HISTORY_FILE,
            "rb",
        ) as f:

            content = base64.b64encode(
                f.read()
            ).decode("utf-8")

        payload = {
            "message": (
                "Update signal history "
                "from Tabdeal bot"
            ),
            "content": content,
            "branch": GITHUB_REF_NAME,
        }

        if sha:
            payload["sha"] = sha

        put_r = S.put(
            api_url,
            headers=headers,
            json=payload,
            timeout=TIMEOUT,
        )

        if put_r.status_code in (
            200,
            201,
        ):

            print(
                "✅ signals_history.json "
                "در GitHub ذخیره شد."
            )

            return True

        print(
            "❌ خطا در ذخیره history در GitHub:"
        )
        print(put_r.text)

        return False

    except Exception as e:

        print(
            f"❌ خطا در ذخیره history در GitHub: {e}"
        )

        return False


# =========================================================
# بررسی نتیجه سیگنال‌های قبلی
# =========================================================

def evaluate(history, mkts):

    done = []

    for s in history:

        if s.get("status") != "OPEN":
            continue

        m = mkts.get(
            (
                s.get("base"),
                s.get("quote"),
            )
        )

        if not m:
            continue

        try:

            ts = trades(m)

        except Exception as e:

            print(
                f"{s.get('base')}: "
                f"خطا در بررسی نتیجه: {e}"
            )

            continue

        if not ts:
            continue

        try:
            st = int(
                s["signal_time_ms"]
            )
        except Exception:
            continue

