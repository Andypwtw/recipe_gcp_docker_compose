from __future__ import annotations

import json
from pathlib import Path

from app.db import get_connection


ENERGY_SOURCE_COLUMN = "熱量(kcal)"
MIN_COVERAGE_PERCENT = 60.0

DIAGNOSTIC_OUT = Path(
    "/workspace/data/processed/calorie_diagnostic.json"
)


def ensure_calorie_status_column(cur):
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'recipe_nutrition_summary'
          AND column_name = 'calorie_status'
        """
    )

    if cur.fetchone()["n"] == 0:
        cur.execute(
            """
            ALTER TABLE recipe_nutrition_summary
            ADD COLUMN calorie_status VARCHAR(30)
            NOT NULL DEFAULT 'INSUFFICIENT'
            AFTER coverage_percent
            """
        )


def main():
    DIAGNOSTIC_OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with get_connection() as conn, conn.cursor() as cur:
        ensure_calorie_status_column(cur)

        cur.execute(
            "DELETE FROM recipe_nutrition_summary"
        )

        # ------------------------------------------------------
        # V7-T-5 calorie quality rule
        #
        # 1) coverage < 60% -> energy_kcal = NULL
        # 2) raw kcal is NULL -> NULL
        # 3) 0 < raw kcal < 1 -> NULL
        #    避免 TRUNCATE 後產生誤導性的 0 kcal
        # 4) 其餘維持使用者指定的 TRUNCATE，不四捨五入
        # ------------------------------------------------------
        cur.execute(
            f"""
            INSERT INTO recipe_nutrition_summary
            (
                recipe_id,
                energy_kcal,
                coverage_percent,
                calorie_status,
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

                calc.coverage_percent,

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
                    ) AS coverage_percent

                FROM recipe_ingredients ri

                LEFT JOIN ingredient_nutrition_map inm
                  ON inm.ingredient_id = ri.ingredient_id
                 AND inm.status = 'APPROVED'

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
                SUM(calorie_status='CALCULATED')
                    AS calculated_count,
                SUM(calorie_status='INSUFFICIENT')
                    AS insufficient_count,
                SUM(calorie_status='BELOW_1_KCAL')
                    AS below_1_kcal_count,
                SUM(calorie_status='TRUE_ZERO_SOURCE')
                    AS true_zero_count,
                ROUND(AVG(coverage_percent), 2)
                    AS avg_coverage
            FROM recipe_nutrition_summary
            """
        )
        summary = cur.fetchone()

        # ------------------------------------------------------
        # 全部非 CALCULATED 都列入診斷，
        # 再加上真正 0 kcal 來源，便於確認。
        # ------------------------------------------------------
        cur.execute(
            """
            SELECT
                r.seq,
                r.name,
                rns.energy_kcal,
                rns.coverage_percent,
                rns.calorie_status,

                COUNT(*) AS total_lines,

                SUM(
                    CASE
                        WHEN ri.weight_g IS NULL
                        THEN 1 ELSE 0
                    END
                ) AS missing_weight_lines,

                SUM(
                    CASE
                        WHEN inm.nutrition_source_id IS NULL
                        THEN 1 ELSE 0
                    END
                ) AS missing_mapping_lines,

                SUM(
                    CASE
                        WHEN inm.nutrition_source_id IS NOT NULL
                         AND energy.energy_kcal_per_100g IS NULL
                        THEN 1 ELSE 0
                    END
                ) AS missing_energy_lines,

                SUM(
                    CASE
                        WHEN ri.weight_g IS NOT NULL
                         AND energy.energy_kcal_per_100g IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS calculable_lines,

                SUM(
                    CASE
                        WHEN ri.weight_g IS NOT NULL
                         AND energy.energy_kcal_per_100g IS NOT NULL
                        THEN
                            energy.energy_kcal_per_100g
                            * ri.weight_g / 100
                        ELSE NULL
                    END
                ) AS raw_energy_kcal

            FROM recipes r

            JOIN recipe_ingredients ri
              ON ri.recipe_id = r.id

            JOIN recipe_nutrition_summary rns
              ON rns.recipe_id = r.id

            LEFT JOIN ingredient_nutrition_map inm
              ON inm.ingredient_id = ri.ingredient_id
             AND inm.status = 'APPROVED'

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

            WHERE rns.calorie_status <> 'CALCULATED'

            GROUP BY
                r.id,
                r.seq,
                r.name,
                rns.energy_kcal,
                rns.coverage_percent,
                rns.calorie_status

            ORDER BY
                rns.calorie_status,
                r.seq
            """,
            (ENERGY_SOURCE_COLUMN,),
        )

        diagnostic_rows = cur.fetchall()
        conn.commit()

    def make_json_safe(value):
        if value is None or isinstance(
            value,
            (str, int, float, bool),
        ):
            return value
        return str(value)

    DIAGNOSTIC_OUT.write_text(
        json.dumps(
            {
                "energy_source_column": ENERGY_SOURCE_COLUMN,
                "minimum_coverage_percent": MIN_COVERAGE_PERCENT,
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

    print("Recipe calorie summary calculated.")
    print(
        f"Minimum coverage: "
        f"{MIN_COVERAGE_PERCENT}%"
    )
    print(summary)
    print(
        f"Calorie diagnostic report -> "
        f"{DIAGNOSTIC_OUT}"
    )


if __name__ == "__main__":
    main()
