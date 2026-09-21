from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.db import get_connection
from app.services.recipe_calculation import calculate_recipe

ENERGY_SOURCE_COLUMN = "熱量(kcal)"
DIAGNOSTIC_OUT = Path("/workspace/data/processed/calorie_diagnostic.json")


def ensure_summary_columns(cur):
    wanted = {
        "ingredient_energy_kcal": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN ingredient_energy_kcal DECIMAL(18,6) NULL
            AFTER energy_kcal
        """,
        "seasoning_energy_kcal": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN seasoning_energy_kcal DECIMAL(18,6) NULL
            AFTER ingredient_energy_kcal
        """,
        "estimated_price": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN estimated_price DECIMAL(18,2) NULL
            AFTER seasoning_energy_kcal
        """,
        "coverage_percent": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN coverage_percent DECIMAL(8,2) NULL
            AFTER estimated_price
        """,
        "price_coverage_percent": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN price_coverage_percent DECIMAL(8,2) NULL
            AFTER coverage_percent
        """,
        "weight_coverage_percent": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN weight_coverage_percent DECIMAL(8,2) NULL
            AFTER price_coverage_percent
        """,
        "price_weight_line_coverage_percent": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN price_weight_line_coverage_percent DECIMAL(8,2) NULL
            AFTER weight_coverage_percent
        """,
        "main_missing_ratio_percent": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN main_missing_ratio_percent DECIMAL(8,2) NULL
            AFTER price_weight_line_coverage_percent
        """,
        "calorie_status": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN calorie_status VARCHAR(30)
            NOT NULL DEFAULT 'INSUFFICIENT'
            AFTER main_missing_ratio_percent
        """,
        "price_status": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN price_status VARCHAR(30)
            NOT NULL DEFAULT 'INSUFFICIENT'
            AFTER calorie_status
        """,
    }
    for column_name, ddl in wanted.items():
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'recipe_nutrition_summary'
              AND column_name = %s
            """,
            (column_name,),
        )
        if cur.fetchone()["n"] == 0:
            cur.execute(ddl)


def load_recipe_lines(cur) -> dict[int, list[dict]]:
    cur.execute(
        """
        SELECT
          ri.recipe_id,
          ri.line_no,
          ri.raw_text,
          ri.ingredient_id,
          i.canonical_name,
          ri.weight_g,
          energy.energy_kcal_per_100g,
          ns.price_per_100g,
          COALESCE(icr.rule_key, 'DEFAULT_FOOD') AS rule_key,
          COALESCE(icr.ingredient_category, 'FOOD') AS ingredient_category,
          COALESCE(icr.is_seasoning, FALSE) AS is_seasoning,
          icr.fallback_kcal_per_100g
        FROM recipe_ingredients ri
        JOIN ingredients i
          ON i.id = ri.ingredient_id
        LEFT JOIN ingredient_calculation_rules icr
          ON icr.ingredient_id = ri.ingredient_id
         AND icr.status = 'ACTIVE'
        LEFT JOIN ingredient_nutrition_map inm
          ON inm.ingredient_id = ri.ingredient_id
         AND inm.status = 'APPROVED'
        LEFT JOIN nutrition_source ns
          ON ns.id = inm.nutrition_source_id
        LEFT JOIN (
          SELECT
            nv.nutrition_source_id,
            nv.value_numeric AS energy_kcal_per_100g
          FROM nutrition_values nv
          JOIN nutrient_definitions nd
            ON nd.id = nv.nutrient_id
          WHERE nd.source_column_name = %s
        ) energy
          ON energy.nutrition_source_id = inm.nutrition_source_id
        ORDER BY ri.recipe_id, ri.line_no
        """,
        (ENERGY_SOURCE_COLUMN,),
    )
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in cur.fetchall():
        grouped[int(row["recipe_id"])].append(dict(row))
    return grouped


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 2)


def main():
    DIAGNOSTIC_OUT.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn, conn.cursor() as cur:
        ensure_summary_columns(cur)
        recipe_lines = load_recipe_lines(cur)

        cur.execute("SELECT id,seq,name FROM recipes ORDER BY id")
        recipes = [dict(row) for row in cur.fetchall()]
        cur.execute("DELETE FROM recipe_nutrition_summary")

        insert_rows = []
        diagnostics = []
        counters = {
            "recipe_count": 0,
            "calorie_calculated_count": 0,
            "calorie_insufficient_count": 0,
            "price_calculated_count": 0,
            "price_partial_count": 0,
            "price_insufficient_count": 0,
        }

        for recipe in recipes:
            lines = recipe_lines.get(int(recipe["id"]), [])
            result = calculate_recipe(lines)
            main_coverage = (
                None
                if result.main_missing_ratio_percent is None
                else round(100.0 - result.main_missing_ratio_percent, 2)
            )
            price_coverage = _pct(result.priced_lines, result.total_lines)

            insert_rows.append(
                (
                    recipe["id"],
                    result.energy_kcal,
                    result.ingredient_energy_kcal,
                    result.seasoning_energy_kcal,
                    result.estimated_price,
                    main_coverage,
                    price_coverage,
                    main_coverage,
                    price_coverage,
                    result.main_missing_ratio_percent,
                    result.calorie_status,
                    result.price_status,
                )
            )

            counters["recipe_count"] += 1
            if result.calorie_status == "CALCULATED":
                counters["calorie_calculated_count"] += 1
            else:
                counters["calorie_insufficient_count"] += 1
            if result.price_status == "CALCULATED":
                counters["price_calculated_count"] += 1
            elif result.price_status == "PARTIAL":
                counters["price_partial_count"] += 1
            else:
                counters["price_insufficient_count"] += 1

            if result.calorie_status != "CALCULATED" or result.price_status != "CALCULATED":
                diagnostics.append(
                    {
                        "seq": recipe["seq"],
                        "name": recipe["name"],
                        "energy_kcal": result.energy_kcal,
                        "ingredient_energy_kcal": result.ingredient_energy_kcal,
                        "seasoning_energy_kcal": result.seasoning_energy_kcal,
                        "estimated_price": result.estimated_price,
                        "main_missing_ratio_percent": result.main_missing_ratio_percent,
                        "main_total_lines": result.main_total_lines,
                        "main_missing_lines": result.main_missing_lines,
                        "priced_lines": result.priced_lines,
                        "total_lines": result.total_lines,
                        "calorie_status": result.calorie_status,
                        "price_status": result.price_status,
                    }
                )

        cur.executemany(
            """
            INSERT INTO recipe_nutrition_summary
            (
              recipe_id,energy_kcal,ingredient_energy_kcal,seasoning_energy_kcal,
              estimated_price,coverage_percent,price_coverage_percent,
              weight_coverage_percent,price_weight_line_coverage_percent,
              main_missing_ratio_percent,calorie_status,price_status,calculated_at
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            """,
            insert_rows,
        )
        conn.commit()

    DIAGNOSTIC_OUT.write_text(
        json.dumps(
            {
                "policy_version": "2026-09-21-v2",
                "energy_source_column": ENERGY_SOURCE_COLUMN,
                "main_ingredient_policy": {
                    "missing_ratio_limit_percent": 20,
                    "known_kcal_override_strictly_greater_than": 500,
                    "aromatic_main_threshold_g": 80,
                    "spices_excluded_from_main_missing_ratio": True,
                },
                "seasoning_policy": {
                    "missing_weight": "EXCLUDE_FROM_CALORIE",
                    "ordinary_min_weight_g": 50,
                    "high_energy_min_weight_g": 20,
                },
                "price_policy": {
                    "per_item": True,
                    "missing_display": "$無資料",
                    "rounding": "CEILING_TO_INTEGER_PER_ITEM",
                    "total": "SUM_OF_ROUNDED_KNOWN_ITEM_PRICES",
                    "all_missing_total": "$無資料",
                },
                "summary": counters,
                "problem_recipes": diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("Recipe calorie + per-ingredient price summary calculated with 2026-09-21 policy.")
    print(counters)
    print(f"Diagnostic report -> {DIAGNOSTIC_OUT}")


if __name__ == "__main__":
    main()
