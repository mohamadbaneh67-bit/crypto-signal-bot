"""
ربات سیگنال معاملاتی - نسخه تنها-Tabdeal
====================================
Tabdeal هیچ endpoint کندل/کلاین تاریخی ندارد (فقط `trades` با پارامتر limit،
بدون بازه زمانی). پس گرفتن ۶ ماه کندل واقعی در یک اجرا از Tabdeal ممکن نیست.

راه‌حل: این ربات هر بار که اجرا می‌شود، آخرین معاملات را می‌گیرد و در یک فایل
محلی (candle_store.json) ذخیره می‌کند. با گذشت روزها/هفته‌ها، این فایل خودش
تبدیل به یک آرشیو واقعی قیمت می‌شود که بک‌تست و تحلیل چندتایم‌فریمی روی آن
انجام می‌شود.

نکته مهم: تا وقتی آرشیو به اندازه کافی نرسیده (به‌طور پیش‌فرض حداقل ~55 روز،
چون فیلتر روند روزانه به این مقدار داده نیاز دارد)، ربات فقط داده جمع می‌کند
و سیگنالی نمی‌فرستد. این عمدی است؛ سیگنال دادن بدون پشتوانه تاریخی همان مشکلی
بود که باعث برد کم می‌شد.

باید این اسکریپت را مرتب (مثلاً هر 5 تا 15 دقیقه، با cron) اجرا کنید تا
آرشیو کامل شود.
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
SESSION = requests.Session()
REQUEST_TIMEOUT = 20

# فقط این 5 ارز رصد می‌شوند - بر اساس حجم/نقدشوندگی. هر وقت خواستی عوض کن.
TOP5 = ["BTC", "ETH", "BNB", "SOL", "XRP"]
QUOTE = "USDT"

TRADE_LIMIT = 1000                 # حداکثر معاملاتی که هر بار از Tabdeal می‌گیریم
BASE_CANDLE_MINUTES = 5            # ریزترین کندلی که از معاملات خام می‌سازیم

CANDLE_STORE_FILE = "candle_store.json"
STORE_MAX_DAYS = 210               # آرشیو را حدوداً هم‌ارز ۷ ماه نگه می‌داریم، مازاد حذف می‌شود

MIN_HISTORY_DAYS = 55              # حداقل عمر آرشیو قبل از فعال‌شدن سیگنال‌دهی
BACKTEST_HORIZON_BARS = 48         # حداکثر کندل ۱ساعته (~۲ روز) برای رسیدن به TP/SL در بک‌تست
BACKTEST_MIN_TRADES = 15           # حداقل نمونه بک‌تست‌شده برای اعتماد آماری
BACKTEST_MIN_WINRATE = 0.55        # زیر این نرخ برد، سیگنال زنده صادر نمی‌شود

SIGNAL_SCORE_MIN = 60
SIGNALS_HISTORY_FILE = "signals_history.json"
SIGNALS_HISTORY_MAX = 500
SIGNAL_TTL_HOURS = 24


# ---------------- HTTP ----------------
def tabdeal_get(endpoint, params=None):
    r = SESSION.get(f"{TABDEAL_BASE}/{endpoint}", params=params or {}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


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


def tv(t, *keys):
    for k in keys:
        if t.get(k) is not None:
            return t[k]
    return None


def trade_time_ms(t):
    x = tv(t, "time", "timestamp", "T", "createdAt", "created_at")
    if x is None:
        return None
    try:
        x = float(x)
        return int(x * 1000 if x < 10_000_000_000 else x)
    except Exception:
        return None


def get_trades(symbol, tabdeal_symbol=None):
    """آخرین معاملات عمومی. symbol را اول امتحان می‌کند، بعد tabdeal_symbol."""
    candidates = []
    for value in (symbol, tabdeal_symbol):
        if value:
            value = str(value).upper()
            if value not in candidates:
                candidates.append(value)

    last_error = None
    for candidate in candidates:
        try:
            data = tabdeal_get("trades", {"symbol": candidate, "limit": TRADE_LIMIT})
            if isinstance(data, dict):
                data = data.get("data", data.get("result", []))
            if isinstance(data, list):
                return data
        except Exception as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    return []


def get_last_price(symbol, tabdeal_symbol):
    try:
        trades = get_trades(symbol, tabdeal_symbol)
    except Exception:
        return None
    priced = [(trade_time_ms(t), tv(t, "price", "p")) for t in trades if isinstance(t, dict)]
    priced = [(ts, p) for ts, p in priced if ts is not None and p is not None]
    if not priced:
        return None
    priced.sort(key=lambda x: x[0])
    try:
        return float(priced[-1][1])
    except Exception:
        return None


def trades_to_candles(trades, minutes):
    rows = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        p, q, ts = tv(t, "price", "p"), tv(t, "qty", "quantity", "q", "amount", "volume"), trade_time_ms(t)
        if p is None or ts is None:
            continue
        try:
            rows.append((pd.to_datetime(ts, unit="ms", utc=True), float(p), float(q or 0)))
        except Exception:
            pass
    if not rows:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    d = pd.DataFrame(rows, columns=["time", "price", "volume"]).sort_values("time").set_index("time")
    c = d["price"].resample(f"{minutes}min").ohlc()
    c["volume"] = d["volume"].resample(f"{minutes}min").sum()
    return c.dropna(subset=["open", "high", "low", "close"]).reset_index()


# ---------------- LOCAL CANDLE STORE (جایگزین کلاین‌های تاریخی) ----------------
def load_store():
    try:
        with open(CANDLE_STORE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    store = {}
    for base, rows in raw.items():
        if not rows:
            store[base] = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
            continue
        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        store[base] = df
    return store


def save_store(store):
    raw = {}
    for base, df in store.items():
        d = df.copy()
        d["time"] = d["time"].astype(str)
        raw[base] = d.to_dict("records")
    with open(CANDLE_STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)


def merge_into_store(existing, new_candles):
    if existing is None or existing.empty:
        merged = new_candles
    elif new_candles.empty:
        merged = existing
    else:
        merged = pd.concat([existing, new_candles], ignore_index=True)
    if merged.empty:
        return merged
    merged = merged.drop_duplicates(subset="time", keep="last").sort_values("time")
    cutoff = pd.Timestamp.now(tz="UTC") - timedelta(days=STORE_MAX_DAYS)
    merged = merged[merged["time"] >= cutoff].reset_index(drop=True)
    return merged


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


def resample_ohlc(df5m, minutes):
    if df5m.empty:
        return df5m
    d = df5m.set_index("time")
    c = d["close"].resample(f"{minutes}min").ohlc()
    c["volume"] = d["volume"].resample(f"{minutes}min").sum()
    return c.dropna(subset=["open", "high", "low", "close"]).reset_index()


def align_timeframes(df5m):
    d1 = add_indicators(resample_ohlc(df5m, 60 * 24)).add_prefix("d1_").rename(columns={"d1_time": "time"})
    d4 = add_indicators(resample_ohlc(df5m, 60 * 4)).add_prefix("d4_").rename(columns={"d4_time": "time"})
    h1a = add_indicators(resample_ohlc(df5m, 60))

    merged = pd.merge_asof(h1a.sort_values("time"), d4[["time", "d4_ema20", "d4_ema50", "d4_rsi", "d4_macd_hist"]],
                            on="time", direction="backward")
    merged = pd.merge_asof(merged, d1[["time", "d1_ema20", "d1_ema50"]], on="time", direction="backward")
    return merged


# ---------------- SIGNAL LOGIC ----------------
def evaluate_row(row):
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
        return None

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


# ---------------- BACKTEST (روی هر چقدر آرشیو محلی که تا الان جمع شده) ----------------
def run_backtest(merged):
    trades = []
    i, n, busy_until = 0, len(merged), -1
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

        direction, _, _ = res
        entry, av = row["close"], row["atr"]
        if not np.isfinite(av) or av <= 0:
            i += 1
            continue
        stop, tp1, tp2 = build_trade_levels(entry, av, direction)

        outcome = "EXPIRED"
        j = min(i + BACKTEST_HORIZON_BARS, n - 1)
        for k in range(i + 1, min(i + 1 + BACKTEST_HORIZON_BARS, n)):
            hi, lo = rows[k]["high"], rows[k]["low"]
            if direction == "LONG":
                if lo <= stop: outcome = "SL"; j = k; break
                if hi >= tp2: outcome = "TP2"; j = k; break
                if hi >= tp1: outcome = "TP1"; j = k; break
            else:
                if hi >= stop: outcome = "SL"; j = k; break
                if lo <= tp2: outcome = "TP2"; j = k; break
                if lo <= tp1: outcome = "TP1"; j = k; break

        trades.append(outcome)
        busy_until = j
        i += 1

    wins = sum(1 for o in trades if o in ("TP1", "TP2"))
    losses = sum(1 for o in trades if o == "SL")
    resolved = wins + losses
    return {
        "total_signals": len(trades),
        "resolved": resolved,
        "wins": wins,
        "losses": losses,
        "winrate": round(wins / resolved, 3) if resolved else 0.0,
    }


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


def make_signal_message(base, direction, entry, stop, tp1, tp2, score, reasons, bt, history_days):
    reasons_txt = "\n".join("✅ " + x for x in reasons)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "📢 سیگنال تحلیل تبدیل (فقط Tabdeal، آرشیو محلی)\n\n"
        f"🪙 {base}/USDT\n"
        f"📌 جهت: {'خرید (LONG)' if direction=='LONG' else 'فروش (SHORT)'}\n"
        "⏱ تایم‌فریم: روزانه + ۴ساعته + ۱ساعته (هم‌جهت)\n\n"
        f"📍 ورود: {fmt(entry)}\n"
        f"🛑 حد ضرر: {fmt(stop)}\n"
        f"🎯 TP1: {fmt(tp1)}\n"
        f"🎯 TP2: {fmt(tp2)}\n"
        f"📊 امتیاز سیگنال: {score}/100\n"
        f"📈 نرخ برد بک‌تست: {bt['winrate']*100:.0f}٪ (از {bt['resolved']} سیگنال گذشته، "
        f"بر پایه {history_days} روز آرشیو محلی)\n"
        f"🕐 زمان: {when}\n\n"
        f"دلایل:\n{reasons_txt}\n\n"
        "⚠️ این فقط تحلیل است، نه توصیه مالی؛ معامله خودکار انجام نمی‌شود."
    )


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def evaluate_open_signals(history, markets):
    changed = False
    now = datetime.now(timezone.utc)
    for x in history:
        if x.get("status") != "OPEN":
            continue
        info = markets.get((x["base"], "USDT"))
        if not info:
            continue
        price = get_last_price(info["symbol"], info["tabdeal_symbol"])
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
    store = load_store()
    sig_history = load_json(SIGNALS_HISTORY_FILE, [])

    if evaluate_open_signals(sig_history, markets):
        save_json(SIGNALS_HISTORY_FILE, sig_history[-SIGNALS_HISTORY_MAX:])

    open_bases = {x["base"] for x in sig_history if x.get("status") == "OPEN"}
    store_changed = False

    for base in TOP5:
        try:
            info = markets.get((base, QUOTE))
            if not info:
                print(f"{base}/{QUOTE} در بازارهای Tabdeal پیدا نشد، رد شد.")
                continue

            trades = get_trades(info["symbol"], info["tabdeal_symbol"])
            new_candles = trades_to_candles(trades, BASE_CANDLE_MINUTES)
            store[base] = merge_into_store(store.get(base), new_candles)
            store_changed = True

            df5m = store[base]
            if df5m.empty:
                continue
            history_days = (df5m["time"].max() - df5m["time"].min()).total_seconds() / 86400

            if history_days < MIN_HISTORY_DAYS:
                print(f"{base}: هنوز {history_days:.1f} روز آرشیو جمع شده "
                      f"(نیاز به {MIN_HISTORY_DAYS} روز) — فقط داده جمع می‌شود.")
                continue

            if base in open_bases:
                continue  # یک پوزیشن باز به ازای هر ارز کافیست

            merged = align_timeframes(df5m)
            if len(merged) < 60:
                continue

            bt = run_backtest(merged)
            if bt["resolved"] < BACKTEST_MIN_TRADES or bt["winrate"] < BACKTEST_MIN_WINRATE:
                print(f"{base}: بک‌تست رد شد (winrate={bt['winrate']}, resolved={bt['resolved']}).")
                continue

            last_row = merged.iloc[-2]  # آخرین کندل کامل‌شده
            res = evaluate_row(last_row)
            if res is None:
                continue

            direction, score, reasons = res
            live_price = get_last_price(info["symbol"], info["tabdeal_symbol"])
            entry = live_price if live_price is not None else float(last_row["close"])
            av = float(last_row["atr"])
            stop, tp1, tp2 = build_trade_levels(entry, av, direction)

            msg = make_signal_message(base, direction, entry, stop, tp1, tp2, score, reasons, bt,
                                       round(history_days))
            send_telegram(msg)

            sig_history.append({
                "base": base, "symbol": info["symbol"], "direction": direction,
                "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
                "signal_time": datetime.now(timezone.utc).isoformat(), "status": "OPEN",
            })
            save_json(SIGNALS_HISTORY_FILE, sig_history[-SIGNALS_HISTORY_MAX:])

        except requests.HTTPError as e:
            print(f"{base}: خطای HTTP - {e}")
        except Exception as e:
            print(f"{base}: خطای غیرمنتظره - {e}")

    if store_changed:
        save_store(store)


if __name__ == "__main__":
    main()
