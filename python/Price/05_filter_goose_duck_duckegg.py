"""計算鵝、鴨與鴨蛋指定價格欄位的平均值，每個欄位只輸出一次。"""

import json
from pathlib import Path


# 來源資料與輸出檔案都位於此篩選程式所在的 Price 資料夾。
BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "05_moa_goose_duck_duckegg.json"
OUTPUT_PATH = BASE_DIR / "05_moa_goose_duck_duckegg_avgprice.json"

# 需要計算平均值的價格欄位。
PRICE_FIELDS = (
    "Goose_WR_TaijinPrice",
    "Duck_75D_TaijinPrice",
    "Duckegg_TNN_TaijinPrice",
)


def numeric_values(records: list[dict], field_name: str) -> list[float]:
    """取得欄位中的數字價格，略過「休市」或空白等非數字內容。"""
    values = []
    for record in records:
        try:
            values.append(float(record.get(field_name, "")))
        except (TypeError, ValueError):
            continue
    return values


def main() -> None:
    # 讀取原始每日交易資料。
    with INPUT_PATH.open("r", encoding="utf-8") as input_file:
        records = json.load(input_file)

    # 各欄位只計算並保留一個平均值，故不會有重複資料。
    output_data = {}
    for field_name in PRICE_FIELDS:
        values = numeric_values(records, field_name)
        if not values:
            raise ValueError(f"{field_name} 找不到可計算的數字價格。")
        output_data[field_name] = round(sum(values) / len(values), 2)

    # 以縮排格式輸出三個欄位的平均值。
    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(output_data, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"已輸出平均價格至：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
