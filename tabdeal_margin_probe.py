import os
import json
import requests
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

URLS = [
    "https://api1.tabdeal.org/r/fapi/v1/exchangeInfo",
    "https://api1.tabdeal.org/fapi/v1/exchangeInfo",
]

RAW_FILE = "tabdeal_margin_exchange_info.json"

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing.")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=20,
        )
    except Exception as e:
        print("Telegram error:", e)

def collect_symbols(obj, found=None):
    if found is None:
        found = set()

    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = str(key).lower()
            if key_lower in {
                "symbol", "tabdealsymbol", "pair", "market",
                "market_symbol", "instrument", "instrumentid"
            }:
                if isinstance(value, str) and value.strip():
                    found.add(value.strip())

            collect_symbols(value, found)

    elif isinstance(obj, list):
        for item in obj:
            collect_symbols(item, found)

    return found

def main():
    print("=== TABDEAL PROFESSIONAL MARGIN SYMBOL PROBE ===")

    all_results = []
    all_symbols = set()

    for url in URLS:
        print("\nREQUEST:", url)

        try:
            response = requests.get(url, timeout=25)
            print("HTTP STATUS:", response.status_code)

            result = {
                "url": url,
                "status_code": response.status_code,
                "ok": response.ok,
            }

            try:
                data = response.json()
                result["json"] = data

                if isinstance(data, dict):
                    result["top_level_keys"] = list(data.keys())

                symbols = sorted(collect_symbols(data))
                result["detected_symbols"] = symbols

                all_symbols.update(symbols)

                print("RESPONSE TYPE:", type(data).__name__)
                if isinstance(data, dict):
                    print("TOP LEVEL KEYS:", list(data.keys()))
                print("DETECTED SYMBOLS:", symbols[:100])

            except Exception as e:
                result["json_error"] = str(e)
                result["raw_text"] = response.text[:10000]
                print("JSON ERROR:", e)
                print("RAW:", response.text[:2000])

            all_results.append(result)

        except Exception as e:
            print("REQUEST ERROR:", e)
            all_results.append({
                "url": url,
                "request_error": str(e),
            })

    output = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "results": all_results,
        "all_detected_symbols": sorted(all_symbols),
    }

    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    symbols = sorted(all_symbols)

    if symbols:
        shown = symbols[:80]
        message = (
            "🔎 نتیجه بررسی بازار اهرم حرفه‌ای Tabdeal\n\n"
            f"✅ تعداد نمادهای پیدا شده: {len(symbols)}\n\n"
            "🪙 نمادهای شناسایی‌شده:\n"
            + "\n".join(shown)
        )
        if len(symbols) > 80:
            message += f"\n\n... و {len(symbols) - 80} نماد دیگر"
    else:
        message = (
            "⚠️ هنوز هیچ نماد قابل شناسایی از بازار اهرم حرفه‌ای پیدا نشد.\n\n"
            "اطلاعات خام exchangeInfo ذخیره شد تا ساختار واقعی پاسخ را بررسی کنیم.\n"
            "فایل: tabdeal_margin_exchange_info.json"
        )

    send_telegram(message)

    print("\n=== FINISHED ===")
    print("TOTAL UNIQUE SYMBOLS:", len(symbols))
    print("RAW FILE:", RAW_FILE)

if __name__ == "__main__":
    main()
                
