from __future__ import annotations

import json
from pathlib import Path

from app.db import get_connection

ENERGY_SOURCE_COLUMN = "熱量(kcal)"
MIN_COVERAGE_PERCENT = 60.0
MIN_PRICE_COVERAGE_PERCENT = 60.0
MIN_WEIGHT_LINE_COVERAGE_PERCENT = 50.0

DIAGNOSTIC_OUT = Path("/workspace/data/processed/calorie_diagnostic.json")

# These ingredients are effectively zero kcal in recipe-scale use and should not
# make nutrition coverage look missing just because the nutrition workbook has no
# exact generic row for them.
ZERO_KCAL_NAMES = ("水", "開水", "冷水", "熱水", "冰水", "冰塊", "鹽", "鹽巴")
# Tap/recipe water is excluded from food-price coverage because its cost is
# negligible compared with purchased ingredients.
PRICE_EXCLUDED_NAMES = ("水", "開水", "冷水", "熱水", "冰水", "冰塊")


def sql_list(values: tuple[str, ...]) -> str:
    return ",".join("%s" for _ in values)


def ensure_summary_columns(cur):
    wanted = {
        "calorie_status": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN calorie_status VARCHAR(30)
            NOT NULL DEFAULT 'INSUFFICIENT'
            AFTER coverage_percent
        """,
        "estimated_price": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN estimated_price DECIMAL(18,2) NULL
            AFTER energy_kcal
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
        "price_status": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN price_status VARCHAR(30)
            NOT NULL DEFAULT 'INSUFFICIENT'
            AFTER weight_coverage_percent
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


def main():
    DIAGNOSTIC_OUT.parent.mkdir(parents=True, exist_ok=True)
    zero_placeholders = sql_list(ZERO_KCAL_NAMES)
    price_excluded_placeholders = sql_list(PRICE_EXCLUDED_NAMES)

    with get_connection() as conn, conn.cursor() as cur:
        ensure_summary_columns(cur)
        cur.execute("DELETE FROM recipe_nutrition_summary")

        # coverage_percent / price_coverage_percent are now WEIGHT-based among
        # ingredient lines whose weight is known. This prevents tiny missing
        # seasonings from counting the same as a 500g main ingredient.
        # weight_coverage_percent remains a line-based guard so recipes with too
        # many completely unknown quantities are still rejected.
        query = f"""
        INSERT INTO recipe_nutrition_summary
        (
          recipe_id,energy_kcal,estimated_price,
          coverage_percent,price_coverage_percent,weight_coverage_percent,
          calorie_status,price_status,calculated_at
        )
        SELECT
          calc.recipe_id,
          CASE
            WHEN calc.known_weight_g <= 0 THEN NULL
            WHEN calc.weight_coverage_percent < {MIN_WEIGHT_LINE_COVERAGE_PERCENT} THEN NULL
            WHEN calc.coverage_percent < {MIN_COVERAGE_PERCENT} THEN NULL
            WHEN calc.raw_energy_kcal > 0 AND calc.raw_energy_kcal < 1 THEN NULL
            ELSE TRUNCATE(calc.raw_energy_kcal,0)
          END AS energy_kcal,
          CASE
            WHEN calc.price_denominator_g <= 0 THEN NULL
            WHEN calc.raw_estimated_price IS NULL THEN NULL
            WHEN calc.weight_coverage_percent < {MIN_WEIGHT_LINE_COVERAGE_PERCENT} THEN NULL
            WHEN calc.price_coverage_percent < {MIN_PRICE_COVERAGE_PERCENT} THEN NULL
            ELSE ROUND(calc.raw_estimated_price,2)
          END AS estimated_price,
          calc.coverage_percent,
          calc.price_coverage_percent,
          calc.weight_coverage_percent,
          CASE
            WHEN calc.known_weight_g <= 0 THEN 'INSUFFICIENT'
            WHEN calc.weight_coverage_percent < {MIN_WEIGHT_LINE_COVERAGE_PERCENT} THEN 'INSUFFICIENT'
            WHEN calc.coverage_percent < {MIN_COVERAGE_PERCENT} THEN 'INSUFFICIENT'
            WHEN calc.raw_energy_kcal > 0 AND calc.raw_energy_kcal < 1 THEN 'BELOW_1_KCAL'
            WHEN calc.raw_energy_kcal = 0 THEN 'TRUE_ZERO_SOURCE'
            ELSE 'CALCULATED'
          END AS calorie_status,
          CASE
            WHEN calc.price_denominator_g <= 0 THEN 'INSUFFICIENT'
            WHEN calc.raw_estimated_price IS NULL THEN 'INSUFFICIENT'
            WHEN calc.weight_coverage_percent < {MIN_WEIGHT_LINE_COVERAGE_PERCENT} THEN 'INSUFFICIENT'
            WHEN calc.price_coverage_percent < {MIN_PRICE_COVERAGE_PERCENT} THEN 'INSUFFICIENT'
            WHEN calc.price_coverage_percent < 99.5 OR calc.weight_coverage_percent < 99.5 THEN 'PARTIAL'
            ELSE 'CALCULATED'
          END AS price_status,
          NOW()
        FROM (
          SELECT
            base.recipe_id,
            base.raw_energy_kcal,
            base.raw_estimated_price,
            base.known_weight_g,
            base.price_denominator_g,
            ROUND(100 * base.energy_covered_weight_g / NULLIF(base.known_weight_g,0),2)
              AS coverage_percent,
            ROUND(100 * base.price_covered_weight_g / NULLIF(base.price_denominator_g,0),2)
              AS price_coverage_percent,
            ROUND(100 * base.known_weight_lines / NULLIF(base.total_lines,0),2)
              AS weight_coverage_percent
          FROM (
            SELECT
              ri.recipe_id,
              COUNT(*) AS total_lines,
              SUM(CASE WHEN ri.weight_g IS NOT NULL THEN 1 ELSE 0 END) AS known_weight_lines,
              SUM(CASE WHEN ri.weight_g IS NOT NULL THEN ri.weight_g ELSE 0 END) AS known_weight_g,

              SUM(
                CASE
                  WHEN ri.weight_g IS NOT NULL AND energy.energy_kcal_per_100g IS NOT NULL
                    THEN energy.energy_kcal_per_100g * ri.weight_g / 100
                  WHEN ri.weight_g IS NOT NULL AND i.canonical_name IN ({zero_placeholders})
                    THEN 0
                  ELSE NULL
                END
              ) AS raw_energy_kcal,

              SUM(
                CASE
                  WHEN ri.weight_g IS NOT NULL
                   AND i.canonical_name NOT IN ({price_excluded_placeholders})
                   AND ns.price_per_100g IS NOT NULL
                    THEN ns.price_per_100g * ri.weight_g / 100
                  ELSE NULL
                END
              ) AS raw_estimated_price,

              SUM(
                CASE
                  WHEN ri.weight_g IS NOT NULL
                   AND (energy.energy_kcal_per_100g IS NOT NULL OR i.canonical_name IN ({zero_placeholders}))
                    THEN ri.weight_g
                  ELSE 0
                END
              ) AS energy_covered_weight_g,

              SUM(
                CASE
                  WHEN ri.weight_g IS NOT NULL
                   AND i.canonical_name NOT IN ({price_excluded_placeholders})
                    THEN ri.weight_g
                  ELSE 0
                END
              ) AS price_denominator_g,

              SUM(
                CASE
                  WHEN ri.weight_g IS NOT NULL
                   AND i.canonical_name NOT IN ({price_excluded_placeholders})
                   AND ns.price_per_100g IS NOT NULL
                    THEN ri.weight_g
                  ELSE 0
                END
              ) AS price_covered_weight_g

            FROM recipe_ingredients ri
            JOIN ingredients i ON i.id = ri.ingredient_id
            LEFT JOIN ingredient_nutrition_map inm
              ON inm.ingredient_id = ri.ingredient_id AND inm.status='APPROVED'
            LEFT JOIN nutrition_source ns ON ns.id = inm.nutrition_source_id
            LEFT JOIN (
              SELECT nv.nutrition_source_id, nv.value_numeric AS energy_kcal_per_100g
              FROM nutrition_values nv
              JOIN nutrient_definitions nd ON nd.id = nv.nutrient_id
              WHERE nd.source_column_name = %s
            ) energy ON energy.nutrition_source_id = inm.nutrition_source_id
            GROUP BY ri.recipe_id
          ) base
        ) calc
        """

        params = (
            *ZERO_KCAL_NAMES,
            *PRICE_EXCLUDED_NAMES,
            *ZERO_KCAL_NAMES,
            *PRICE_EXCLUDED_NAMES,
            *PRICE_EXCLUDED_NAMES,
            ENERGY_SOURCE_COLUMN,
        )
        cur.execute(query, params)

        cur.execute(
            """
            SELECT
              COUNT(*) AS recipe_count,
              SUM(calorie_status='CALCULATED') AS calorie_calculated_count,
              SUM(calorie_status='INSUFFICIENT') AS calorie_insufficient_count,
              SUM(calorie_status='BELOW_1_KCAL') AS below_1_kcal_count,
              SUM(calorie_status='TRUE_ZERO_SOURCE') AS true_zero_count,
              SUM(price_status='CALCULATED') AS price_calculated_count,
              SUM(price_status='PARTIAL') AS price_partial_count,
              SUM(price_status='INSUFFICIENT') AS price_insufficient_count,
              ROUND(AVG(coverage_percent),2) AS avg_calorie_weight_coverage,
              ROUND(AVG(price_coverage_percent),2) AS avg_price_weight_coverage,
              ROUND(AVG(weight_coverage_percent),2) AS avg_weight_line_coverage
            FROM recipe_nutrition_summary
            """
        )
        summary = cur.fetchone()

        cur.execute(
            """
            SELECT
              r.seq,r.name,rns.energy_kcal,rns.estimated_price,
              rns.coverage_percent,rns.price_coverage_percent,rns.weight_coverage_percent,
              rns.calorie_status,rns.price_status,
              COUNT(*) AS total_lines,
              SUM(ri.weight_g IS NULL) AS missing_weight_lines,
              SUM(inm.nutrition_source_id IS NULL) AS missing_mapping_lines,
              SUM(inm.nutrition_source_id IS NOT NULL AND energy.energy_kcal_per_100g IS NULL)
                AS missing_energy_lines,
              SUM(inm.nutrition_source_id IS NOT NULL AND ns.price_per_100g IS NULL)
                AS missing_price_lines
            FROM recipes r
            JOIN recipe_ingredients ri ON ri.recipe_id=r.id
            JOIN recipe_nutrition_summary rns ON rns.recipe_id=r.id
            JOIN ingredients i ON i.id=ri.ingredient_id
            LEFT JOIN ingredient_nutrition_map inm
              ON inm.ingredient_id=ri.ingredient_id AND inm.status='APPROVED'
            LEFT JOIN nutrition_source ns ON ns.id=inm.nutrition_source_id
            LEFT JOIN (
              SELECT nv.nutrition_source_id,nv.value_numeric AS energy_kcal_per_100g
              FROM nutrition_values nv
              JOIN nutrient_definitions nd ON nd.id=nv.nutrient_id
              WHERE nd.source_column_name=%s
            ) energy ON energy.nutrition_source_id=inm.nutrition_source_id
            WHERE rns.calorie_status<>'CALCULATED' OR rns.price_status='INSUFFICIENT'
            GROUP BY r.id,r.seq,r.name,rns.energy_kcal,rns.estimated_price,
                     rns.coverage_percent,rns.price_coverage_percent,rns.weight_coverage_percent,
                     rns.calorie_status,rns.price_status
            ORDER BY rns.calorie_status,rns.price_status,r.seq
            """,
            (ENERGY_SOURCE_COLUMN,),
        )
        diagnostic_rows = cur.fetchall()
        conn.commit()

    def safe(v):
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        return str(v)

    DIAGNOSTIC_OUT.write_text(
        json.dumps(
            {
                "coverage_method": "WEIGHT_BASED_WITH_LINE_GUARD",
                "energy_source_column": ENERGY_SOURCE_COLUMN,
                "price_source_column": "nutrition_source.price_per_100g",
                "minimum_calorie_weight_coverage_percent": MIN_COVERAGE_PERCENT,
                "minimum_price_weight_coverage_percent": MIN_PRICE_COVERAGE_PERCENT,
                "minimum_known_weight_line_coverage_percent": MIN_WEIGHT_LINE_COVERAGE_PERCENT,
                "summary": {k: safe(v) for k, v in summary.items()},
                "problem_recipes": [
                    {k: safe(v) for k, v in dict(row).items()} for row in diagnostic_rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("Recipe calorie + price summary calculated with weighted coverage.")
    print(summary)
    print(f"Diagnostic report -> {DIAGNOSTIC_OUT}")


if __name__ == "__main__":
    main()
