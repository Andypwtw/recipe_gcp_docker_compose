from __future__ import annotations

from app.db import get_connection


def resolve_categories_from_text(text: str) -> list[dict]:
    query_text = str(text or "").strip()

    if not query_text:
        return []

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ca.id AS alias_id,
                ca.alias_name,
                ca.priority,
                c.id AS category_id,
                c.category_name,
                c.category_type
            FROM category_aliases ca
            JOIN categories c
              ON c.id = ca.category_id
            WHERE ca.is_active = TRUE
              AND c.is_active = TRUE
            ORDER BY
                CHAR_LENGTH(ca.alias_name) DESC,
                ca.priority ASC,
                ca.id ASC
            """
        )
        candidates = cur.fetchall()

    matched_by_category = {}

    for row in candidates:
        alias = str(row["alias_name"] or "").strip()

        if not alias or alias not in query_text:
            continue

        category_id = row["category_id"]

        if category_id not in matched_by_category:
            matched_by_category[category_id] = {
                "category_id": category_id,
                "category_name": row["category_name"],
                "category_type": row["category_type"],
                "matched_alias": alias,
            }

    return list(matched_by_category.values())
