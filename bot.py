"""
ربات سیگنال معاملاتی - نسخه بازنویسی‌شده
تغییرات اصلی نسبت به نسخه قبلی:
  1) Tabdeal endpoint کندل تاریخی ندارد (فقط 1000 ترید آخر) -> برای تحلیل و بک‌تست
     از کندل‌های واقعی Binance (رایگان، بدون کلید) استفاده می‌شود؛ قیمت لحظه‌ای ورود
     همچنان از Tabdeal گرفته می‌شود چون معامله واقعی همان‌جا انجام می‌شود.
  2) فقط 5 ارز برتر بررسی می‌شود (به‌جای اسکن کل بازار).
  3) قبل از ارسال هر سیگنال زنده، همان استراتژی روی 6 ماه گذشته بک‌تست می‌شود؛
     اگر نرخ برد کافی و نمونه کافی نداشته باشد، سیگنالی برای آن ارز ارسال نمی‌شود.
  4) فیلتر هم‌جهتی روند در 3 تایم‌فریم (روزانه + 4 ساعته + 1 ساعته) اضافه شده که
     بیشتر سیگنال‌های خلاف‌روند (منبع اصلی ضررها) را حذف می‌کند.
"""

import os
import json
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TABDEAL_BASE = "https://api1.tabdeal.org/r/api/v1"
BINANCE_BASE = "https://api.binance.com/api/v3"

# فقط این 5 ارز رصد می‌شوند - بر اساس حجم/نقدشوندگی. هر وقت خواستی عوض کن.
TOP5 = ["BTC", "ETH", "BNB", "SOL", "XRP"]
QUOTE = "USDT"

HISTORY_MONTHS = 6
BACKTEST_HORIZON_BARS = 48       # حداکثر 48 کندل 1 ساعته (~2 روز) برای رسیدن سیگنال به TP/SL
BACKTEST_MIN_TRADES = 15         # کمتر از این تعداد نمونه در بک‌تست، آماری قابل اتکا نیست
BACKTEST_MIN_WINRATE = 0.55      # زیر این نرخ برد، سیگنال زنده صادر نمی‌شود
BACKTEST_REFRESH_HOURS = 24      # هر چند وقت یک‌بار بک‌تست را دوباره اجرا کن

SIGNAL_SCORE_MIN = 60
HISTORY_FILE = "signals_history.json"
BACKTEST_CACHE_FILE = "backtest_cache.json"
HISTORY_MAX = 500
SIGNAL_TTL_HOURS = 24
REQUEST_TIMEOUT = 20
REQUEST_SLEEP = 0.25

SESSION = requests.Session()


# ---------------- HTTP HELPERS ----------------
def tabdeal_get(endpoint, params=None):
    r = SESSION.get(f"{TABDEAL_BASE}/{endpoint}", params=params or {}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def binance_get(endpoint, params=None):
    r = SESSION.get(f"{BINANCE_BASE}/{endpoint}", params=params or {}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------- TABDEAL MARKET LOOKUP (for live entry price) ----------------
def get_tabdeal_markets():
    data = tabdeal_get("exchangeInfo")
    markets = data.get("symbols", data.get("data", data.get("result", []))) if isinstance(data, dict) else data
    if not isinstance(markets, list):
        return {}
    out = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        if str(m.get("status", "TRADING")).upper() != "TRADING":
            continue
        base = str(m.get("baseAsset") or "").upper()
        quote = str(m.get("quoteAsset") or "").upper()
        symbol = str(m.get("symbol") or "").upper()
        tabdeal_symbol = str(m.get("tabdealSymbol", symbol))
        if base and quote:
            out[(base, quote)] = {"symbol": symbol, "tabdeal_symbol": tabdeal_symbol}
    return out


def get_tabdeal_last_price(symbol, tabdeal_symbol):
    for cand in dict.fromkeys([symbol, tabdeal_symbol]):
        if not cand:
            continue
        try:
            data = tabdeal_get("trades", {"symbol": cand, "limit": 5})
            if isinstance(data, dict):
                data = data.get("data", data.get("result", []))
            if isinstance(data, list) and data:
                last = sorted(data, key=lambda t: t.get("time", t.get("timestamp", 0)))[-1]
                price = last.get("price") or last.get("p")
                if price is not None:
                    return float(price)
        except Exception:
            continue
    return None


# ---------------- BINANCE HISTORICAL KLINES ----------------
def binance_klines(symbol, interval, start_ms, end_ms):

out = []
    cur = start_ms
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}
        data = binance_get("klines", params)
        if not data:
            break
        out.extend(data)
        if len(data) < 1000:
            break
        cur = data[-1][0] + 1
        time.sleep(REQUEST_SLEEP)
    return out


def klines_to_df(raw):
    cols = ["open_time", "open", "high", "low", "close", "volume",
             "close_time", "qav", "trades", "tbbav", "tbqav", "ignore"]
    if not raw:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(raw, columns=cols)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df[["time", "open", "high", "low", "close", "volume"]].sort_values("time").reset_index(drop=True)


def get_history(base, months=HISTORY_MONTHS):
    symbol = f"{base}{QUOTE}"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=months * 30)
    s_ms, e_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    d1 = klines_to_df(binance_klines(symbol, "1d", s_ms, e_ms))
    d4 = klines_to_df(binance_klines(symbol, "4h", s_ms, e_ms))
    h1 = klines_to_df(binance_klines(symbol, "1h", s_ms, e_ms))
    return d1, d4, h1


