#!/usr/bin/env python3
"""整合六個 MOA 平均價格 JSON，轉換為每 100g 的價格資料。"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


# 本程式與六個來源檔案放在同一個資料夾，因此使用程式自身位置作為資料夾。
BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "07_moa_avgprice_per_100g.json"


def load_json(file_name: str):
    """讀取同資料夾中的 JSON 檔案。"""
    with (BASE_DIR / file_name).open("r", encoding="utf-8") as input_file:
        return json.load(input_file)


def convert_price(value, multiplier: str) -> float:
    """將價格乘上指定倍率，保留最多三位小數以避免浮點數誤差。"""
    try:
        converted = Decimal(str(value)) * Decimal(multiplier)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"無法轉換價格：{value!r}") from error
    return float(round(converted, 3))


def main() -> None:
    # 豬肉：使用已計算完成的整體平均價格。
    pork = load_json("03_moa_pork_avgprice.json")

    # 農產品與水產品：每個品名各保留其對應價格。
    agriculture = load_json("01_moa_agri_avgprice.json")
    fishery = load_json("02_moa_fisher_avgprice.json")

    # 鵝、鴨、鴨蛋，雞肉、雞蛋與羊肉：各檔案皆已是一筆平均價格資料。
    goose_duck_egg = load_json("05_moa_goose_duck_duckegg_avgprice.json")
    poultry_eggs = load_json("04_moa_poultry_eggs_avgprice.json")
    sheep = load_json("06_moa_sheep_avgprice.json")

    # 組合所有轉換後資料；農產品與水產品以清單保留品名與價格的對應關係。
    output_data = {
        "豬肉": {
            "豬肉平均價(元/100g)": convert_price(
                pork["TransNum_AvgPrice_Average"], "0.1"
            )
        },
        "農產品": [
            {
                "品名": record["CropName"],
                "平均價(元/100g)": convert_price(record["Avg_Price"], "0.1"),
            }
            for record in agriculture
        ],
        "水產品": [
            {
                "品名": record["SeafoodProdName"],
                "平均價(元/100g)": convert_price(record["Avg_Price"], "0.1"),
            }
            for record in fishery
        ],
        "鵝鴨蛋": {
            "鵝肉平均價(元/100g)": convert_price(
                goose_duck_egg["Goose_WR_TaijinPrice"], "0.166"
            ),
            "鴨肉平均價(元/100g)": convert_price(
                goose_duck_egg["Duck_75D_TaijinPrice"], "0.166"
            ),
            "鴨蛋平均價(元/100g)": convert_price(
                goose_duck_egg["Duckegg_TNN_TaijinPrice"], "0.166"
            ),
        },
        "雞肉雞蛋": {
            "雞肉平均價(元/100g)": convert_price(
                poultry_eggs["Chicken_TaijinPrice"], "0.166"
            ),
            "雞蛋平均價(元/100g)": convert_price(
                poultry_eggs["egg_Price"], "0.166"
            ),
        },
        "羊肉": {
            "羊肉平均價(元/100g)": convert_price(sheep["avgPrice"], "0.1")
        },
    }

    # 寫出整合完成的 JSON 檔案。
    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(output_data, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"已輸出整合資料至：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
