import json
import ssl
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OUTPUT_FILE = Path(__file__).with_name("01_moa_agri.json")
API_URL = "https://data.moa.gov.tw/api/v1/AgriProductsTransType/"

# 在 VS Code 中可用 FIELD_NAMES["CropName"] 查詢中文欄位名稱。
FIELD_NAMES = {
    "Start_time": "交易日期(起)",
    "End_time": "交易日期(迄)",
    "CropCode": "農產品代碼",
    "CropName": "農產品名稱",
    "Avg_Price": "平均價(元/公斤)",
}


def to_roc_date(value: date) -> str:
    """將西元日期轉為 API 所需的民國格式，例如 2026-08-08 -> 115.08.08。"""
    return f"{value.year - 1911:03d}.{value.month:02d}.{value.day:02d}"


def date_range(today: date | None = None) -> tuple[str, str]:
    """回傳 API 所需區間：執行日前 44 天到前 30 天。"""
    run_date = today or date.today()
    end_date = run_date - timedelta(days=30)
    start_date = end_date - timedelta(days=14)
    return to_roc_date(start_date), to_roc_date(end_date)


def fetch_page(start_time: str, end_time: str, page: int | None = None) -> dict:
    """取得一頁 API 資料。"""
    params = {"Start_time": start_time, "End_time": end_time}
    if page is not None:
        params["Page"] = str(page)

    request = Request(
        f"{API_URL}?{urlencode(params)}",
        headers={"Accept": "application/json"},
        method="GET",
    )

    # 此端點的憑證在部分 macOS Python 版本無法通過驗證。
    context = ssl._create_unverified_context()
    try:
        with urlopen(request, timeout=30, context=context) as response:
            return json.load(response)
    except HTTPError as error:
        raise SystemExit(f"API request failed: HTTP {error.code} {error.reason}")
    except URLError as error:
        raise SystemExit(f"Unable to reach the API: {error.reason}")
    except json.JSONDecodeError:
        raise SystemExit("The API response was not valid JSON.")


def main() -> None:
    records = []
    page = None
    start_time, end_time = date_range()

    print(f"查詢期間：{start_time} 至 {end_time}")

    # API 每頁最多回傳 1,000 筆。只要 Next 為 true，就讀取下一頁。
    while True:
        payload = fetch_page(start_time, end_time, page)
        for item in payload.get("Data", []):
            # Start_time、End_time 是查詢條件，不會出現在 API 的每筆回傳資料，
            # 因此在輸出時補入，讓每筆資料都有五個指定欄位。
            records.append({
                "Start_time": start_time,
                "End_time": end_time,
                "CropCode": item.get("CropCode"),
                "CropName": item.get("CropName"),
                "Avg_Price": item.get("Avg_Price"),
            })

        if not payload.get("Next", False):
            break
        page = 2 if page is None else page + 1

    OUTPUT_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(records)} records to: {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
