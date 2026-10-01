import requests
import json

BASE = "https://api1.tabdeal.org"


def test(name, url):
    print("\n================================")
    print(name)
    print(url)
    print("================================")

    try:
        r = requests.get(url, timeout=20)

        print("HTTP STATUS:", r.status_code)
        print("RESPONSE:")
        print(r.text[:5000])

    except Exception as e:
        print("ERROR:", repr(e))


def main():

    print("====================================")
    print("TABDEAL MARGIN MARKET TEST")
    print("====================================")

    # همه دارایی‌های اهرم‌دار
    test(
        "MARGIN ALL ASSETS",
        BASE + "/api/v1/margin/allAssets"
    )

    # اطلاعات بازار BTC به شکل قدیمی
    test(
        "BTCIRT EXCHANGE INFO",
        BASE + "/api/v1/exchangeInfo?symbol=BTCIRT"
    )

    # اطلاعات بازار BTC با فرمت underscore
    test(
        "BTC_IRT EXCHANGE INFO",
        BASE + "/api/v1/exchangeInfo?symbol=BTC_IRT"
    )

    # آخرین معاملات BTCIRT
    test(
        "BTCIRT TRADES",
        BASE + "/api/v1/trades?symbol=BTCIRT&limit=10"
    )

    print("\n====================================")
    print("TEST FINISHED")
    print("====================================")


if __name__ == "__main__":
    main()
