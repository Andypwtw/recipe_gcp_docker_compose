"""計算肉雞與雞蛋價格平均值，並將各結果各輸出一筆。"""

import json
from pathlib import Path


<<<<<<< HEAD
# 來源資料與輸出檔案都位於此篩選程式所在的 Price 資料夾。
BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "04_moa_poultry_eggs.json"
OUTPUT_PATH = BASE_DIR / "04_moa_poultry_eggs_avgprice.json"
=======
# 來源資料與輸出檔案位置。
INPUT_PATH = Path("/Users/amy/TKR102/Price/04_moa_poultry_eggs.json")
OUTPUT_PATH = Path(__file__).with_name("04_moa_poultry_eggs_avgprice.json")
>>>>>>> 035b0fa063cfe124d9b22f99bdad978556497911

# 兩種肉雞重量區間的價格欄位，會合併後計算單一雞價平均值。
CHICKEN_PRICE_FIELDS = (
    "TaijinPrice_2.0kgup",
    "TaijinPrice_1.75kg_1.95kg",
)
EGG_PRICE_FIELD = "egg_Price"


def numeric_values(records: list[dict], field_names: tuple[str, ...]) -> list[float]:
    """擷取指定欄位的數字價格，略過空白或非數字資料。"""
    values = []
    for record in records:
        for field_name in field_names:
            try:
                values.append(float(record.get(field_name, "")))
            except (TypeError, ValueError):
                continue
    return values


def average(values: list[float], description: str) -> float:
    """計算平均值；若無有效價格則顯示明確錯誤。"""
    if not values:
        raise ValueError(f"找不到可計算的{description}。")
    return round(sum(values) / len(values), 2)


def main() -> None:
    # 讀取原始每日交易資料。
    with INPUT_PATH.open("r", encoding="utf-8") as input_file:
        records = json.load(input_file)

    # 合併兩個肉雞重量區間的有效價格，計算一個 Chicken_TaijinPrice。
    chicken_prices = numeric_values(records, CHICKEN_PRICE_FIELDS)
    egg_prices = numeric_values(records, (EGG_PRICE_FIELD,))

    # 每個平均結果只保留一筆於新的 JSON 檔案。
    output_data = {
        "Chicken_TaijinPrice": average(chicken_prices, "肉雞價格"),
        EGG_PRICE_FIELD: average(egg_prices, "雞蛋價格"),
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(output_data, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"已輸出平均價格至：{OUTPUT_PATH}")


if __name__ == "__main__":
<<<<<<< HEAD
    main()
=======
    main()
>>>>>>> 035b0fa063cfe124d9b22f99bdad978556497911
