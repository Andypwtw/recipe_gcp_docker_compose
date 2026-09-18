from __future__ import annotations

import json
from pathlib import Path

from app.db import get_connection

REPORT = Path("/workspace/data/processed/weight_estimate_report.json")

COUNT_WEIGHT_FALLBACK = {
    ("蛋", "個"): (50.0, "MEDIUM", "egg_1_piece_50g"),
    ("蛋", "顆"): (50.0, "MEDIUM", "egg_1_piece_50g"),
    ("雞蛋", "個"): (50.0, "MEDIUM", "egg_1_piece_50g"),
    ("雞蛋", "顆"): (50.0, "MEDIUM", "egg_1_piece_50g"),
    ("乾雞絲麵", "片"): (100.0, "LOW", "dataset_family_chicken_noodle_100g"),
}

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
}

VOLUME_TO_ML = {"ml": 1.0, "l": 1000.0, "tbsp": 15.0, "tsp": 5.0}


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    details = {
        "unit_weight_map": 0,
        "density_map": 0,
        "qualitative_high_confidence": 0,
        "count_fallback": 0,
        "volume_fallback": 0,
        "unresolved_qualitative": 0,
    }

    with get_connection() as conn, conn.cursor() as cur:
        # A. Ingredient + count unit map.
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
              ri.needs_manual_review = (COALESCE(iuw.confidence,'LOW') = 'LOW'),
              ri.review_reason = CONCAT('UNIT_WEIGHT_MAP:', COALESCE(iuw.source,''))
            WHERE ri.weight_g IS NULL
              AND ri.quantity_value IS NOT NULL
            """
        )
        details["unit_weight_map"] = cur.rowcount

        # B. Ingredient-specific density for explicit volume units.
        cur.execute(
            """
            UPDATE recipe_ingredients ri
            JOIN units u ON u.id = ri.unit_id
            JOIN ingredient_densities d
              ON d.ingredient_id = ri.ingredient_id
             AND d.status = 'ACTIVE'
            SET
              ri.weight_g = ri.quantity_value *
                CASE u.canonical_unit
                  WHEN 'ml' THEN 1
                  WHEN 'l' THEN 1000
                  WHEN 'tbsp' THEN 15
                  WHEN 'tsp' THEN 5
                  ELSE NULL
                END * d.density_g_ml,
              ri.is_estimated = TRUE,
              ri.needs_manual_review = (COALESCE(d.confidence,'LOW') = 'LOW'),
              ri.review_reason = CONCAT('DENSITY_MAP:', COALESCE(d.source,''))
            WHERE ri.weight_g IS NULL
              AND ri.quantity_value IS NOT NULL
              AND u.canonical_unit IN ('ml','l','tbsp','tsp')
            """
        )
        details["density_map"] = cur.rowcount

        # C. High-confidence qualitative rules only.
        #    "適量" is intentionally not assigned a fixed number unless a future
        #    researched rule is explicitly stored as auto_convert=TRUE.
        cur.execute(
            """
            UPDATE recipe_ingredients ri
            JOIN units u ON u.id = ri.unit_id
            JOIN qualitative_amount_rules qar
              ON qar.ingredient_id = ri.ingredient_id
             AND qar.qualitative_term = u.canonical_unit
             AND qar.status = 'ACTIVE'
             AND qar.auto_convert = TRUE
             AND qar.confidence_score >= 80
            SET
              ri.weight_g = qar.default_grams,
              ri.is_estimated = TRUE,
              ri.needs_manual_review = FALSE,
              ri.review_reason = CONCAT(
                'QUALITATIVE_RULE:', qar.qualitative_term,
                ':confidence=', qar.confidence_score
              )
            WHERE ri.weight_g IS NULL
            """
        )
        details["qualitative_high_confidence"] = cur.rowcount

        # D. Very small curated count fallbacks.
        cur.execute(
            """
            SELECT ri.id, ri.quantity_value, i.canonical_name, u.canonical_unit
            FROM recipe_ingredients ri
            JOIN ingredients i ON i.id = ri.ingredient_id
            JOIN units u ON u.id = ri.unit_id
            WHERE ri.weight_g IS NULL
              AND ri.quantity_value IS NOT NULL
            ORDER BY ri.id
            """
        )
        for row in cur.fetchall():
            rule = COUNT_WEIGHT_FALLBACK.get((row["canonical_name"], row["canonical_unit"]))
            if not rule:
                continue
            grams_per_unit, confidence, source = rule
            cur.execute(
                """
                UPDATE recipe_ingredients
                SET weight_g=%s,
                    is_estimated=TRUE,
                    needs_manual_review=%s,
                    review_reason=%s
                WHERE id=%s AND weight_g IS NULL
                """,
                (
                    float(row["quantity_value"]) * grams_per_unit,
                    confidence == "LOW",
                    f"COUNT_FALLBACK:{source}:{confidence}",
                    row["id"],
                ),
            )
            details["count_fallback"] += cur.rowcount

        # E. Conservative water/broth density fallback.
        cur.execute(
            """
            SELECT ri.id, ri.quantity_value, i.canonical_name, u.canonical_unit
            FROM recipe_ingredients ri
            JOIN ingredients i ON i.id = ri.ingredient_id
            JOIN units u ON u.id = ri.unit_id
            WHERE ri.weight_g IS NULL
              AND ri.quantity_value IS NOT NULL
              AND u.canonical_unit IN ('ml','l','tbsp','tsp')
            ORDER BY ri.id
            """
        )
        for row in cur.fetchall():
            rule = VOLUME_DENSITY_FALLBACK.get(row["canonical_name"])
            if not rule:
                continue
            density, confidence, source = rule
            ml_factor = VOLUME_TO_ML[row["canonical_unit"]]
            weight = float(row["quantity_value"]) * ml_factor * density
            cur.execute(
                """
                UPDATE recipe_ingredients
                SET weight_g=%s,
                    is_estimated=TRUE,
                    needs_manual_review=%s,
                    review_reason=%s
                WHERE id=%s AND weight_g IS NULL
                """,
                (weight, confidence == "LOW", f"VOLUME_FALLBACK:{source}:{confidence}", row["id"]),
            )
            details["volume_fallback"] += cur.rowcount

        # F. Keep unsupported qualitative amounts NULL and explicitly flagged.
        cur.execute(
            """
            UPDATE recipe_ingredients ri
            JOIN units u ON u.id = ri.unit_id
            SET ri.needs_manual_review=TRUE,
                ri.review_reason=COALESCE(ri.review_reason,'QUALITATIVE_NO_HIGH_CONFIDENCE_RULE')
            WHERE ri.weight_g IS NULL
              AND u.is_qualitative=TRUE
            """
        )
        details["unresolved_qualitative"] = cur.rowcount

        cur.execute(
            """
            SELECT
              COUNT(*) AS total_lines,
              SUM(weight_g IS NOT NULL) AS has_weight,
              SUM(weight_g IS NULL) AS missing_weight,
              SUM(is_estimated=TRUE AND weight_g IS NOT NULL) AS estimated_weight_lines
            FROM recipe_ingredients
            """
        )
        summary = cur.fetchone()
        conn.commit()

    report = {"applied": details, "summary": summary}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("weight estimates applied")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
