import json
import ssl
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OUTPUT_FILE = Path(__file__).with_name("02_moa_fisher.json")
API_URL = "https://data.moa.gov.tw/api/v1/FisheryProductsTransType/"

# 在 VS Code 中可用 FIELD_NAMES["Avg_Price"] 查詢中文欄位名稱。
FIELD_NAMES = {
    "SeafoodProdCode": "漁產品代碼",
    "SeafoodProdName": "漁產品名稱(含各語系)",
    "MarketName": "市場名稱(含各語系)",
    "TransDate": "交易日期(民國年，例如107-05-01)",
    "Upper_Price": "上價(元/公斤)",
    "Middle_Price": "中價(元/公斤)",
    "Lower_Price": "下價(元/公斤)",
    "Trans_Quantity": "交易量(公斤)",
    "Avg_Price": "平均價(元/公斤)",
}

SELECTED_FIELDS = list(FIELD_NAMES)


def to_roc_date(value: date) -> str:
    """將西元日期轉為 API 所需的民國格式，例如 2026-08-08 -> 1150808。"""
    return f"{value.year - 1911:03d}{value.month:02d}{value.day:02d}"


def date_range(today: date | None = None) -> tuple[str, str]:
    """回傳 API 所需區間：執行日前 44 天到前 30 天。"""
    run_date = today or date.today()
    end_date = run_date - timedelta(days=30)
    start_date = end_date - timedelta(days=14)
    return to_roc_date(start_date), to_roc_date(end_date)


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

    # 部分 macOS Python 會拒絕此 API 的憑證；此設定僅限本 API 使用。
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
        records.extend(
            {field: item.get(field) for field in SELECTED_FIELDS}
            for item in payload.get("Data", [])
        )

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
