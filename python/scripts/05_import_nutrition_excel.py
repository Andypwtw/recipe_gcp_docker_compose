from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from app.db import get_connection


XLSX = Path("/workspace/data/reference/food_nutrition_2025.xlsx")
PRICE_SOURCE_COLUMN = "每100g的價格"

META_COLUMNS = {
    "整合編號",
    "食品分類",
    "樣品名稱",
    "內容物描述",
    "俗名",
    "廢棄率(%)",
    PRICE_SOURCE_COLUMN,
}


def clean_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def parse_nutrient_header(header: str) -> tuple[str, str | None]:
    """
    例如：
      熱量(kcal) -> ("熱量", "kcal")
      粗蛋白(g) -> ("粗蛋白", "g")
      P/M/S -> ("P/M/S", None)

    有些欄位名稱本身含括號，例如：
      視網醇當量(RE)(ug)
    此時只取最後一組括號作為 unit。
    """
    header = str(header).strip()
    match = re.search(r"\(([^()]*)\)\s*$", header)

    if not match:
        return header, None

    unit = match.group(1).strip() or None
    nutrient_name = header[:match.start()].strip()

    return nutrient_name, unit


def nutrient_group(header: str) -> str:
    if "熱量" in header:
        return "熱量"
    if any(x in header for x in ["蛋白", "胺基酸", "Asp", "Thr", "Ser", "Glu",
                                  "Pro", "Gly", "Ala", "Cys", "Val", "Met",
                                  "Ile", "Leu", "Tyr", "Phe", "Lys", "His",
                                  "Arg", "Trp"]):
        return "蛋白質與胺基酸"
    if any(x in header for x in ["脂肪", "脂肪酸", "膽固醇", "生育酚"]):
        return "脂質"
    if any(x in header for x in ["碳水", "糖", "纖維"]):
        return "碳水化合物"
    if any(x in header for x in ["鈉", "鉀", "鈣", "鎂", "鐵", "鋅", "磷", "銅", "錳"]):
        return "礦物質"
    if "維生素" in header or "胡蘿蔔素" in header or "視網醇" in header or "葉酸" in header:
        return "維生素"
    if "水分" in header:
        return "水分"
    if "酒精" in header:
        return "酒精"
    return "其他"


def to_numeric_or_text(value):
    value = clean_value(value)

    if value is None or value == "":
        return None, None

    if isinstance(value, bool):
        return float(value), str(value)

    if isinstance(value, (int, float)):
        return float(value), str(value)

    text = str(value).strip()

    if not text:
        return None, None

    try:
        return float(text), text
    except Exception:
        return None, text



def parse_price_per_100g(value):
    """將 Excel 的每100g價格轉成 Decimal 可接受的數值。

    空白或無效值回傳 None。0 允許保留；負值視為無效。
    """
    value = clean_value(value)
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return None

    try:
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            text = str(value).strip()
            if not text:
                return None
            for token in (",", "NT$", "NT＄", "$", "元"):
                text = text.replace(token, "")
            number = float(text.strip())
    except (TypeError, ValueError):
        return None

    if number < 0:
        return None
    return number


def ensure_price_column(cur):
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'nutrition_source'
          AND column_name = 'price_per_100g'
        """
    )
    if cur.fetchone()["n"] == 0:
        cur.execute(
            """
            ALTER TABLE nutrition_source
            ADD COLUMN price_per_100g DECIMAL(18,4) NULL
            AFTER waste_percent
            """
        )


def main():
    if not XLSX.exists():
        raise FileNotFoundError(
            f"Nutrition Excel not found: {XLSX}"
        )

    # Excel 第 1 列是說明文字，第 2 列才是真正欄名。
    df = pd.read_excel(
        XLSX,
        sheet_name="台灣食品成分表",
        header=1,
    )

    # 移除 Excel 最右側完全空白欄位。
    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]

    nutrient_columns = [
        col
        for col in df.columns
        if col not in META_COLUMNS
    ]

    with get_connection() as conn, conn.cursor() as cur:
        ensure_price_column(cur)

        # Nutrition source 重建時，先清除所有依賴它的 Mapping。
        # 06~08 會在同一次 Pipeline 中重新建立。
        cur.execute("DELETE FROM ingredient_nutrition_map")
        cur.execute("DELETE FROM manual_review")
        cur.execute("DELETE FROM nutrition_values")
        cur.execute("DELETE FROM nutrient_definitions")
        cur.execute("DELETE FROM nutrition_source")

        # ----------------------------------------------------
        # 建立所有營養欄位定義
        # ----------------------------------------------------
        nutrient_id_by_column = {}

        for order_no, source_column in enumerate(
            nutrient_columns,
            start=1,
        ):
            name, unit = parse_nutrient_header(source_column)

            cur.execute(
                """
                INSERT INTO nutrient_definitions
                (
                    nutrient_name,
                    unit,
                    source_column_name,
                    nutrient_group,
                    display_order
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    name,
                    unit,
                    source_column,
                    nutrient_group(source_column),
                    order_no,
                ),
            )

            nutrient_id_by_column[source_column] = (
                cur.lastrowid
            )

        # ----------------------------------------------------
        # 食品主檔 + 全部營養值
        # ----------------------------------------------------
        food_count = 0
        nutrient_value_count = 0

        for _, row in df.iterrows():
            data = {
                col: clean_value(row[col])
                for col in df.columns
            }

            food_code = data.get("整合編號")
            food_name = data.get("樣品名稱")

            if not food_code or not food_name:
                continue

            cur.execute(
                """
                INSERT INTO nutrition_source
                (
                    food_code,
                    food_category,
                    food_name,
                    content_description,
                    common_names,
                    waste_percent,
                    price_per_100g,
                    raw_data
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(food_code),
                    data.get("食品分類"),
                    str(food_name),
                    data.get("內容物描述"),
                    data.get("俗名"),
                    data.get("廢棄率(%)"),
                    parse_price_per_100g(data.get(PRICE_SOURCE_COLUMN)),
                    json.dumps(
                        data,
                        ensure_ascii=False,
                        default=str,
                    ),
                ),
            )

            nutrition_source_id = cur.lastrowid
            food_count += 1

            value_rows = []

            for source_column in nutrient_columns:
                value_numeric, value_text = (
                    to_numeric_or_text(
                        data.get(source_column)
                    )
                )

                # 空白值不建立明細，可大幅減少無效資料列。
                if value_numeric is None and value_text is None:
                    continue

                value_rows.append(
                    (
                        nutrition_source_id,
                        nutrient_id_by_column[source_column],
                        value_numeric,
                        value_text,
                    )
                )

            if value_rows:
                cur.executemany(
                    """
                    INSERT INTO nutrition_values
                    (
                        nutrition_source_id,
                        nutrient_id,
                        value_numeric,
                        value_text
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    value_rows,
                )

                nutrient_value_count += len(value_rows)

            if food_count % 200 == 0:
                conn.commit()
                print(
                    f"Nutrition import progress: "
                    f"{food_count}/{len(df)} foods"
                )

        conn.commit()

    print("Full nutrition Excel import finished.")
    print(f"foods imported = {food_count}")
    print(
        f"nutrient definitions = "
        f"{len(nutrient_columns)}"
    )
    print(
        f"nutrition values = "
        f"{nutrient_value_count}"
    )
    print(
        "Current application calculation mode: "
        "ENERGY_KCAL_AND_PRICE"
    )


if __name__ == "__main__":
    main()