# ---------------- INDICATORS ----------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(d, n=14):
    prev = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df):
    df = df.copy()
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"])
    df["atr"] = atr(df)
    macd_line = ema(df["close"], 12) - ema(df["close"], 26)
    df["macd_hist"] = macd_line - ema(macd_line, 9)
    df["vol_ma"] = df["volume"].rolling(20).mean()
    return df


# ---------------- MULTI-TIMEFRAME ALIGNMENT ----------------
def align_timeframes(d1, d4, h1):
    d1a = add_indicators(d1).add_prefix("d1_").rename(columns={"d1_time": "time"})
    d4a = add_indicators(d4).add_prefix("d4_").rename(columns={"d4_time": "time"})
    h1a = add_indicators(h1)

    merged = pd.merge_asof(h1a.sort_values("time"), d4a[["time", "d4_ema20", "d4_ema50", "d4_rsi", "d4_macd_hist"]],
                            on="time", direction="backward")
    merged = pd.merge_asof(merged, d1a[["time", "d1_ema20", "d1_ema50"]], on="time", direction="backward")
    return merged


# ---------------- SIGNAL LOGIC ----------------
def evaluate_row(row):
    """Return (direction, score, reasons) or None. Requires trend agreement across all 3 timeframes."""
    needed = ["d1_ema20", "d1_ema50", "d4_ema20", "d4_ema50", "d4_rsi", "d4_macd_hist",
              "ema20", "rsi", "vol_ma", "atr", "close", "open", "volume"]
    if any(pd.isna(row.get(k)) for k in needed):
        return None

    daily_up = row["d1_ema20"] > row["d1_ema50"]
    daily_down = row["d1_ema20"] < row["d1_ema50"]
    h4_up = row["d4_ema20"] > row["d4_ema50"]
    h4_down = row["d4_ema20"] < row["d4_ema50"]

    long_ok = daily_up and h4_up
    short_ok = daily_down and h4_down
    if not (long_ok or short_ok):
        return None  # روند بزرگ‌تر هم‌جهت نیست -> بی‌خیال این کندل

    score, reasons = 0, []
    direction = "LONG" if long_ok else "SHORT"

if direction == "LONG":
        if row["d4_rsi"] > 50: score += 20; reasons.append("RSI ۴ساعته مثبت")
        if row["d4_macd_hist"] > 0: score += 20; reasons.append("MACD ۴ساعته مثبت")
        if row["close"] > row["ema20"]: score += 20; reasons.append("قیمت ۱ساعته بالای EMA20")
        if row["rsi"] >= 52: score += 15; reasons.append("مومنتوم ۱ساعته صعودی")
        if pd.notna(row["vol_ma"]) and row["volume"] > row["vol_ma"] * 1.2 and row["close"] > row["open"]:
            score += 15; reasons.append("حجم بالاتر از میانگین")
        score += 10; reasons.append("هم‌جهتی روند روزانه + ۴ساعته")
    else:
        if row["d4_rsi"] < 50: score += 20; reasons.append("RSI ۴ساعته منفی")
        if row["d4_macd_hist"] < 0: score += 20; reasons.append("MACD ۴ساعته منفی")
        if row["close"] < row["ema20"]: score += 20; reasons.append("قیمت ۱ساعته زیر EMA20")
        if row["rsi"] <= 48: score += 15; reasons.append("مومنتوم ۱ساعته نزولی")
        if pd.notna(row["vol_ma"]) and row["volume"] > row["vol_ma"] * 1.2 and row["close"] < row["open"]:
            score += 15; reasons.append("حجم بالاتر از میانگین")
        score += 10; reasons.append("هم‌جهتی روند روزانه + ۴ساعته")

    if score < SIGNAL_SCORE_MIN:
        return None
    return direction, score, reasons


