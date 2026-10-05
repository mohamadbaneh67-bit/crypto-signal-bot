import json
import requests
from datetime import datetime

BASE_URL = "https://api1.tabdeal.org"


def test(name, path, params=None):
    url = BASE_URL + path

    print("\n" + "=" * 70)
    print(name)
    print("URL:", url)
    print("PARAMS:", params)

    try:
        response = requests.get(
            url,
            params=params,
            timeout=20
        )

        print("HTTP STATUS:", response.status_code)

        try:
            data = response.json()

            print(json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ))

        except Exception:
            print("RAW RESPONSE:")
            print(response.text[:5000])

    except Exception as e:
        print("ERROR:", repr(e))


def main():

    print("=" * 70)
    print("TABDEAL MARGIN KLINE TEST")
    print("TIME:", datetime.utcnow().isoformat())
    print("=" * 70)

    test(
        "1 - MARGIN ALL ASSETS",
        "/api/v1/margin/allAssets"
    )

    test(
        "2 - BTCUSDT EXCHANGE INFO",
        "/r/api/v1/exchangeInfo",
        {
            "symbol": "BTCUSDT"
        }
    )

    test(
        "3 - BTCUSDT TRADES",
        "/r/api/v1/trades",
        {
            "symbol": "BTCUSDT",
            "limit": 20
        }
    )

    test(
        "4 - STANDARD KLINES 5m",
        "/r/api/v1/klines",
        {
            "symbol": "BTCUSDT",
            "interval": "5m",
            "limit": 10
        }
    )

    test(
        "5 - STANDARD KLINES 15m",
        "/r/api/v1/klines",
        {
            "symbol": "BTCUSDT",
            "interval": "15m",
            "limit": 10
        }
    )

    test(
        "6 - MARGIN KLINES 5m",
        "/r/api/v1/margin/klines",
        {
            "symbol": "BTCUSDT",
            "interval": "5m",
            "limit": 10
        }
    )

    test(
        "7 - MARGIN KLINES 15m",
        "/r/api/v1/margin/klines",
        {
            "symbol": "BTCUSDT",
            "interval": "15m",
            "limit": 10
        }
    )

    test(
        "8 - CANDLESTICK 5m",
        "/r/api/v1/candles",
        {
            "symbol": "BTCUSDT",
            "interval": "5m",
            "limit": 10
        }
    )

    print("\n" + "=" * 70)
    print("TEST FINISHED")
    print("=" * 70)


if __name__ == "__main__":
    main()
