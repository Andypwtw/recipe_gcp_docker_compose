from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import date

from app.db import get_connection
from app.services.normalization import unit_type


SRC = Path("/workspace/data/processed/recipes_normalized.json")
KEYWORD_SPLIT_RE = re.compile(r"[,，、;/；|｜]+")


def parse_published_date(value: object) -> date | None:
    """Extract YYYY-M-D from YTower date/time text and return a MySQL-safe DATE."""
    text = str(value or "").strip()
    match = re.search(r"(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})", text)
    if not match:
        return None
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None


def split_keywords(raw_keywords: str) -> list[str]:
    seen = set()
    result = []

    for part in KEYWORD_SPLIT_RE.split(str(raw_keywords or "")):
        keyword = re.sub(r"\s+", " ", part).strip()

        if not keyword or keyword in seen:
            continue

        seen.add(keyword)
        result.append(keyword)

    return result


def main():
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    keyword_links = 0

    with get_connection() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """
                INSERT INTO recipes(
                    seq,name,published_date,source_url,raw_keywords,steps
                )
                VALUES(%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    name=VALUES(name),
                    published_date=VALUES(published_date),
                    source_url=VALUES(source_url),
                    raw_keywords=VALUES(raw_keywords),
                    steps=VALUES(steps)
                """,
                (
                    r["seq"],
                    r["name"],
                    parse_published_date(r.get("published_date")),
                    r["source_url"],
                    r["raw_keywords"],
                    r["steps"],
                ),
            )

            cur.execute(
                "SELECT id FROM recipes WHERE seq=%s",
                (r["seq"],),
            )
            recipe_id = cur.fetchone()["id"]

            # Keyword 關聯每次重建，避免舊關聯殘留。
            cur.execute(
                "DELETE FROM recipe_keywords WHERE recipe_id=%s",
                (recipe_id,),
            )

            for keyword in split_keywords(r.get("raw_keywords", "")):
                cur.execute(
                    """
                    INSERT INTO keywords(keyword_name)
                    VALUES(%s)
                    ON DUPLICATE KEY UPDATE
                        keyword_name=VALUES(keyword_name)
                    """,
                    (keyword,),
                )
                cur.execute(
                    "SELECT id FROM keywords WHERE keyword_name=%s",
                    (keyword,),
                )
                keyword_id = cur.fetchone()["id"]

                cur.execute(
                    """
                    INSERT IGNORE INTO recipe_keywords(
                        recipe_id,
                        keyword_id
                    )
                    VALUES(%s,%s)
                    """,
                    (recipe_id, keyword_id),
                )
                keyword_links += 1

            cur.execute(
                "DELETE FROM recipe_ingredients WHERE recipe_id=%s",
                (recipe_id,),
            )

            for ing in r["ingredients"]:
                name = ing["canonical_name"]

                cur.execute(
                    """
                    INSERT INTO ingredients(canonical_name)
                    VALUES(%s)
                    ON DUPLICATE KEY UPDATE
                        canonical_name=VALUES(canonical_name)
                    """,
                    (name,),
                )
                cur.execute(
                    "SELECT id FROM ingredients WHERE canonical_name=%s",
                    (name,),
                )
                ingredient_id = cur.fetchone()["id"]

                unit_id = None
                if ing["unit"]:
                    normalized_unit_type = unit_type(ing["unit"])

                    cur.execute(
                        """
                        INSERT INTO units(canonical_unit,unit_type,is_qualitative)
                        VALUES(%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                            canonical_unit=VALUES(canonical_unit),
                            unit_type=VALUES(unit_type),
                            is_qualitative=VALUES(is_qualitative)
                        """,
                        (
                            ing["unit"],
                            normalized_unit_type,
                            normalized_unit_type == "qualitative",
                        ),
                    )
                    cur.execute(
                        "SELECT id FROM units WHERE canonical_unit=%s",
                        (ing["unit"],),
                    )
                    unit_id = cur.fetchone()["id"]

                cur.execute(
                    """
                    INSERT INTO recipe_ingredients
                    (
                        recipe_id,line_no,raw_text,raw_name,
                        ingredient_id,quantity_min,quantity_max,
                        quantity_value,unit_id,weight_g,is_estimated
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        recipe_id,
                        ing["line_no"],
                        ing["raw_text"],
                        ing["raw_name"],
                        ingredient_id,
                        ing.get("quantity_min"),
                        ing.get("quantity_max"),
                        ing["quantity_value"],
                        unit_id,
                        ing["weight_g"],
                        ing.get("is_estimated", False),
                    ),
                )

        # 移除已無任何食譜使用的 keyword。
        cur.execute(
            """
            DELETE k
            FROM keywords k
            LEFT JOIN recipe_keywords rk
              ON rk.keyword_id = k.id
            WHERE rk.keyword_id IS NULL
            """
        )

        conn.commit()

    print(f"imported recipes={len(rows)}")
    print(f"recipe_keyword links={keyword_links}")


if __name__ == "__main__":
    main()
