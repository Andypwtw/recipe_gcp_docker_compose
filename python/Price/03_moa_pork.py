import json
import ssl
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# 自動抓取「執行日期前 30 天」的資料
TARGET_DATE = date.today() - timedelta(days=30)

# 西元日期轉成民國年月日，例如 2026-08-22 -> 1150822
TRANS_DATE = (
    f"{TARGET_DATE.year - 1911:03d}"
    f"{TARGET_DATE.month:02d}"
    f"{TARGET_DATE.day:02d}"
)

OUTPUT_FILE = Path(__file__).with_name(
    f"03_moa_pork.json"
)

API_URL = "https://data.moa.gov.tw/api/v1/PorkTransType/"


# 在 VS Code 中可用 FIELD_NAMES["SpecPig_AvgPrice"] 查詢中文欄位名稱。
FIELD_NAMES = {
    "TransDate": "交易日期",
    "MarketName": "市場名稱",
    "TransNum_Total": "成交頭數總數",
    "TransNum_AvgWgt": "成交頭數平均重量",
    "TransNum_AvgPrice": "成交頭數平均價格",
    "SpecPig_Num": "規格豬頭數",
    "SpecPig_AvgWgt": "規格豬平均重量",
    "SpecPig_AvgPrice": "規格豬平均價格",
    "Num_95in_115in": "95(含)-115(含)頭數",
    "AvgWgt_95in_115in": "95(含)-115(含)平均重量",
    "AvgPrice_95in_115in": "95(含)-115(含)平均價格",
    "Num_75in_95": "75(含)-95(不含)頭數",
    "AvgWgt_75in_95": "75(含)-95(不含)平均重量",
    "AvgPrice_75in_95": "75(含)-95(不含)平均價格",
    "Num_115up": "115(含)-135(不含)頭數",
    "AvgWgt_115up": "115(含)-135(不含)平均重量",
    "AvgPrice_115up": "115(含)-135(不含)平均價格",
    "Num_75low": "75公斤以下頭數",
    "AvgWgt_75low": "75公斤以下平均重量",
    "AvgPrice_75low": "75公斤以下平均價格",
    "OutPigs_Num": "淘汰種豬頭數",
    "OutPigs_AvgWgt": "淘汰種豬平均重量",
    "OutPigs_AvgPrice": "淘汰種豬平均價格",
    "OtherPigs_Num": "其他豬頭數",
    "OtherPigs_AvgWgt": "其他豬平均重量",
    "OtherPigs_AvgPrice": "其他豬平均價格",
    "FreezerPigs_Num": "冷凍廠頭數",
    "FreezerPigs_AvgWgt": "冷凍廠平均重量",
    "FreezerPigs_AvgPrice": "冷凍廠平均價格",
    "TotalTrans_ExcludeFreezer_Num": "成交總數(不含冷凍廠)頭數",
    "TotalTrans_ExcludeFreezer_AvgWeight": "成交總數(不含冷凍廠)平均重量",
    "TotalTrans_ExcludeFreezer_AvgPrice": "成交總數(不含冷凍廠)平均價格",
    "KgPig5_Q": "135(含)-155(不含)頭數",
    "KgPig5_W": "135(含)-155(不含)平均重量",
    "KgPig5_P": "135(含)-155(不含)平均價格",
    "KgPig6_Q": "155公斤以上頭數",
    "KgPig6_W": "155公斤以上平均重量",
    "KgPig6_P": "155公斤以上平均價格",
}

SELECTED_FIELDS = list(FIELD_NAMES)


def fetch_page(page: int | None = None) -> dict:
    """取得 API 的一頁資料。"""
    params = {"TransDate": TRANS_DATE}

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
        raise SystemExit(
            f"API request failed: HTTP {error.code} {error.reason}"
        )

    except URLError as error:
        raise SystemExit(f"Unable to reach the API: {error.reason}")

    except json.JSONDecodeError:
        raise SystemExit("The API response was not valid JSON.")


def main() -> None:
    records = []
    page = None

    print(f"查詢西元日期：{TARGET_DATE.strftime('%Y/%m/%d')}")
    print(f"查詢民國日期：{TRANS_DATE}")
    print(f"API URL：{API_URL}?TransDate={TRANS_DATE}")

    # API 若回傳 Next=true，代表仍有下一頁資料。
    while True:
        payload = fetch_page(page)

        records.extend(
            {
                field: item.get(field)
                for field in SELECTED_FIELDS
            }
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
