import os
import json
import time
import threading
from datetime import datetime, timezone

import requests
import websocket

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

REST_BASE = "https://api1.tabdeal.org/r/fapi/v1"
WS_URL = "wss://api1.tabdeal.org/special_margin/stream/"

REQUESTED_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
]

RUN_SECONDS = 90
OUTPUT_FILE = "tabdeal_margin_messages.jsonl"
TIMEOUT = 20

session = requests.Session()
message_count = 0
error_count = 0
subscribed_symbols = []


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram settings are incomplete.")
        return False

    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"

    try:
        response = session.post(
            url,
            json={"chat_id": CHAT_ID, "text": message},
            timeout=TIMEOUT,
        )
        print("Telegram HTTP:", response.status_code)
        if not response.ok:
            print("Telegram response:", response.text[:1000])
        return response.ok
    except Exception as error:
        print("Telegram error:", repr(error))
        return False


def save_message(message, kind="websocket"):
    record = {
        "received_at": now_iso(),
        "kind": kind,
        "message": message,
    }
    with open(OUTPUT_FILE, "a", encoding="utf-8") as file:
        file.write(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )


def reset_output_file():
    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write("")


def get_exchange_info():
    print("=" * 70)
    print("مرحله 1: بررسی بازار اهرم حرفه‌ای Tabdeal")
    print("=" * 70)

    url = REST_BASE + "/exchangeInfo"

    try:
        response = session.get(url, timeout=TIMEOUT)
        print("REST URL:", url)
        print("HTTP STATUS:", response.status_code)

        if not response.ok:
            print("REST ERROR:", response.text[:3000])
            return []

        data = response.json()
        save_message(data, kind="exchangeInfo")

        raw_symbols = data.get("symbols", [])
        if not isinstance(raw_symbols, list):
            print("ساختار symbols قابل تشخیص نیست.")
            return []

        available = []
        for item in raw_symbols:
            if isinstance(item, dict):
                symbol = str(item.get("symbol", "")).upper()
                if symbol:
                    available.append(symbol)

        print("تعداد نمادهای بازار:", len(available))

        found = [
            symbol for symbol in REQUESTED_SYMBOLS
            if symbol in available
        ]

        print("نمادهای پیدا شده:", found)

        if not found:
            print("نمونه نمادهای واقعی بازار:")
            print(available[:50])

        return found

    except Exception as error:
        print("EXCHANGE INFO ERROR:", repr(error))
        return []


def test_depth_snapshot(symbols):
    print("=" * 70)
    print("مرحله 2: تست داده بازار اهرم حرفه‌ای")
    print("=" * 70)

    successful = 0

    for symbol in symbols:
        try:
            response = session.get(
                REST_BASE + "/depth",
                params={"symbol": symbol, "limit": 5},
                timeout=TIMEOUT,
            )

            print(symbol, "HTTP", response.status_code)

            if response.ok:
                data = response.json()
                save_message(
                    {"symbol": symbol, "data": data},
                    kind="depth_snapshot",
                )

                bids = data.get("bids", [])
                asks = data.get("asks", [])

                print("  bids =", len(bids), "asks =", len(asks))

                if bids or asks:
                    successful += 1
            else:
                print("  ERROR:", response.text[:1000])

        except Exception as error:
            print(symbol, "ERROR:", repr(error))

    return successful


def on_open(ws):
    print("=" * 70)
    print("مرحله 3: WebSocket اهرم حرفه‌ای متصل شد")
    print("=" * 70)

    params = [
        symbol.lower() + "@depth@2000ms"
        for symbol in subscribed_symbols
    ]

    request = {
        "method": "SUBSCRIBE",
        "params": params,
        "id": 1,
    }

    print("SUBSCRIBE:")
    print(json.dumps(request, ensure_ascii=False))

    try:
        ws.send(json.dumps(request))
        print("درخواست اشتراک ارسال شد.")
    except Exception as error:
        print("خطای ارسال:", repr(error))


def on_message(ws, message):
    global message_count
    message_count += 1

    print("----- پیام WebSocket -----")
    print(message[:5000])

    try:
        parsed = json.loads(message)
        save_message(parsed, kind="websocket")
    except Exception:
        save_message(message, kind="websocket_raw")


def on_error(ws, error):
    global error_count
    error_count += 1

    print("!!! WebSocket ERROR !!!")
    print(repr(error))

    save_message(
        {"error": repr(error)},
        kind="websocket_error",
    )


def on_close(ws, close_status_code, close_msg):
    print("WebSocket بسته شد.")
    print("کد:", close_status_code)
    print("پیام:", close_msg)


def run_websocket_probe():
    if not subscribed_symbols:
        return

    start_time = time.time()

    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    def stopper():
        remaining = RUN_SECONDS - (time.time() - start_time)
        if remaining > 0:
            time.sleep(remaining)
        try:
            ws.close()
        except Exception:
            pass

    threading.Thread(target=stopper, daemon=True).start()

    print("WebSocket برای", RUN_SECONDS, "ثانیه اجرا می‌شود...")

    try:
        ws.run_forever(
            ping_interval=25,
            ping_timeout=10,
        )
    except Exception as error:
        print("run_forever ERROR:", repr(error))

    print("تعداد پیام‌ها:", message_count)
    print("تعداد خطاها:", error_count)


def main():
    global subscribed_symbols

    reset_output_file()

    send_telegram(
        "🔌 شروع تست اصلاح‌شده Tabdeal\n\n"
        "📡 بازار: اهرم حرفه‌ای\n"
        "🪙 BTC / ETH / BNB / SOL / XRP\n"
        "🔎 ابتدا نمادهای واقعی بازار بررسی می‌شوند.\n"
        "📥 سپس REST و WebSocket تست می‌شود.\n\n"
        "⚠️ هیچ معامله‌ای انجام نمی‌شود."
    )

    try:
        subscribed_symbols = get_exchange_info()

        if not subscribed_symbols:
            send_telegram(
                "❌ نماد قابل استفاده در بازار اهرم حرفه‌ای پیدا نشد.\n\n"
                "داده خام در فایل ذخیره شده است."
            )
            return

        depth_success = test_depth_snapshot(subscribed_symbols)
        run_websocket_probe()

        if message_count > 0:
            ws_status = f"✅ WebSocket: {message_count} پیام دریافت شد."
        else:
            ws_status = "⚠️ WebSocket: پیام بازار دریافت نشد."

        final_message = (
            "✅ تست اصلاح‌شده Tabdeal تمام شد.\n\n"
            "📡 بازار: اهرم حرفه‌ای\n"
            f"🪙 نمادها: {', '.join(subscribed_symbols)}\n\n"
            f"📊 REST Depth موفق: {depth_success}\n"
            f"{ws_status}\n"
            f"⚠️ خطاهای WebSocket: {error_count}\n\n"
            "📁 داده خام: tabdeal_margin_messages.jsonl\n\n"
            "مرحله بعد: ساخت کندل 5m و 15m بر اساس داده واقعی."
        )

        print(final_message)
        send_telegram(final_message)

    except Exception as error:
        print("خطای اصلی:", repr(error))
        send_telegram(
            "❌ تست Tabdeal با خطا متوقف شد.\n\n"
            "خطا:\n" + repr(error)[:1500]
        )
        raise


if __name__ == "__main__":
    main()
        
