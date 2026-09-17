from __future__ import annotations

from app.db import get_connection
from app.services.category_resolver import resolve_categories_from_text


def resolve_explicit_category_ids(categories: list[str]) -> list[int]:
    values = [
        str(value or "").strip()
        for value in categories or []
        if str(value or "").strip()
    ]

    if not values:
        return []

    placeholders = ",".join(["%s"] * len(values))

    sql = f"""
        SELECT DISTINCT
            c.id AS category_id
        FROM category_aliases ca
        JOIN categories c
          ON c.id = ca.category_id
        WHERE ca.is_active = TRUE
          AND c.is_active = TRUE
          AND ca.alias_name IN ({placeholders})
    """

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(values))
        return [row["category_id"] for row in cur.fetchall()]


def _build_recommendation_filters(
    ingredients: list[str] | None = None,
    categories: list[str] | None = None,
    text: str | None = None,
):
    ingredient_patterns = [
        f"%{str(name).strip()}%"
        for name in ingredients or []
        if str(name).strip()
    ]

    text_matches = resolve_categories_from_text(text or "")
    category_ids = [row["category_id"] for row in text_matches]

    explicit_ids = resolve_explicit_category_ids(categories or [])
    for category_id in explicit_ids:
        if category_id not in category_ids:
            category_ids.append(category_id)

    joins = [
        """
        LEFT JOIN recipe_nutrition_summary rns
          ON rns.recipe_id = r.id
        """
    ]
    where_parts: list[str] = []
    params: list[object] = []

    if ingredient_patterns:
        joins.extend(
            [
                """
                JOIN recipe_ingredients ri
                  ON ri.recipe_id = r.id
                """,
                """
                JOIN ingredients i
                  ON i.id = ri.ingredient_id
                """,
            ]
        )
        ingredient_where = " OR ".join(
            ["i.canonical_name LIKE %s"] * len(ingredient_patterns)
        )
        where_parts.append(f"({ingredient_where})")
        params.extend(ingredient_patterns)

    # 多分類全部 AND：每一個 category_id 各一個 EXISTS。
    for category_id in category_ids:
        where_parts.append(
            """
            EXISTS (
                SELECT 1
                FROM recipe_categories rc
                WHERE rc.recipe_id = r.id
                  AND rc.category_id = %s
            )
            """
        )
        params.append(category_id)

    return ingredient_patterns, category_ids, joins, where_parts, params


def recommend_recipes(
    ingredients: list[str] | None = None,
    limit: int = 10,
    categories: list[str] | None = None,
    text: str | None = None,
):
    """既有推薦介面。

    保留原本回傳 list 的行為，供 Hermes 既有 API 使用，避免此次網頁測試版
    修改破壞 Hermes 的 request/response 契約。
    """
    ingredient_patterns, category_ids, joins, where_parts, params = (
        _build_recommendation_filters(
            ingredients=ingredients,
            categories=categories,
            text=text,
        )
    )

    if not ingredient_patterns and not category_ids:
        return []

    where_sql = " AND ".join(where_parts)
    matched_expr = "COUNT(DISTINCT i.id)" if ingredient_patterns else "0"

    # 保留原本 Hermes/既有推薦行為：有分類時採隨機排序。
    order_sql = "RAND()" if category_ids else "matched_ingredients DESC, r.seq"

    sql = f"""
        SELECT
            r.seq,
            r.name,
            {matched_expr} AS matched_ingredients,
            CAST(TRUNCATE(rns.energy_kcal, 0) AS SIGNED) AS energy_kcal,
            ROUND(rns.estimated_price, 2) AS estimated_price,
            ROUND(rns.coverage_percent, 2) AS calorie_coverage_percent,
            ROUND(rns.price_coverage_percent, 2) AS price_coverage_percent,
            rns.calorie_status,
            rns.price_status
        FROM recipes r
        {' '.join(joins)}
        WHERE {where_sql}
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
        ORDER BY {order_sql}
        LIMIT %s
    """

    params.append(min(max(int(limit), 1), 100))

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()


def recommend_recipes_page(
    ingredients: list[str] | None = None,
    categories: list[str] | None = None,
    text: str | None = None,
    page: int = 1,
    limit: int = 12,
) -> dict:
    """供網頁版使用的穩定分頁推薦結果。

    與 recommend_recipes 分開，確保 Hermes 保持原本隨機排序與 items 契約。
    """
    ingredient_patterns, category_ids, joins, where_parts, base_params = (
        _build_recommendation_filters(
            ingredients=ingredients,
            categories=categories,
            text=text,
        )
    )

    page = max(int(page), 1)
    limit = min(max(int(limit), 1), 100)
    offset = (page - 1) * limit

    if not ingredient_patterns and not category_ids:
        return {
            "items": [],
            "page": page,
            "limit": limit,
            "total": 0,
            "total_pages": 0,
        }

    where_sql = " AND ".join(where_parts)
    matched_expr = "COUNT(DISTINCT i.id)" if ingredient_patterns else "0"

    count_sql = f"""
        SELECT COUNT(DISTINCT r.id) AS total
        FROM recipes r
        {' '.join(joins)}
        WHERE {where_sql}
    """

    data_sql = f"""
        SELECT
            r.id,
            r.seq,
            r.name,
            {matched_expr} AS matched_ingredients,
            CAST(TRUNCATE(rns.energy_kcal, 0) AS SIGNED) AS energy_kcal,
            ROUND(rns.estimated_price, 2) AS estimated_price,
            ROUND(rns.coverage_percent, 2) AS calorie_coverage_percent,
            ROUND(rns.price_coverage_percent, 2) AS price_coverage_percent,
            rns.calorie_status,
            rns.price_status
        FROM recipes r
        {' '.join(joins)}
        WHERE {where_sql}
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
        ORDER BY matched_ingredients DESC, r.seq
        LIMIT %s OFFSET %s
    """

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(count_sql, tuple(base_params))
        total = int((cur.fetchone() or {}).get("total") or 0)

        data_params = [*base_params, limit, offset]
        cur.execute(data_sql, tuple(data_params))
        items = cur.fetchall()

    total_pages = (total + limit - 1) // limit if total else 0
    return {
        "items": items,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
    }


def recommend_by_ingredients(
    ingredients: list[str],
    limit: int = 10,
    categories: list[str] | None = None,
):
    return recommend_recipes(
        ingredients=ingredients,
        limit=limit,
        categories=categories,
    )
