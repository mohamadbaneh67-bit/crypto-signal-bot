import os
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Phase 1: public market data from Binance.
# Tabdil public API has not been verified, so these are NOT claimed to be Tabdil prices.
SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","DOGEUSDT","AVAXUSDT","TRXUSDT","LINKUSDT",
    "DOTUSDT","LTCUSDT","BCHUSDT","ATOMUSDT","ETCUSDT",
    "FILUSDT","NEARUSDT","APTUSDT","ARBUSDT","SUIUSDT"
]

BINANCE_URL = "https://api.binance.com/api/v3/klines"


def get_klines(symbol, interval, limit=200):
    r = requests.get(
        BINANCE_URL,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15
    )
    r.raise_for_status()

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbav", "tqav", "ignore"
    ]

    df = pd.DataFrame(r.json(), columns=cols)

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()

    gain = d.clip(lower=0).ewm(
        alpha=1 / n,
        adjust=False
    ).mean()

    loss = (-d.clip(upper=0)).ewm(
        alpha=1 / n,
        adjust=False
    ).mean()

    rs = gain / loss.replace(0, np.nan)

    return 100 - (100 / (1 + rs))


def atr(df, n=14):
    prev = df["close"].shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs()
    ], axis=1).max(axis=1)

    return tr.ewm(
        alpha=1 / n,
        adjust=False
    ).mean()


def macd(s):
    line = ema(s, 12) - ema(s, 26)
    signal = ema(line, 9)

    return line, signal, line - signal


def analyze(symbol):
    d15 = get_klines(symbol, "15m")
    d5 = get_klines(symbol, "5m")

    for d in (d15, d5):
        d["ema20"] = ema(d.close, 20)
        d["ema50"] = ema(d.close, 50)
        d["rsi"] = rsi(d.close)
        d["atr"] = atr(d)
        d["vol_ma"] = d.volume.rolling(20).mean()

    mline, msignal, mhist = macd(d15.close)
    d15["macd_hist"] = mhist

    # Last closed candles
    a15 = d15.iloc[-2]
    a5 = d5.iloc[-2]

    long_score = 0
    short_score = 0

    lr = []
    sr = []

    if a15.ema20 > a15.ema50:
        long_score += 20
        lr.append("روند 15دقیقه صعودی")

    if a15.ema20 < a15.ema50:
        short_score += 20
        sr.append("روند 15دقیقه نزولی")

    if a15.rsi > 50:
        long_score += 15
        lr.append("RSI مثبت")

    if a15.rsi < 50:
        short_score += 15
        sr.append("RSI منفی")

    if a15.macd_hist > 0:
        long_score += 20
        lr.append("MACD مثبت")

    if a15.macd_hist < 0:
        short_score += 20
        sr.append("MACD منفی")

    if a5.close > a5.ema20:
        long_score += 15
        lr.append("قیمت 5دقیقه بالای EMA20")

    if a5.close < a5.ema20:
        short_score += 15
        sr.append("قیمت 5دقیقه زیر EMA20")

    if a5.volume > a5.vol_ma * 1.2:
        if a5.close > a5.open:
            long_score += 15
            lr.append("حجم بالاتر از میانگین")

        elif a5.close < a5.open:
            short_score += 15
            sr.append("حجم بالاتر از میانگین")

    if a5.rsi >= 52:
        long_score += 15
        lr.append("مومنتوم 5دقیقه‌ای")

    if a5.rsi <= 48:
        short_score += 15
        sr.append("مومنتوم 5دقیقه‌ای")

    price = float(a5.close)
    a = float(a5.atr)

    if not np.isfinite(a) or a <= 0:
        return None

    if long_score >= 75 and long_score > short_score:
        stop = price - 1.2 * a
        risk = price - stop

        return (
            "LONG",
            long_score,
            price,
            stop,
            price + 1.5 * risk,
            price + 2.5 * risk,
            lr
        )

    if short_score >= 75 and short_score > long_score:
        stop = price + 1.2 * a
        risk = stop - price

        return (
            "SHORT",
            short_score,
            price,
            stop,
            price - 1.5 * risk,
            price - 2.5 * risk,
            sr
        )

    return None


def fmt(x):
    if x >= 1000:
        return f"{x:,.2f}"

    if x >= 1:
        return f"{x:,.4f}"

    return f"{x:.8f}".rstrip("0").rstrip(".")


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=15
    )

    r.raise_for_status()


def main():
    found = []

    for symbol in SYMBOLS:
        try:
            result = analyze(symbol)

            if result:
                found.append((symbol, result))

        except Exception as e:
            print("ERROR", symbol, e)

    now = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    if not found:
        send(
            f"🔎 اسکن بازار انجام شد\n\n"
            f"در این اسکن سیگنال قوی پیدا نشد.\n"
            f"زمان: {now}"
        )
        return

    for symbol, s in found:

        side, score, entry, stop, tp1, tp2, reasons = s

        emoji = "🟢" if side == "LONG" else "🔴"

        text = (
            f"{emoji} فرصت {side}\n"
            f"ارز: {symbol.replace('USDT','/USDT')}\n"
            f"تایم‌فریم: 5m + 15m\n\n"
            f"📍 ورود: {fmt(entry)}\n"
            f"🛑 حد ضرر: {fmt(stop)}\n"
            f"🎯 هدف 1: {fmt(tp1)}\n"
            f"🎯 هدف 2: {fmt(tp2)}\n"
            f"⚖️ R:R تقریبی: 1:2.5\n"
            f"📊 امتیاز شرایط: {score}/100\n\n"
            "دلایل:\n"
            + "\n".join("✅ " + x for x in reasons)
            + "\n\n"
            "⚠️ سیگنال تضمین سود نیست؛ قبل از معامله "
            "قیمت، کارمزد و اسلیپیج صرافی خودت را بررسی کن."
        )

        send(text)


if __name__ == "__main__":
    main()
