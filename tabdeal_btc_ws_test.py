import json
import time
import websocket

URL = "wss://api1.tabdeal.org/stream/"

SUBSCRIBE = {
    "method": "SUBSCRIBE",
    "params": [
        "btcusdt@depth@2000ms"
    ],
    "id": 1
}

def on_open(ws):
    print("WEBSOCKET CONNECTED")
    ws.send(json.dumps(SUBSCRIBE))
    print("SUBSCRIBE SENT")

def on_message(ws, message):
    print("MESSAGE RECEIVED:")
    print(message)

def on_error(ws, error):
    print("WEBSOCKET ERROR:")
    print(error)

def on_close(ws, close_status_code, close_msg):
    print("WEBSOCKET CLOSED")
    print(close_status_code)
    print(close_msg)

print("Starting Tabdeal WebSocket test...")
print("URL:", URL)

ws = websocket.WebSocketApp(
    URL,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close
)

ws.run_forever(
    ping_interval=20,
    ping_timeout=10
)
