"""從農業部 SheepQuotation API 抓取資料並輸出為 JSON 檔。"""

import json
import ssl
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# API 位址與輸出檔案位置。
API_URL = "https://data.moa.gov.tw/api/v1/SheepQuotation/"
OUTPUT_PATH = Path(__file__).with_name("06_moa_sheep.json")


def date_range(today: date | None = None) -> tuple[str, str]:
    """回傳 API 所需區間：執行日前 44 天到前 30 天。"""
    run_date = today or date.today()
    end_date = run_date - timedelta(days=30)
    start_date = end_date - timedelta(days=14)
    return start_date.strftime("%Y/%m/%d"), end_date.strftime("%Y/%m/%d")


def main() -> None:
    start_time, end_time = date_range()
    # 將查詢日期區間進行 URL 編碼，組成完整 API 請求網址。
    query = urlencode({"Start_time": start_time, "End_time": end_time})
    request = Request(
        f"{API_URL}?{query}",
        headers={"accept": "application/json"},
    )

    # 該 API 的憑證缺少 Subject Key Identifier。新版 Python 預設會嚴格檢查
    # 此欄位，因此僅關閉這一項額外檢查；仍保留一般 HTTPS 憑證鏈驗證。
    ssl_context = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    print(f"查詢期間：{start_time} 至 {end_time}")
    try:
        # 呼叫 API 並將回應文字轉為 Python 字典。
        with urlopen(request, timeout=30, context=ssl_context) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise SystemExit(f"API request failed: HTTP {error.code} {error.reason}")
    except URLError as error:
        raise SystemExit(f"Unable to reach the API: {error.reason}")
    except json.JSONDecodeError:
        raise SystemExit("The API response was not valid JSON.")

    # API 成功時會回傳 RS: OK；失敗時停止並顯示回傳訊息。
    if payload.get("RS") != "OK":
        raise RuntimeError(f"API 回傳失敗：{payload}")

    # 只輸出實際交易資料陣列，不保留 RS 等 API 狀態資訊。
    records = payload.get("Data", [])
    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(records, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"已抓取 {len(records)} 筆資料至：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