def build_trade_levels(entry, av, direction):
    if direction == "LONG":
        stop = entry - 1.2 * av
        risk = entry - stop
        return stop, entry + 1.5 * risk, entry + 2.5 * risk
    stop = entry + 1.2 * av
    risk = stop - entry
    return stop, entry - 1.5 * risk, entry - 2.5 * risk


# ---------------- BACKTEST ----------------
def run_backtest(merged):
    """Walk forward through 1h bars, simulate one trade at a time, return stats."""
    trades = []
    i = 0
    n = len(merged)
    busy_until = -1
    rows = merged.to_dict("records")

    while i < n:
        if i <= busy_until:
            i += 1
            continue
        row = rows[i]
        res = evaluate_row(row)
        if res is None:
            i += 1
            continue

        direction, score, _ = res
        entry, av = row["close"], row["atr"]
        if not np.isfinite(av) or av <= 0:
            i += 1
            continue
        stop, tp1, tp2 = build_trade_levels(entry, av, direction)

        outcome = "EXPIRED"
        for j in range(i + 1, min(i + 1 + BACKTEST_HORIZON_BARS, n)):
            hi, lo = rows[j]["high"], rows[j]["low"]
            if direction == "LONG":
                if lo <= stop: outcome = "SL"; break
                if hi >= tp2: outcome = "TP2"; break
                if hi >= tp1: outcome = "TP1"; break
            else:
                if hi >= stop: outcome = "SL"; break
                if lo <= tp2: outcome = "TP2"; break
                if lo <= tp1: outcome = "TP1"; break
        else:
            j = min(i + BACKTEST_HORIZON_BARS, n - 1)

        trades.append({"time": str(row["time"]), "direction": direction, "outcome": outcome})
        busy_until = j
        i += 1

    wins = sum(1 for t in trades if t["outcome"] in ("TP1", "TP2"))
    losses = sum(1 for t in trades if t["outcome"] == "SL")
    resolved = wins + losses
    winrate = wins / resolved if resolved else 0.0
    return {
        "total_signals": len(trades),
        "resolved": resolved,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 3),
    }


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_backtest_stats(base, merged):
    cache = load_json(BACKTEST_CACHE_FILE, {})
    entry = cache.get(base)
    now = datetime.now(timezone.utc)
    if entry:
        age_h = (now - datetime.fromisoformat(entry["computed_at"])).total_seconds() / 3600
        if age_h < BACKTEST_REFRESH_HOURS:
            return entry["stats"]

stats = run_backtest(merged)
    cache[base] = {"computed_at": now.isoformat(), "stats": stats}
    save_json(BACKTEST_CACHE_FILE, cache)
    return stats


# ---------------- MESSAGING ----------------
def fmt(x):
    x = float(x)
    if x >= 1000: return f"{x:,.2f}"
    if x >= 1: return f"{x:,.4f}"
    if x >= 0.01: return f"{x:,.6f}"
    return f"{x:.10f}"


def send_telegram(text):
    r = SESSION.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": text}, timeout=20)
    r.raise_for_status()


def make_signal_message(base, direction, entry, stop, tp1, tp2, score, reasons, bt):
    reasons_txt = "\n".join("✅ " + x for x in reasons)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "📢 سیگنال تحلیل تبدیل (5 ارز برتر، تایید ۶ماهه)\n\n"
        f"🪙 {base}/USDT\n"
        f"📌 جهت: {'خرید (LONG)' if direction=='LONG' else 'فروش (SHORT)'}\n"
        "⏱ تایم‌فریم: روزانه + ۴ساعته + ۱ساعته (هم‌جهت)\n\n"
        f"📍 ورود: {fmt(entry)}\n"
        f"🛑 حد ضرر: {fmt(stop)}\n"
        f"🎯 TP1: {fmt(tp1)}\n"
        f"🎯 TP2: {fmt(tp2)}\n"
        f"📊 امتیاز سیگنال: {score}/100\n"
        f"📈 نرخ برد بک‌تست ۶ماهه این الگو: {bt['winrate']*100:.0f}٪ "
        f"(از {bt['resolved']} سیگنال گذشته)\n"
        f"🕐 زمان: {when}\n\n"
        f"دلایل:\n{reasons_txt}\n\n"
        "⚠️ این فقط تحلیل است، نه توصیه مالی؛ معامله خودکار انجام نمی‌شود."
    )


