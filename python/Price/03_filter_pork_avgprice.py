"""篩選 TransNum_AvgPrice 大於 0 的資料，並新增其平均值欄位。"""

import json
from pathlib import Path


INPUT_PATH = Path("/Users/amy/TKR102/Price/03_moa_pork.json")
OUTPUT_PATH = Path(__file__).with_name("03_moa_pork_avgprice.json")
PRICE_FIELD = "TransNum_AvgPrice"
AVERAGE_FIELD = "TransNum_AvgPrice_Average"


def main() -> None:
    with INPUT_PATH.open("r", encoding="utf-8") as input_file:
        records = json.load(input_file)

    positive_prices = [
        float(record[PRICE_FIELD])
        for record in records
        if float(record.get(PRICE_FIELD, 0) or 0) > 0
    ]

    if not positive_prices:
        raise ValueError(f"找不到 {PRICE_FIELD} 大於 0 的資料。")

    average_price = round(sum(positive_prices) / len(positive_prices), 2)
    output_data = {
        PRICE_FIELD: positive_prices,
        AVERAGE_FIELD: average_price,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(output_data, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"已輸出 {len(positive_prices)} 筆資料至：{OUTPUT_PATH}")
    print(f"{PRICE_FIELD} 平均值：{average_price}")


if __name__ == "__main__":
    main()