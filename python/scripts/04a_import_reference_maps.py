from __future__ import annotations

import json
from pathlib import Path

from app.db import get_connection
from app.services.calculation_policy import classify_ingredient, load_calculation_policy

UNIT_MAP = Path("/workspace/data/reference/unit_weight_map.json")
DENSITY = Path("/workspace/data/reference/ingredient_density_map.json")
QUALITATIVE_RULES = Path("/workspace/data/reference/qualitative_amount_rules.json")
CALCULATION_RULES = Path("/workspace/data/reference/ingredient_calculation_rules.json")


def ensure_qualitative_rules_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qualitative_amount_rules (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          ingredient_id BIGINT NOT NULL,
          ingredient_alias VARCHAR(255) NOT NULL,
          qualitative_term VARCHAR(50) NOT NULL,
          canonical_rule_name VARCHAR(255),
          conversion_type VARCHAR(50) NOT NULL DEFAULT 'ESTIMATED_HIGH_CONFIDENCE',
          default_grams DECIMAL(18,6) NOT NULL,
          min_grams DECIMAL(18,6),
          max_grams DECIMAL(18,6),
          confidence_score DECIMAL(6,2) NOT NULL,
          auto_convert BOOLEAN NOT NULL DEFAULT FALSE,
          status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
          source_url_1 TEXT,
          source_url_2 TEXT,
          note VARCHAR(1000),
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_qar_alias_term (ingredient_alias, qualitative_term),
          INDEX idx_qar_ingredient_term (ingredient_id, qualitative_term, status),
          CONSTRAINT fk_qar_ingredient
            FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
            ON DELETE CASCADE
        )
        """
    )



def ensure_calculation_rules_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ingredient_calculation_rules (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          ingredient_id BIGINT NOT NULL,
          rule_key VARCHAR(100) NOT NULL,
          ingredient_category VARCHAR(60) NOT NULL DEFAULT 'FOOD',
          is_seasoning BOOLEAN NOT NULL DEFAULT FALSE,
          calorie_policy VARCHAR(20) NOT NULL DEFAULT 'INCLUDE',
          price_policy VARCHAR(20) NOT NULL DEFAULT 'INCLUDE',
          calorie_ignore_threshold_kcal DECIMAL(10,4) NOT NULL DEFAULT 5.0000,
          fallback_kcal_per_100g DECIMAL(12,4) NULL,
          confidence_score DECIMAL(6,2) NOT NULL DEFAULT 100.00,
          match_reason VARCHAR(255),
          source VARCHAR(255) NOT NULL DEFAULT 'ingredient_calculation_rules.json',
          note VARCHAR(1000),
          status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_icr_ingredient (ingredient_id),
          INDEX idx_icr_price_policy (price_policy,is_seasoning,status),
          INDEX idx_icr_calorie_policy (calorie_policy,status),
          CONSTRAINT fk_icr_ingredient
            FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
            ON DELETE CASCADE
        )
        """
    )

def get_or_create_ingredient(cur, name: str) -> int:
    cur.execute(
        """
        INSERT INTO ingredients(canonical_name)
        VALUES(%s)
        ON DUPLICATE KEY UPDATE canonical_name=VALUES(canonical_name)
        """,
        (name,),
    )
    cur.execute("SELECT id FROM ingredients WHERE canonical_name=%s", (name,))
    return cur.fetchone()["id"]


def get_or_create_unit(cur, unit: str, unit_type: str) -> int:
    cur.execute(
        """
        INSERT INTO units(canonical_unit,unit_type,is_qualitative)
        VALUES(%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          unit_type=VALUES(unit_type),
          is_qualitative=VALUES(is_qualitative)
        """,
        (unit, unit_type, unit_type == "qualitative"),
    )
    cur.execute("SELECT id FROM units WHERE canonical_unit=%s", (unit,))
    return cur.fetchone()["id"]


