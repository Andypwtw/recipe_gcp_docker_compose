"""依 SeafoodProdName 去除重複漁產品資料，保留第一筆 Avg_Price。"""

import json
from pathlib import Path


# 來源資料與輸出檔案都位於此篩選程式所在的 Price 資料夾。
BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "02_moa_fisher.json"
OUTPUT_PATH = BASE_DIR / "02_moa_fisher_avgprice.json"

# 將使用的欄位名稱集中定義，方便日後維護。
SEAFOOD_NAME_FIELD = "SeafoodProdName"
PRICE_FIELD = "Avg_Price"


def main() -> None:
    # 讀取原始 JSON 陣列。
    with INPUT_PATH.open("r", encoding="utf-8") as input_file:
        records = json.load(input_file)

    # 依品名去重；同名資料只保留原始資料中最先出現的一筆。
    seen_seafood_names = set()
    output_records = []
    for record in records:
        seafood_name = str(record.get(SEAFOOD_NAME_FIELD, "")).strip()
        if seafood_name and seafood_name not in seen_seafood_names:
            output_records.append(
                {
                    SEAFOOD_NAME_FIELD: seafood_name,
                    PRICE_FIELD: record.get(PRICE_FIELD),
                }
            )
            seen_seafood_names.add(seafood_name)

    # 以 UTF-8 與縮排格式輸出，保留品名與其 Avg_Price。
    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(output_records, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"已輸出 {len(output_records)} 筆不重複品名資料至：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
