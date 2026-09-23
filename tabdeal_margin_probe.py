import os
import json
import time
import threading
from datetime import datetime, timezone

import requests
import websocket


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WS_URL = "wss://api1.tabdeal.org/special_margin/stream/"

SYMBOLS = [
    "btcusdt",
    "ethusdt",
    "bnbusdt",
    "solusdt",
    "xrpusdt",
]

RUN_SECONDS = 90
OUTPUT_FILE = "tabdeal_margin_messages.jsonl"
TIMEOUT = 20

session = requests.Session()


def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("تنظیمات تلگرام کامل نیست.")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendMessage"
    )

    try:
        response = session.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": message,
            },
            timeout=TIMEOUT,
        )

        print("Telegram HTTP:", response.status_code)

        return response.ok

    except Exception as error:
        print("خطای Telegram:", error)
        return False


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def save_message(message):
    record = {
        "received_at": now_iso(),
        "message": message,
    }

    with open(
        OUTPUT_FILE,
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


def on_open(ws):

    print("=" * 60)
    print("اتصال به WebSocket اهرم حرفه‌ای برقرار شد.")
    print("آدرس:", WS_URL)
    print("=" * 60)

    params = [
        symbol + "@depth@2000ms"
        for symbol in SYMBOLS
    ]

    request = {
        "method": "SUBSCRIBE",
        "params": params,
        "id": 1,
    }

    print("ارسال درخواست اشتراک:")

    print(
        json.dumps(
            request,
            ensure_ascii=False,
        )
    )

    ws.send(
        json.dumps(request)
    )


def on_message(ws, message):

    print("\n----- پیام جدید -----")

    print(
        message[:3000]
    )

    try:

        parsed = json.loads(message)

        save_message(parsed)

    except Exception:

        save_message(message)


def on_error(ws, error):

    print(
        "WebSocket ERROR:",
        error,
    )


def on_close(
    ws,
    close_status_code,
    close_msg,
):

    print(
        "WebSocket بسته شد."
    )

    print(
        "کد:",
        close_status_code,
    )

    print(
        "پیام:",
        close_msg,
    )


def run_probe():

    print("=" * 60)
    print("TABDEAL PROFESSIONAL MARGIN DATA PROBE")
    print("=" * 60)

    print(
        "مدت اجرا:",
        RUN_SECONDS,
        "ثانیه",
    )

    print(
        "ارزها:",
        ", ".join(SYMBOLS),
    )

    print(
        "فایل ذخیره:",
        OUTPUT_FILE,
    )

    ws = websocket.WebSocketApp(

        WS_URL,

        on_open=on_open,

        on_message=on_message,

        on_error=on_error,

        on_close=on_close,
    )

    start_time = time.time()

    def stopper():

        remaining = (
            RUN_SECONDS
            - (
                time.time()
                - start_time
            )
        )

        if remaining > 0:

            time.sleep(
                remaining
            )

        try:

            ws.close()

        except Exception:

            pass

    threading.Thread(
        target=stopper,
        daemon=True,
    ).start()

    ws.run_forever(
        ping_interval=25,
        ping_timeout=10,
    )

    print("=" * 60)
    print("مرحله تست اتصال تمام شد.")
    print(
        "پیام‌های دریافت‌شده در:",
        OUTPUT_FILE,
    )
    print("=" * 60)


def main():

    send_telegram(
        "\n".join(
            [
                "🔌 شروع تست اتصال Tabdeal",
                "",
                "📡 بازار: اهرم حرفه‌ای",
                "🪙 BTC / ETH / BNB / SOL / XRP",
                "📥 حالت: دریافت داده بازار",
                "",
                "⚠️ هیچ معامله‌ای انجام نمی‌شود.",
            ]
        )
    )

    try:

        run_probe()

        send_telegram(
            "\n".join(
                [
                    "✅ تست اتصال Tabdeal تمام شد.",
                    "",
                    "📡 بازار: اهرم حرفه‌ای",
                    "📁 داده خام ذخیره شد.",
                    "",
                    "مرحله بعد: بررسی داده و ساخت کندل 5m و 15m.",
                ]
            )
        )

    except Exception as error:

        print(
            "خطای اصلی:",
            error,
        )

        send_telegram(
            "\n".join(
                [
                    "❌ تست اتصال Tabdeal با خطا متوقف شد.",
                    "",
                    "خطا:",
                    str(error)[:1000],
                ]
            )
        )

        raise


if __name__ == "__main__":

    main()
