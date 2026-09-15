from __future__ import annotations

import json
from pathlib import Path

from app.db import get_connection


REPORT = Path("/workspace/data/processed/weight_estimate_report.json")

QUALITATIVE_UNITS = {
    "少許", "適量", "酌量", "少量", "些許",
}

POWDER_SEASONINGS = {
    "鹽", "胡椒粉", "白胡椒粉", "黑胡椒粉", "雞粉", "味精",
    "五香粉", "咖哩粉", "辣椒粉", "糖", "砂糖",
}

LIQUID_SEASONINGS = {
    "醬油", "米酒", "酒", "醋", "烏醋", "白醋", "香油", "麻油",
    "沙拉油", "橄欖油", "大豆油",
}

FLOURS_STARCHES = {
    "太白粉", "馬鈴薯粉", "地瓜粉", "低筋麵粉", "中筋麵粉",
    "高筋麵粉", "麵粉", "麵包粉",
}

GARNISHES = {
    "香菜", "蔥花", "蔥", "芝麻", "九層塔", "羅勒",
}

WATER_LIKE = {
    "水", "開水", "冷水", "熱水", "冰水",
}

# ------------------------------------------------------------
# V7-T-5：料理常用「個/顆/片」保守 fallback。
# 優先順序仍是 ingredient_unit_weights，
# 只有 DB map 找不到時才走這裡。
# ------------------------------------------------------------
COUNT_WEIGHT_FALLBACK = {
    ("蛋", "個"): (50.0, "MEDIUM", "egg_1_piece_50g"),
    ("蛋", "顆"): (50.0, "MEDIUM", "egg_1_piece_50g"),
    ("雞蛋", "個"): (50.0, "MEDIUM", "egg_1_piece_50g"),
    ("雞蛋", "顆"): (50.0, "MEDIUM", "egg_1_piece_50g"),

    # 目前原始資料中雞絲麵常見明確重量為 100g；
    # 「乾雞絲麵 1片」僅作測試版 DATA_DERIVED fallback。
    ("乾雞絲麵", "片"): (
        100.0,
        "LOW",
        "dataset_family_chicken_noodle_100g",
    ),
}

# 體積 fallback：g/ml
# 優先使用 ingredient_densities；只有無 density 才使用。
VOLUME_DENSITY_FALLBACK = {
    "水": (1.0, "HIGH", "water_density"),
    "開水": (1.0, "HIGH", "water_density"),
    "冷水": (1.0, "HIGH", "water_density"),
    "熱水": (1.0, "HIGH", "water_density"),
    "冰水": (1.0, "HIGH", "water_density"),

    "高湯": (1.0, "MEDIUM", "broth_water_like"),
    "雞高湯": (1.0, "MEDIUM", "broth_water_like"),
    "豬高湯": (1.0, "MEDIUM", "broth_water_like"),
    "清湯": (1.0, "MEDIUM", "broth_water_like"),

    "香油": (0.92, "MEDIUM", "oil_density_approx"),
    "麻油": (0.92, "MEDIUM", "oil_density_approx"),
    "沙拉油": (0.92, "MEDIUM", "oil_density_approx"),
    "橄欖油": (0.92, "MEDIUM", "oil_density_approx"),
    "大豆油": (0.92, "MEDIUM", "oil_density_approx"),

    # 常見小匙固體/碎料，換算為等效 g/ml，僅測試版 fallback。
    "胡蘿蔔": (0.80, "LOW", "minced_carrot_tsp_approx"),
    "油蔥酥": (0.60, "LOW", "fried_shallot_tsp_approx"),
}

VOLUME_TO_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "tbsp": 15.0,
    "tsp": 5.0,
}


