#!/usr/bin/env python3
"""篩選 Avg_Price 大於 0 的農產品資料，並保留對應的 CropName。"""

import json
from pathlib import Path


# 原始 MOA 資料與此篩選程式都位於同一個 Price 資料夾。
BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "01_moa_agri.json"
OUTPUT_PATH = BASE_DIR / "01_moa_agri_avgprice.json"

# 將欄位名稱集中定義，日後調整資料格式時更容易維護。
PRICE_FIELD = "Avg_Price"
CROP_NAME_FIELD = "CropName"


def main() -> None:
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(f"找不到農產品來源 JSON 檔案：{INPUT_PATH}")

    # 讀取原始 JSON 陣列。
    with INPUT_PATH.open("r", encoding="utf-8") as input_file:
        records = json.load(input_file)

    # 記錄已輸出的品項名稱，用於刪除同一品項的重複資料。
    seen_crop_names = set()

    # 僅保留 Avg_Price 大於 0 的資料，並輸出所需的兩個欄位。
    output_records = []
    for record in records:
        price = float(record.get(PRICE_FIELD, 0) or 0)
        # 以第一個「-」左側名稱作為品項，例如「百香果-改良種」視為「百香果」。
        crop_name = str(record.get(CROP_NAME_FIELD, "")).split("-", 1)[0].strip()

        # 價格須大於 0，且同一品項只保留第一筆。
        if price > 0 and crop_name and crop_name not in seen_crop_names:
            output_records.append(
                {
                    PRICE_FIELD: price,
                    CROP_NAME_FIELD: crop_name,
                }
            )
            seen_crop_names.add(crop_name)

    # 以縮排格式寫入 JSON，方便人工檢閱。
    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(output_records, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"已輸出 {len(output_records)} 筆資料至：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