def main():
    imported_unit_weights = 0
    imported_densities = 0
    imported_qualitative = 0
    imported_calculation_rules = 0

    with get_connection() as conn, conn.cursor() as cur:
        ensure_qualitative_rules_table(cur)
        ensure_calculation_rules_table(cur)

        if UNIT_MAP.exists():
            for r in json.loads(UNIT_MAP.read_text(encoding="utf-8")):
                iid = get_or_create_ingredient(cur, r["ingredient"])
                uid = get_or_create_unit(cur, r["unit"], "count")
                cur.execute(
                    """
                    INSERT INTO ingredient_unit_weights
                    (ingredient_id,unit_id,grams_per_unit,confidence,status,source,note)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      grams_per_unit=VALUES(grams_per_unit),
                      confidence=VALUES(confidence),
                      status=VALUES(status),
                      source=VALUES(source),
                      note=VALUES(note)
                    """,
                    (
                        iid,
                        uid,
                        r["grams_per_unit"],
                        r.get("confidence", "LOW"),
                        r.get("status", "ACTIVE"),
                        r.get("source", "unit_weight_map.json"),
                        f"samples={r.get('sample_count', 1)}; dispersion={r.get('dispersion_ratio', '')}",
                    ),
                )
                imported_unit_weights += 1

        if DENSITY.exists():
            for r in json.loads(DENSITY.read_text(encoding="utf-8")):
                iid = get_or_create_ingredient(cur, r["ingredient"])
                cur.execute(
                    """
                    INSERT INTO ingredient_densities
                    (ingredient_id,density_g_ml,density_type,confidence,status,source,note)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      density_g_ml=VALUES(density_g_ml),
                      density_type=VALUES(density_type),
                      confidence=VALUES(confidence),
                      status=VALUES(status),
                      source=VALUES(source),
                      note=VALUES(note)
                    """,
                    (
                        iid,
                        r["density_g_ml"],
                        r.get("density_type", "liquid"),
                        r.get("confidence", "MEDIUM"),
                        r.get("status", "ACTIVE"),
                        r.get("source", "json"),
                        r.get("note"),
                    ),
                )
                imported_densities += 1

        if QUALITATIVE_RULES.exists():
            for r in json.loads(QUALITATIVE_RULES.read_text(encoding="utf-8")):
                # The alias is the ingredient name actually used by recipe_ingredients.
                iid = get_or_create_ingredient(cur, r["ingredient_alias"])
                get_or_create_unit(cur, r["qualitative_term"], "qualitative")

                cur.execute(
                    """
                    INSERT INTO qualitative_amount_rules
                    (
                      ingredient_id,ingredient_alias,qualitative_term,
                      canonical_rule_name,conversion_type,
                      default_grams,min_grams,max_grams,
                      confidence_score,auto_convert,status,
                      source_url_1,source_url_2,note
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      ingredient_id=VALUES(ingredient_id),
                      canonical_rule_name=VALUES(canonical_rule_name),
                      conversion_type=VALUES(conversion_type),
                      default_grams=VALUES(default_grams),
                      min_grams=VALUES(min_grams),
                      max_grams=VALUES(max_grams),
                      confidence_score=VALUES(confidence_score),
                      auto_convert=VALUES(auto_convert),
                      status=VALUES(status),
                      source_url_1=VALUES(source_url_1),
                      source_url_2=VALUES(source_url_2),
                      note=VALUES(note)
                    """,
                    (
                        iid,
                        r["ingredient_alias"],
                        r["qualitative_term"],
                        r.get("canonical_rule_name"),
                        r.get("conversion_type", "ESTIMATED_HIGH_CONFIDENCE"),
                        r["default_grams"],
                        r.get("min_grams"),
                        r.get("max_grams"),
                        r["confidence_score"],
                        bool(r.get("auto_convert", False)),
                        r.get("status", "ACTIVE"),
                        r.get("source_url_1"),
                        r.get("source_url_2"),
                        r.get("notes"),
                    ),
                )
                imported_qualitative += 1


        if CALCULATION_RULES.exists():
            policy_data = load_calculation_policy(CALCULATION_RULES)
            # This table is derived from the current ingredient master. Rebuild it
            # every ETL run so changed policy definitions cannot leave stale rows.
            cur.execute("DELETE FROM ingredient_calculation_rules")
            cur.execute("SELECT id,canonical_name FROM ingredients ORDER BY id")
            ingredient_rows = cur.fetchall()
            for ingredient in ingredient_rows:
                policy = classify_ingredient(ingredient["canonical_name"], policy_data)
                if not policy["matched"]:
                    continue
                cur.execute(
                    """
                    INSERT INTO ingredient_calculation_rules
                    (
                      ingredient_id,rule_key,ingredient_category,is_seasoning,
                      calorie_policy,price_policy,calorie_ignore_threshold_kcal,
                      fallback_kcal_per_100g,confidence_score,match_reason,source,note,status
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE')
                    ON DUPLICATE KEY UPDATE
                      rule_key=VALUES(rule_key),
                      ingredient_category=VALUES(ingredient_category),
                      is_seasoning=VALUES(is_seasoning),
                      calorie_policy=VALUES(calorie_policy),
                      price_policy=VALUES(price_policy),
                      calorie_ignore_threshold_kcal=VALUES(calorie_ignore_threshold_kcal),
                      fallback_kcal_per_100g=VALUES(fallback_kcal_per_100g),
                      confidence_score=VALUES(confidence_score),
                      match_reason=VALUES(match_reason),
                      source=VALUES(source),
                      note=VALUES(note),
                      status='ACTIVE'
                    """,
                    (
                        ingredient["id"],
                        policy["rule_key"],
                        policy["ingredient_category"],
                        policy["is_seasoning"],
                        policy["calorie_policy"],
                        policy["price_policy"],
                        policy["calorie_ignore_threshold_kcal"],
                        policy["fallback_kcal_per_100g"],
                        policy["confidence_score"],
                        policy["match_reason"],
                        "ingredient_calculation_rules.json",
                        policy.get("note"),
                    ),
                )
                imported_calculation_rules += 1

        conn.commit()

    print("reference maps imported")
    print(f"unit weights={imported_unit_weights}")
    print(f"densities={imported_densities}")
    print(f"qualitative high-confidence rules={imported_qualitative}")
    print(f"ingredient calculation policies={imported_calculation_rules}")


if __name__ == "__main__":
    main()
