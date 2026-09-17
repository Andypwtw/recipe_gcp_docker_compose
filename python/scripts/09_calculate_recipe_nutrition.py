from __future__ import annotations

import json
from pathlib import Path

from app.db import get_connection


ENERGY_SOURCE_COLUMN = "熱量(kcal)"
MIN_COVERAGE_PERCENT = 60.0
MIN_PRICE_COVERAGE_PERCENT = 60.0

DIAGNOSTIC_OUT = Path(
    "/workspace/data/processed/calorie_diagnostic.json"
)


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
        "price_status": """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN price_status VARCHAR(30)
            NOT NULL DEFAULT 'INSUFFICIENT'
            AFTER price_coverage_percent
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
    DIAGNOSTIC_OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with get_connection() as conn, conn.cursor() as cur:
        ensure_summary_columns(cur)

        cur.execute(
            "DELETE FROM recipe_nutrition_summary"
        )

        # ------------------------------------------------------
        # 熱量：沿用既有規則
        # 1) coverage < 60% -> NULL
        # 2) raw kcal is NULL -> NULL
        # 3) 0 < raw kcal < 1 -> NULL
        # 4) 其餘維持 TRUNCATE，不四捨五入
        #
        # 價格：
        # 1) weight_g + price_per_100g 都存在才可計算
        # 2) price coverage < 60% -> NULL / INSUFFICIENT
        # 3) 60% <= coverage < 100% -> PARTIAL
        # 4) coverage = 100% -> CALCULATED
        # ------------------------------------------------------
        cur.execute(
            f"""
            INSERT INTO recipe_nutrition_summary
            (
                recipe_id,
                energy_kcal,
                estimated_price,
                coverage_percent,
                price_coverage_percent,
                calorie_status,
                price_status,
                calculated_at
            )

            SELECT
                calc.recipe_id,

                CASE
                    WHEN calc.raw_energy_kcal IS NULL
                    THEN NULL
                    WHEN calc.coverage_percent < {MIN_COVERAGE_PERCENT}
                    THEN NULL
                    WHEN calc.raw_energy_kcal > 0
                     AND calc.raw_energy_kcal < 1
                    THEN NULL
                    ELSE TRUNCATE(calc.raw_energy_kcal, 0)
                END AS energy_kcal,

                CASE
                    WHEN calc.raw_estimated_price IS NULL
                    THEN NULL
                    WHEN calc.price_coverage_percent < {MIN_PRICE_COVERAGE_PERCENT}
                    THEN NULL
                    ELSE ROUND(calc.raw_estimated_price, 2)
                END AS estimated_price,

                calc.coverage_percent,
                calc.price_coverage_percent,

                CASE
                    WHEN calc.raw_energy_kcal IS NULL
                    THEN 'INSUFFICIENT'
                    WHEN calc.coverage_percent < {MIN_COVERAGE_PERCENT}
                    THEN 'INSUFFICIENT'
                    WHEN calc.raw_energy_kcal > 0
                     AND calc.raw_energy_kcal < 1
                    THEN 'BELOW_1_KCAL'
                    WHEN calc.raw_energy_kcal = 0
                    THEN 'TRUE_ZERO_SOURCE'
                    ELSE 'CALCULATED'
                END AS calorie_status,

                CASE
                    WHEN calc.raw_estimated_price IS NULL
                    THEN 'INSUFFICIENT'
                    WHEN calc.price_coverage_percent < {MIN_PRICE_COVERAGE_PERCENT}
                    THEN 'INSUFFICIENT'
                    WHEN calc.price_coverage_percent < 100
                    THEN 'PARTIAL'
                    ELSE 'CALCULATED'
                END AS price_status,

                NOW()

            FROM (
                SELECT
                    ri.recipe_id,

                    SUM(
                        CASE
                            WHEN ri.weight_g IS NOT NULL
                             AND energy.energy_kcal_per_100g IS NOT NULL
                            THEN
                                energy.energy_kcal_per_100g
                                * ri.weight_g / 100
                            ELSE NULL
                        END
                    ) AS raw_energy_kcal,

                    SUM(
                        CASE
                            WHEN ri.weight_g IS NOT NULL
                             AND ns.price_per_100g IS NOT NULL
                            THEN
                                ns.price_per_100g
                                * ri.weight_g / 100
                            ELSE NULL
                        END
                    ) AS raw_estimated_price,

                    ROUND(
                        100 * SUM(
                            CASE
                                WHEN ri.weight_g IS NOT NULL
                                 AND energy.energy_kcal_per_100g IS NOT NULL
                                THEN 1
                                ELSE 0
                            END
                        ) / COUNT(*),
                        2
                    ) AS coverage_percent,

                    ROUND(
                        100 * SUM(
                            CASE
                                WHEN ri.weight_g IS NOT NULL
                                 AND ns.price_per_100g IS NOT NULL
                                THEN 1
                                ELSE 0
                            END
                        ) / COUNT(*),
                        2
                    ) AS price_coverage_percent

                FROM recipe_ingredients ri

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
                  ON energy.nutrition_source_id
                   = inm.nutrition_source_id

                GROUP BY ri.recipe_id
            ) calc
            """,
            (ENERGY_SOURCE_COLUMN,),
        )

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
                ROUND(AVG(coverage_percent), 2) AS avg_calorie_coverage,
                ROUND(AVG(price_coverage_percent), 2) AS avg_price_coverage
            FROM recipe_nutrition_summary
            """
        )
        summary = cur.fetchone()

        cur.execute(
            """
            SELECT
                r.seq,
                r.name,
                rns.energy_kcal,
                rns.estimated_price,
                rns.coverage_percent,
                rns.price_coverage_percent,
                rns.calorie_status,
                rns.price_status,

                COUNT(*) AS total_lines,

                SUM(CASE WHEN ri.weight_g IS NULL THEN 1 ELSE 0 END)
                    AS missing_weight_lines,

                SUM(CASE WHEN inm.nutrition_source_id IS NULL THEN 1 ELSE 0 END)
                    AS missing_mapping_lines,

                SUM(
                    CASE
                        WHEN inm.nutrition_source_id IS NOT NULL
                         AND energy.energy_kcal_per_100g IS NULL
                        THEN 1 ELSE 0
                    END
                ) AS missing_energy_lines,

                SUM(
                    CASE
                        WHEN inm.nutrition_source_id IS NOT NULL
                         AND ns.price_per_100g IS NULL
                        THEN 1 ELSE 0
                    END
                ) AS missing_price_lines,

                SUM(
                    CASE
                        WHEN ri.weight_g IS NOT NULL
                         AND energy.energy_kcal_per_100g IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS calorie_calculable_lines,

                SUM(
                    CASE
                        WHEN ri.weight_g IS NOT NULL
                         AND ns.price_per_100g IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS price_calculable_lines,

                SUM(
                    CASE
                        WHEN ri.weight_g IS NOT NULL
                         AND energy.energy_kcal_per_100g IS NOT NULL
                        THEN energy.energy_kcal_per_100g * ri.weight_g / 100
                        ELSE NULL
                    END
                ) AS raw_energy_kcal,

                SUM(
                    CASE
                        WHEN ri.weight_g IS NOT NULL
                         AND ns.price_per_100g IS NOT NULL
                        THEN ns.price_per_100g * ri.weight_g / 100
                        ELSE NULL
                    END
                ) AS raw_estimated_price

            FROM recipes r
            JOIN recipe_ingredients ri
              ON ri.recipe_id = r.id
            JOIN recipe_nutrition_summary rns
              ON rns.recipe_id = r.id
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

            WHERE rns.calorie_status <> 'CALCULATED'
               OR rns.price_status <> 'CALCULATED'

            GROUP BY
                r.id,
                r.seq,
                r.name,
                rns.energy_kcal,
                rns.estimated_price,
                rns.coverage_percent,
                rns.price_coverage_percent,
                rns.calorie_status,
                rns.price_status

            ORDER BY rns.calorie_status, rns.price_status, r.seq
            """,
            (ENERGY_SOURCE_COLUMN,),
        )

        diagnostic_rows = cur.fetchall()
        conn.commit()

    def make_json_safe(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    DIAGNOSTIC_OUT.write_text(
        json.dumps(
            {
                "energy_source_column": ENERGY_SOURCE_COLUMN,
                "price_source_column": "nutrition_source.price_per_100g",
                "minimum_calorie_coverage_percent": MIN_COVERAGE_PERCENT,
                "minimum_price_coverage_percent": MIN_PRICE_COVERAGE_PERCENT,
                "summary": {
                    key: make_json_safe(value)
                    for key, value in summary.items()
                },
                "problem_recipes": [
                    {
                        key: make_json_safe(value)
                        for key, value in dict(row).items()
                    }
                    for row in diagnostic_rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("Recipe calorie + price summary calculated.")
    print(f"Minimum calorie coverage: {MIN_COVERAGE_PERCENT}%")
    print(f"Minimum price coverage: {MIN_PRICE_COVERAGE_PERCENT}%")
    print(summary)
    print(f"Diagnostic report -> {DIAGNOSTIC_OUT}")


if __name__ == "__main__":
    main()