# ---------------- OPEN SIGNAL TRACKING / RESULTS ----------------
def evaluate_open_signals(history, markets):
    changed = False
    now = datetime.now(timezone.utc)
    for x in history:
        if x.get("status") != "OPEN":
            continue

        info = markets.get((x["base"], "USDT"))
        if not info:
            continue

        price = get_tabdeal_last_price(info["symbol"], info["tabdeal_symbol"])
        if price is None:
            continue

        direction = x["direction"]
        outcome = None
        if direction == "LONG":
            if price <= x["stop"]: outcome = "SL"
            elif price >= x["tp2"]: outcome = "TP2"
            elif price >= x["tp1"]: outcome = "TP1"
        else:
            if price >= x["stop"]: outcome = "SL"
            elif price <= x["tp2"]: outcome = "TP2"
            elif price <= x["tp1"]: outcome = "TP1"

        signal_time = datetime.fromisoformat(x["signal_time"])
        if outcome is None and (now - signal_time) > timedelta(hours=SIGNAL_TTL_HOURS):
            outcome = "EXPIRED"

        if outcome:
            x["status"] = outcome
            x["hit_price"] = price
            x["closed_time"] = now.isoformat()
            changed = True
            try:
                label = {"TP1": "✅ درست — TP1", "TP2": "✅ درست — TP2",
                          "SL": "❌ نادرست — حد ضرر", "EXPIRED": "⚪ نامشخص — منقضی شد"}[outcome]
                send_telegram(f"📊 نتیجه سیگنال\n\n🪙 {x['base']}/USDT\n{label}\n"
                               f"💵 ورود: {fmt(x['entry'])}\n🎯 قیمت نتیجه: {fmt(price)}")
            except Exception:
                pass
    return changed


# ---------------- MAIN ----------------
def main():
    markets = get_tabdeal_markets()
    history = load_json(HISTORY_FILE, [])

    if evaluate_open_signals(history, markets):
        save_json(HISTORY_FILE, history[-HISTORY_MAX:])

    open_bases = {x["base"] for x in history if x.get("status") == "OPEN"}

    for base in TOP5:
        try:
            info = markets.get((base, QUOTE))
            if not info:
                print(f"{base}/{QUOTE} در بازارهای Tabdeal پیدا نشد، رد شد.")
                continue
            if base in open_bases:
                continue  # یک پوزیشن باز به ازای هر ارز کافیست

            d1, d4, h1 = get_history(base)
            if len(d1) < 60 or len(d4) < 60 or len(h1) < 60:
                print(f"{base}: داده تاریخی کافی نیست.")
                continue

            merged = align_timeframes(d1, d4, h1)
            bt = get_backtest_stats(base, merged)

if bt["resolved"] < BACKTEST_MIN_TRADES or bt["winrate"] < BACKTEST_MIN_WINRATE:
                print(f"{base}: بک‌تست رد شد (winrate={bt['winrate']}, resolved={bt['resolved']}).")
                continue

            last_row = merged.iloc[-2]  # آخرین کندل کامل‌شده (نه کندل در حال شکل‌گیری)
            res = evaluate_row(last_row)
            if res is None:
                continue

            direction, score, reasons = res
            live_price = get_tabdeal_last_price(info["symbol"], info["tabdeal_symbol"])
            entry = live_price if live_price is not None else float(last_row["close"])
            av = float(last_row["atr"])
            stop, tp1, tp2 = build_trade_levels(entry, av, direction)

            msg = make_signal_message(base, direction, entry, stop, tp1, tp2, score, reasons, bt)
            send_telegram(msg)

            history.append({
                "base": base, "symbol": info["symbol"], "direction": direction,
                "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
                "signal_time": datetime.now(timezone.utc).isoformat(), "status": "OPEN",
            })
            save_json(HISTORY_FILE, history[-HISTORY_MAX:])

        except requests.HTTPError as e:
            print(f"{base}: خطای HTTP - {e}")
        except Exception as e:
            print(f"{base}: خطای غیرمنتظره - {e}")


if name == "main":
    main()
