#!/usr/bin/env python3
"""以名稱與俗名關鍵字，配對食品營養資料庫與 MOA 每 100g 價格資料。"""

import json
import re
import unicodedata
from pathlib import Path

import pandas as pd


# 程式與營養資料庫 Excel 都位於 Price 資料夾。
BASE_DIR = Path(__file__).resolve().parent
NUTRITION_XLSX = BASE_DIR / "食品營養成分資料庫2025版UPDATE1EXCEL(另開新視窗).xlsx"
PRICE_JSON = BASE_DIR / "07_moa_avgprice_per_100g.json"
OUTPUT_JSON = BASE_DIR / "08_match_nutrition_foods__moa_prices.json"


# 這些類別使用整體肉蛋平均價。只採用明確的肉、蛋部位名稱，避免把
# 「雞頭米」、「鴨兒芹」等含有動物字樣的植物或俗名錯配為肉蛋。
# 順序很重要：蛋類須先於同名肉類判斷。
ANIMAL_PRICE_RULES = (
    ("鴨蛋", "鵝鴨蛋", "鴨蛋平均價(元/100g)", ("鴨蛋", "鴨卵", "鴨鹹蛋")),
    ("雞蛋", "雞肉雞蛋", "雞蛋平均價(元/100g)", ("雞蛋", "雞卵", "雞蛋白", "雞蛋黃")),
    ("鵝肉", "鵝鴨蛋", "鵝肉平均價(元/100g)", ("鵝肉", "鵝腿", "鵝胸", "鵝肝", "鵝油", "鵝血", "鵝掌")),
    ("鴨肉", "鵝鴨蛋", "鴨肉平均價(元/100g)", ("鴨肉", "鴨腿", "鴨胸", "鴨翅", "鴨肝", "鴨血", "鴨油", "鴨胗", "鴨舌")),
    ("雞肉", "雞肉雞蛋", "雞肉平均價(元/100g)", ("雞肉", "雞腿", "雞胸", "雞翅", "雞肝", "雞心", "雞胗", "雞血", "雞油", "雞皮", "雞爪", "雞排", "雞骨", "雞湯", "土雞", "肉雞", "烏骨雞")),
    ("羊肉", "羊肉", "羊肉平均價(元/100g)", ("羊肉", "羊排", "羊腿", "羊肝", "羊腎", "羊油", "羊乳", "羊奶", "羔羊", "山羊肉", "綿羊肉")),
    ("豬肉", "豬肉", "豬肉平均價(元/100g)", ("豬肉", "豬排", "豬腿", "豬肝", "豬心", "豬腎", "豬肚", "豬腸", "豬血", "豬油", "豬皮", "豬腳", "豬蹄", "豬骨", "豬舌", "豬耳", "豬肺", "豬腦")),
)

# 肉蛋關鍵字僅在相符的食品分類中生效，進一步避免植物、菇類或調味料誤配。
ALLOWED_FOOD_CLASSES = {
    "鴨蛋": {"蛋類"},
    "雞蛋": {"蛋類"},
    "鵝肉": {"肉類"},
    "鴨肉": {"肉類"},
    "雞肉": {"肉類"},
    "羊肉": {"肉類", "乳品類"},
    "豬肉": {"肉類"},
}


def normalize(text: object) -> str:
    """正規化名稱，忽略全半形、大小寫、空白與常見標點差異。"""
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    value = value.replace("台", "臺")
    return re.sub(r"[\s\-_/（）()，,、；;．.]+", "", value)


def name_terms(sample_name: str, common_name: str) -> list[str]:
    """從樣品名稱與以逗號分隔的俗名，建立可比對的關鍵字清單。"""
    terms = [sample_name]
    terms.extend(re.split(r"[，,、；;／/]", common_name))
    return [normalize(term) for term in terms if normalize(term)]


def animal_match(sample_name: str, food_class: str, prices: dict) -> tuple[str, float] | None:
    """以樣品名稱中的明確肉蛋關鍵字配對，避免俗名造成誤配。"""
    normalized_sample_name = normalize(sample_name)
    for category, section, price_key, keywords in ANIMAL_PRICE_RULES:
        if (
            food_class in ALLOWED_FOOD_CLASSES[category]
            and any(normalize(keyword) in normalized_sample_name for keyword in keywords)
        ):
            return category, prices[section][price_key]
    return None


def product_match(terms: list[str], product_prices: list[dict]) -> tuple[str, float, str] | None:
    """找出最相近的農產品或水產品品名，回傳名稱、價格與配對關鍵字。"""
    best_match = None
    best_score = 0

    for product in product_prices:
        product_name = str(product["品名"])
        normalized_product = normalize(product_name)
        if len(normalized_product) < 2:
            continue

        for term in terms:
            # 「葡萄酒」屬酒類，不是生鮮葡萄，因此不可套用葡萄價格。
            if normalized_product == "葡萄" and "葡萄酒" in term:
                continue

            # 名稱完全相同優先；含有關係則以較短關鍵字長度評分。
            if term == normalized_product:
                score = 10_000 + len(term)
            elif normalized_product in term or term in normalized_product:
                score = min(len(term), len(normalized_product))
            else:
                continue

            if score > best_score:
                best_score = score
                best_match = (
                    product_name,
                    product["平均價(元/100g)"],
                    term,
                )

    return best_match


def main() -> None:
    # Excel 的第 2 列是欄位名稱，因此 header=1；只讀取本次所需欄位。
    foods = pd.read_excel(
        NUTRITION_XLSX,
        sheet_name="台灣食品成分表",
        header=1,
        usecols=["食品分類", "樣品名稱", "俗名"],
    ).fillna("")

    # 讀取先前整合完成、已換算為每 100g 的價格資料。
    with PRICE_JSON.open("r", encoding="utf-8") as price_file:
        prices = json.load(price_file)

    # 農產品與水產品共同以「品名」進行自然語言關鍵字比對。
    product_prices = prices["農產品"] + prices["水產品"]
    output_data = {category: [] for category, *_ in ANIMAL_PRICE_RULES}
    output_data["品名"] = []

    for _, food in foods.iterrows():
        sample_name = str(food["樣品名稱"]).strip()
        common_name = str(food["俗名"]).strip()
        food_class = str(food["食品分類"]).strip()
        if not sample_name:
            continue

        terms = name_terms(sample_name, common_name)

        # 肉蛋價格優先使用對應的整體平均價格。
        matched_animal = animal_match(sample_name, food_class, prices)
        if matched_animal:
            category, price = matched_animal
            output_data[category].append(
                {
                    "樣品名稱": sample_name,
                    "俗名": common_name,
                    "每100g的價格": price,
                }
            )
            continue

        # 其餘食材從農產品與水產品名稱中選擇最相近的品名。
        matched_product = product_match(terms, product_prices)
        if matched_product:
            product_name, price, keyword = matched_product
            output_data["品名"].append(
                {
                    "樣品名稱": sample_name,
                    "俗名": common_name,
                    "品名": product_name,
                    "每100g的價格": price,
                    "配對關鍵字": keyword,
                }
            )

    # 僅輸出有成功配對的資料，讓結果可直接作為食材與價格對照表使用。
    output_data = {category: rows for category, rows in output_data.items() if rows}
    with OUTPUT_JSON.open("w", encoding="utf-8") as output_file:
        json.dump(output_data, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    total = sum(len(rows) for rows in output_data.values())
    print(f"已輸出 {total} 筆配對資料至：{OUTPUT_JSON}")
    for category, rows in output_data.items():
        print(f"{category}：{len(rows)} 筆")


if __name__ == "__main__":
    main()
