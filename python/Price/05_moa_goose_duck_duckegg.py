import json
import ssl
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OUTPUT_FILE = Path(__file__).with_name("05_moa_goose_duck_duckegg.json")
API_URL = "https://data.moa.gov.tw/api/v1/PoultryTransType_Goose_Duck_Duckegg/"

# 在 VS Code 中可用 FIELD_NAMES["Goose_WR_TaijinPrice"] 查詢中文欄位名稱。
FIELD_NAMES = {
    "Start_time": "交易日期(起)",
    "End_time": "交易日期(迄)",
    "TransDate": "交易日期",
    "LunarCalendar": "農曆",
    "Goose_WR_TaijinPrice": "肉鵝白羅曼(元/台斤)",
    "Duck_M_TaijinPrice": "正番鴨公(元/台斤)",
    "Duck_75D_TaijinPrice": "土番鴨75天(元/台斤)",
    "Duckegg_TNN_TaijinPrice": "鴨蛋新蛋台南(元/台斤)",
}

API_FIELDS = [field for field in FIELD_NAMES if field not in {"Start_time", "End_time"}]


def date_range(today: date | None = None) -> tuple[str, str]:
    """回傳 API 所需的西元日期區間：執行日前 44 天到前 30 天。"""
    run_date = today or date.today()
    end_date = run_date - timedelta(days=30)
    start_date = end_date - timedelta(days=14)
    return start_date.strftime("%Y/%m/%d"), end_date.strftime("%Y/%m/%d")


def fetch_page(start_time: str, end_time: str, page: int | None = None) -> dict:
    """取得 API 的一頁資料。"""
    params = {"Start_time": start_time, "End_time": end_time}
    if page is not None:
        params["Page"] = str(page)

    request = Request(
        f"{API_URL}?{urlencode(params)}",
        headers={"Accept": "application/json"},
        method="GET",
    )

    # 此 API 的憑證在部分 macOS Python 版本會出現相容性問題。
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

    # API 若回傳 Next=true，代表仍有下一頁資料。
    while True:
        payload = fetch_page(start_time, end_time, page)
        for item in payload.get("Data", []):
            # 起迄日為查詢條件，可能不在原始單筆資料中，因此統一補入輸出。
            records.append({
                "Start_time": start_time,
                "End_time": end_time,
                **{field: item.get(field) for field in API_FIELDS},
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
