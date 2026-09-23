import json
import websocket

URL = "wss://api1.tabdeal.org/special_margin/stream/"

SUBSCRIBE = {
    "method": "SUBSCRIBE",
    "id": 1,
    "params": ["BTC_USDT@depth@2000ms"],
}

messages = 0


def on_open(ws):
    print("WEBSOCKET CONNECTED")
    ws.send(json.dumps(SUBSCRIBE))
    print("SUBSCRIBE SENT")


def on_message(ws, message):
    global messages

    messages += 1

    print("\n--- MESSAGE", messages, "---")
    print(message[:3000])

    if messages >= 5:
        print("\nReceived 5 live messages.")
        ws.close()


def on_error(ws, error):
    print("WEBSOCKET ERROR:", error)


def on_close(ws, close_status_code, close_msg):
    print("WEBSOCKET CLOSED:", close_status_code, close_msg)


ws = websocket.WebSocketApp(
    URL,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
)

try:
    ws.run_forever(
        ping_interval=20,
        ping_timeout=10
    )
except Exception as e:
    print("RUN ERROR:", e)


print("\nTOTAL LIVE MESSAGES:", messages)

if messages == 0:
    print("NO LIVE MESSAGES RECEIVED.")
else:
    print("TABDEAL BTC_USDT WEBSOCKET IS WORKING.")