def qualitative_grams(
    name: str,
    term: str,
) -> tuple[float | None, str, str]:
    name = (name or "").strip()

    if term == "少許":
        if name in POWDER_SEASONINGS:
            grams = 1.0 if name in {"糖", "砂糖"} else 0.5
            return grams, "MEDIUM", "pinch_powder"
        if name in LIQUID_SEASONINGS:
            return 5.0, "LOW", "small_liquid_5ml_approx"
        if name in FLOURS_STARCHES:
            return 3.0, "LOW", "pinch_flour"
        if name in GARNISHES:
            return 1.0, "LOW", "pinch_garnish"
        return 1.0, "LOW", "pinch_default"

    if term == "少量":
        if name in POWDER_SEASONINGS:
            return 2.0, "LOW", "small_powder"
        if name in LIQUID_SEASONINGS:
            return 5.0, "LOW", "small_liquid_5ml_approx"
        if name in GARNISHES:
            return 2.0, "LOW", "small_garnish"
        return 3.0, "LOW", "small_default"

    if term == "些許":
        if name in LIQUID_SEASONINGS:
            return 5.0, "LOW", "some_liquid_5ml_approx"
        return 1.0, "LOW", "some_default"

    if term == "酌量":
        if name in LIQUID_SEASONINGS:
            return 5.0, "LOW", "to_taste_liquid_5ml_approx"
        if name in POWDER_SEASONINGS:
            return 1.0, "LOW", "to_taste_powder"
        return 3.0, "LOW", "to_taste_default"

    if term == "適量":
        if name in WATER_LIKE:
            return 100.0, "LOW", "water_zero_kcal_placeholder"

        if name == "鹽":
            return 3.0, "MEDIUM", "salt_half_tsp_approx"

        if name in {
            "胡椒粉", "白胡椒粉", "黑胡椒粉",
            "五香粉", "咖哩粉", "辣椒粉",
        }:
            return 0.5, "LOW", "seasoning_quarter_tsp_approx"

        if name in {"香油", "麻油"}:
            return 5.0, "LOW", "aroma_oil_1tsp_approx"

        if name in {"沙拉油", "橄欖油", "大豆油"}:
            return 15.0, "LOW", "cooking_oil_1tbsp_approx"

        if name in {"醬油", "米酒", "酒", "醋", "烏醋", "白醋"}:
            return 15.0, "LOW", "liquid_seasoning_1tbsp_approx"

        if name == "太白粉":
            return 15.0, "LOW", "starch_1tbsp_approx"

        if name in {
            "地瓜粉", "低筋麵粉", "中筋麵粉",
            "高筋麵粉", "麵粉", "麵包粉",
        }:
            return 30.0, "LOW", "coating_flour_approx"

        if name == "香菜":
            return 5.0, "LOW", "garnish_coriander"

        if name in {"蔥", "蔥花"}:
            return 10.0, "LOW", "garnish_scallion"

        if name == "芝麻":
            return 3.0, "LOW", "garnish_sesame"

        return 5.0, "LOW", "appropriate_default"

    return None, "NONE", "no_rule"


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    count_fallback_applied = []
    volume_fallback_applied = []

    with get_connection() as conn, conn.cursor() as cur:
        # A. 優先使用資料導出的 ingredient_unit_weights
        cur.execute(
            """
            UPDATE recipe_ingredients ri
            JOIN ingredient_unit_weights iuw
              ON iuw.ingredient_id = ri.ingredient_id
             AND iuw.unit_id = ri.unit_id
             AND iuw.status = 'ACTIVE'
            SET
                ri.weight_g = ri.quantity_value * iuw.grams_per_unit,
                ri.is_estimated = TRUE,
                ri.review_reason = CONCAT(
                    'UNIT_WEIGHT_MAP:',
                    COALESCE(iuw.source, '')
                )
            WHERE ri.weight_g IS NULL
              AND ri.quantity_value IS NOT NULL
            """
        )
        unit_weight_rows = cur.rowcount

        # B. 優先使用專屬 density
        cur.execute(
            """
            UPDATE recipe_ingredients ri
            JOIN units u
              ON u.id = ri.unit_id
            JOIN ingredient_densities d
              ON d.ingredient_id = ri.ingredient_id
             AND d.status = 'ACTIVE'
            SET
                ri.weight_g =
                    ri.quantity_value
                    *
                    CASE u.canonical_unit
                        WHEN 'ml' THEN 1
                        WHEN 'l' THEN 1000
                        WHEN 'tbsp' THEN 15
                        WHEN 'tsp' THEN 5
                        ELSE NULL
                    END
                    * d.density_g_ml,
                ri.is_estimated = TRUE,
                ri.review_reason = CONCAT(
                    'DENSITY_MAP:',
                    COALESCE(d.source, '')
                )
            WHERE ri.weight_g IS NULL
              AND ri.quantity_value IS NOT NULL
              AND u.canonical_unit IN ('ml', 'l', 'tbsp', 'tsp')
            """
        )
        density_rows = cur.rowcount

        # C. count fallback
        cur.execute(
            """
            SELECT
                ri.id,
                ri.quantity_value,
                i.canonical_name,
                u.canonical_unit
            FROM recipe_ingredients ri
            JOIN ingredients i
              ON i.id = ri.ingredient_id
            JOIN units u
              ON u.id = ri.unit_id
            WHERE ri.weight_g IS NULL
              AND ri.quantity_value IS NOT NULL
            ORDER BY ri.id
            """
        )
        unresolved_rows = cur.fetchall()

        for row in unresolved_rows:
            key = (
                row["canonical_name"],
                row["canonical_unit"],
            )
            rule = COUNT_WEIGHT_FALLBACK.get(key)

            if not rule:
                continue

            grams_per_unit, confidence, source_rule = rule
            grams = float(row["quantity_value"]) * grams_per_unit

            cur.execute(
                """
                UPDATE recipe_ingredients
                SET
                    weight_g=%s,
                    is_estimated=TRUE,
                    needs_manual_review=FALSE,
                    review_reason=%s
                WHERE id=%s
                """,
                (
                    grams,
                    f"COUNT_FALLBACK:{source_rule}:{confidence}",
                    row["id"],
                ),
            )

            count_fallback_applied.append(
                {
                    "id": row["id"],
                    "ingredient": row["canonical_name"],
                    "unit": row["canonical_unit"],
                    "quantity": float(row["quantity_value"]),
                    "weight_g": grams,
                    "confidence": confidence,
                    "rule": source_rule,
                }
            )

        # D. volume fallback
        cur.execute(
            """
            SELECT
                ri.id,
                ri.quantity_value,
                i.canonical_name,
                u.canonical_unit
            FROM recipe_ingredients ri
            JOIN ingredients i
              ON i.id = ri.ingredient_id
            JOIN units u
              ON u.id = ri.unit_id
            WHERE ri.weight_g IS NULL
              AND ri.quantity_value IS NOT NULL
              AND u.canonical_unit IN ('ml','l','tbsp','tsp')
            ORDER BY ri.id
            """
        )
        volume_rows = cur.fetchall()

        for row in volume_rows:
            density_rule = VOLUME_DENSITY_FALLBACK.get(
                row["canonical_name"]
            )

            if not density_rule:
                continue

            density, confidence, source_rule = density_rule
            ml = (
                float(row["quantity_value"])
                * VOLUME_TO_ML[row["canonical_unit"]]
            )
            grams = ml * density

            cur.execute(
                """
                UPDATE recipe_ingredients
                SET
                    weight_g=%s,
                    is_estimated=TRUE,
                    needs_manual_review=FALSE,
                    review_reason=%s
                WHERE id=%s
                """,
                (
                    grams,
                    f"VOLUME_FALLBACK:{source_rule}:{confidence}",
                    row["id"],
                ),
            )

            volume_fallback_applied.append(
                {
                    "id": row["id"],
                    "ingredient": row["canonical_name"],
                    "unit": row["canonical_unit"],
                    "quantity": float(row["quantity_value"]),
                    "weight_g": grams,
                    "confidence": confidence,
                    "rule": source_rule,
                }
            )

        # E. 模糊用量
        cur.execute(
            """
            SELECT
                ri.id,
                i.canonical_name,
                u.canonical_unit
            FROM recipe_ingredients ri
            JOIN ingredients i
              ON i.id = ri.ingredient_id
            JOIN units u
              ON u.id = ri.unit_id
            WHERE ri.weight_g IS NULL
              AND u.canonical_unit IN (
                  '少許','適量','酌量','少量','些許'
              )
            ORDER BY ri.id
            """
        )

        qualitative_rows = cur.fetchall()
        applied = []
        skipped = []

        for row in qualitative_rows:
            grams, confidence, rule = qualitative_grams(
                row["canonical_name"],
                row["canonical_unit"],
            )

            if grams is None:
                skipped.append(dict(row))
                continue

            cur.execute(
                """
                UPDATE recipe_ingredients
                SET
                    weight_g=%s,
                    is_estimated=TRUE,
                    needs_manual_review=FALSE,
                    review_reason=%s
                WHERE id=%s
                """,
                (
                    grams,
                    f"QUALITATIVE:{row['canonical_unit']}:{rule}:{confidence}",
                    row["id"],
                ),
            )

            applied.append(
                {
                    "recipe_ingredient_id": row["id"],
                    "ingredient": row["canonical_name"],
                    "qualitative_unit": row["canonical_unit"],
                    "estimated_weight_g": grams,
                    "confidence": confidence,
                    "rule": rule,
                }
            )

        conn.commit()

    REPORT.write_text(
        json.dumps(
            {
                "unit_weight_map_updated_rows": unit_weight_rows,
                "density_map_updated_rows": density_rows,
                "count_fallback_applied": count_fallback_applied,
                "volume_fallback_applied": volume_fallback_applied,
                "qualitative_candidates": len(qualitative_rows),
                "qualitative_applied": len(applied),
                "qualitative_skipped": len(skipped),
                "applied": applied,
                "skipped": skipped,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"unit-weight updated={unit_weight_rows}")
    print(f"density updated={density_rows}")
    print(f"count fallback={len(count_fallback_applied)}")
    print(f"volume fallback={len(volume_fallback_applied)}")
    print(f"qualitative applied={len(applied)}")
    print(f"report={REPORT}")


if __name__ == "__main__":
    main()
