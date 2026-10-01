import json
import time
import requests
import websocket

BASE_REST = "https://api1.tabdeal.org"
WS_STREAM = "wss://api1.tabdeal.org/special_margin/stream/"
WS_BROADCAST = "wss://api1.tabdeal.org/special_margin/broadcast/"


def rest_test(path):
    url = BASE_REST + path

    print("\n==============================")
    print("REST TEST")
    print(url)
    print("==============================")

    try:
        response = requests.get(
            url,
            timeout=15,
        )

        print("HTTP STATUS:", response.status_code)
        print("RESPONSE:")
        print(response.text[:3000])

    except Exception as e:
        print("REST ERROR:", repr(e))


def websocket_test(name, url):
    print("\n==============================")
    print("WEBSOCKET TEST:", name)
    print(url)
    print("==============================")

    received = []

    def on_open(ws):
        print("CONNECTED")

        payload = {
            "method": "SUBSCRIBE",
            "params": [
                "btcusdt@depth@2000ms"
            ],
            "id": 1,
        }

        ws.send(json.dumps(payload))

        print(
            "SUBSCRIBE SENT:",
            json.dumps(payload)
        )

    def on_message(ws, message):
        print(
            "MESSAGE:",
            message[:3000]
            if isinstance(message, str)
            else message
        )

        received.append(message)

        if len(received) >= 3:
            ws.close()

    def on_error(ws, error):
        print("ERROR:", repr(error))

    def on_close(ws, code, msg):
        print(
            "CLOSED:",
            code,
            msg
        )

    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    try:
        ws.run_forever(
            ping_interval=20,
            ping_timeout=10,
        )
    except Exception as e:
        print(
            "RUN ERROR:",
            repr(e)
        )

    print(
        "TOTAL MESSAGES:",
        len(received)
    )


def main():

    print("====================================")
    print("TABDEAL FUTURES CONNECTION TEST")
    print("====================================")

    # REST tests
    rest_test(
        "/r/fapi/v1/ping"
    )

    rest_test(
        "/r/fapi/v1/time"
    )

    rest_test(
        "/r/fapi/v1/exchangeInfo?symbol=BTCUSDT"
    )

    rest_test(
        "/r/fapi/v1/depth?symbol=BTCUSDT&limit=50"
    )

    # WebSocket normal Futures
    websocket_test(
        "FUTURES STREAM",
        WS_STREAM
    )

    # WebSocket broadcast Futures
    websocket_test(
        "FUTURES BROADCAST",
        WS_BROADCAST
    )

    print("\n====================================")
    print("TEST FINISHED")
    print("====================================")


if __name__ == "__main__":
    main()
