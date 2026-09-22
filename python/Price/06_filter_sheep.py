"""計算羊價 avgPrice 的平均值，並只輸出一筆結果。"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


# 要處理的羊價 JSON 檔案與平均值輸出檔案。
INPUT_PATH = Path(__file__).with_name("06_moa_sheep.json")
OUTPUT_PATH = Path(__file__).with_name("06_moa_sheep_avgprice.json")
PRICE_FIELD = "avgPrice"


def numeric_price(value: object) -> Decimal | None:
    """將價格轉為數字；空白或非數字資料則略過。"""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def main() -> None:
    # 讀取先前由 API 產出的交易資料。
    with INPUT_PATH.open("r", encoding="utf-8") as input_file:
        records = json.load(input_file)

    # 擷取所有有效 avgPrice，並計算單一平均價格。
    prices = []
    for record in records:
        price = numeric_price(record.get(PRICE_FIELD, ""))
        if price is not None:
            prices.append(price)

    if not prices:
        raise ValueError(f"找不到可計算的 {PRICE_FIELD} 資料。")

    average_price = round(float(sum(prices) / len(prices)), 2)
    output_data = {PRICE_FIELD: average_price}

    # 只寫出一筆 avgPrice 平均值。
    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(output_data, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"已計算 {len(prices)} 筆 {PRICE_FIELD} 的平均值：{average_price}")
    print(f"已輸出至：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()