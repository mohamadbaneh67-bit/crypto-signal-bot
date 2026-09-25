import json
import time

from tabdeal.websocket_client import FutureWebsocketClient


SYMBOLS = [
    "btcusdt",
    "ethusdt",
    "adausdt",
    "dogeusdt",
    "solusdt",
]


def handler(message):
    print()
    print("=" * 70)
    print("LIVE TABDEAL FUTURES MESSAGE")
    print("=" * 70)

    try:
        if isinstance(message, str):
            data = json.loads(message)
        else:
            data = message

        print(json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ))

    except Exception as exc:
        print("MESSAGE ERROR:", exc)
        print(message)


print("=" * 70)
print("TABDEAL FUTURES WEBSOCKET TEST")
print("5 COINS")
print("ANALYSIS ONLY - NO TRADE")
print("=" * 70)

ws = FutureWebsocketClient()

for index, symbol in enumerate(SYMBOLS, start=1):

    print(
        f"SUBSCRIBING {index}/{len(SYMBOLS)}:",
        symbol
    )

    try:

        ws.market_order_book(
            symbol=symbol,
            id=index,
            callback=handler,
        )

        print(
            "SUBSCRIBE OK:",
            symbol
        )

    except Exception as exc:

        print(
            "SUBSCRIBE ERROR:",
            symbol,
            exc
        )


print()
print("Waiting for live Futures data...")
print("The program will wait for 30 seconds.")

time.sleep(30)

print()
print("=" * 70)
print("FUTURES WEBSOCKET TEST FINISHED")
print("=" * 70)
