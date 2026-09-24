"""抓取白肉雞與雞蛋交易資料。

資料區間固定為「執行日 30 天前」到「再往前推 14 天」；例如 2026-09-21
執行時，會查詢 2026/08/08 至 2026/08/22。
"""

from __future__ import annotations

import json
import ssl
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://data.moa.gov.tw/api/v1/PoultryTransType_BoiledChicken_Eggs/"
OUTPUT_FILE = Path(__file__).with_name("04_moa_poultry_eggs.json")


def date_range(today: date | None = None) -> tuple[str, str]:
    """回傳 API 所需的西元日期區間：44 天前到 30 天前。"""
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

    # 此端點的憑證在部分 macOS Python 版本可能無法通過驗證。
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
    start_time, end_time = date_range()
    records: list[dict] = []
    page: int | None = None

    print(f"查詢期間：{start_time} 至 {end_time}")
    while True:
        payload = fetch_page(start_time, end_time, page)
        records.extend(payload.get("Data", []))

        if not payload.get("Next", False):
            break
        page = 2 if page is None else page + 1

    OUTPUT_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已輸出 {len(records)} 筆資料至：{OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
