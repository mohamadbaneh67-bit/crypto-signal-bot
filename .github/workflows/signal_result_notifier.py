import os
import json
import requests

SIGNALS_FILE = "tabdeal_signals.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SENT_FILE = "tabdeal_sent_results.json"


def load_json(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def send_telegram(text):

    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=20
        )

        return response.ok

    except Exception as e:
        print("Telegram error:", e)
        return False


def result_text(signal):

    symbol = signal.get("symbol", "?")
    direction = signal.get("direction", "?")
    entry = signal.get("entry", 0)
    tp1 = signal.get("tp1", 0)
    tp2 = signal.get("tp2", 0)
    sl = signal.get("stop_loss", 0)

    result = str(
        signal.get("result", "")
    ).lower()

    if result == "tp1":
        title = "🟢 نتیجه: TP1"
        reason = (
            "قیمت در جهت سیگنال حرکت کرده "
            "و هدف اول را لمس کرده است."
        )

    elif result == "tp2":
        title = "🟢 نتیجه: TP2"
        reason = (
            "قیمت در جهت سیگنال حرکت کرده "
            "و هدف دوم را لمس کرده است."
        )

    elif result == "sl":
        title = "🔴 نتیجه: SL"
        reason = (
            "قیمت برخلاف جهت سیگنال حرکت کرده "
            "و حد ضرر فعال شده است."
        )

    elif result == "ambiguous":
        title = "🟡 نتیجه: AMBIGUOUS"
        reason = (
            "در یک کندل هم محدوده هدف و هم حد ضرر "
            "لمس شده و از داده OHLC نمی‌توان ترتیب "
            "وقوع آنها را با قطعیت مشخص کرد."
        )

    else:
        return None

    return (
        "📊 نتیجه سیگنال Tabdeal\n\n"
        f"🪙 نماد: {symbol}\n"
        f"📈 جهت: {direction}\n\n"
        f"💰 ورود: {entry}\n"
        f"🎯 TP1: {tp1}\n"
        f"🎯 TP2: {tp2}\n"
        f"🛑 SL: {sl}\n\n"
        f"{title}\n\n"
        f"🔎 علت:\n{reason}\n\n"
        "⏱ تحلیل: 5m + 15m\n"
        "⚠️ این نتیجه مربوط به تحلیل است؛ "
        "ربات هیچ معامله‌ای انجام نداده است."
    )


def main():

    signals = load_json(
        SIGNALS_FILE,
        []
    )

    sent = load_json(
        SENT_FILE,
        []
    )

    if not isinstance(signals, list):
        signals = []

    if not isinstance(sent, list):
        sent = []

    changed = False

    for signal in signals:

        result = str(
            signal.get("result", "")
        ).lower()

        if result not in {
            "tp1",
            "tp2",
            "sl",
            "ambiguous"
        }:
            continue

        signal_id = signal.get("id")

        if not signal_id:
            continue

        if signal_id in sent:
            continue

        message = result_text(signal)

        if not message:
            continue

        if send_telegram(message):

            sent.append(signal_id)
            changed = True

            print(
                "Result sent:",
                signal_id
            )

    if changed:
        save_json(
            SENT_FILE,
            sent
        )

    print("Result notifier finished.")


if __name__ == "__main__":
    main()
