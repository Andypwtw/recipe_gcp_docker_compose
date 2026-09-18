from __future__ import annotations

from app.db import get_connection
from app.services.category_resolver import resolve_categories_from_text
from app.services.ingredient_intent import (
    detect_ingredient_groups,
    excludes_for_group,
    patterns_for_group,
)


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


def _ingredient_exists_clause(
    patterns: list[str] | tuple[str, ...],
    excludes: list[str] | tuple[str, ...] = (),
) -> tuple[str, list[str]]:
    """Build one EXISTS condition for one logical ingredient request.

    Patterns within a group are OR.  Multiple ingredient requests are represented
    by multiple EXISTS clauses and therefore combine with AND.
    """
    positive = " OR ".join(["ix.canonical_name LIKE %s"] * len(patterns))
    params = list(patterns)

    exclude_sql = ""
    if excludes:
        exclude_sql = " AND " + " AND ".join(
            ["ix.canonical_name NOT LIKE %s"] * len(excludes)
        )
        params.extend(excludes)

    return (
        f"""
        EXISTS (
            SELECT 1
            FROM recipe_ingredients rix
            JOIN ingredients ix
              ON ix.id = rix.ingredient_id
            WHERE rix.recipe_id = r.id
              AND ({positive})
              {exclude_sql}
        )
        """,
        params,
    )


def _build_recommendation_filters(
    ingredients: list[str] | None = None,
    categories: list[str] | None = None,
    text: str | None = None,
):
    explicit_ingredients = [
        str(name or "").strip()
        for name in ingredients or []
        if str(name or "").strip()
    ]

    text_matches = resolve_categories_from_text(text or "")

    # Detect ingredient intent in two ways:
    # 1) normalized category aliases from MySQL;
    # 2) direct deterministic fallback such as "雞肉" in the raw text.
    text_ingredient_groups: list[str] = detect_ingredient_groups(text or "")
    category_ids: list[int] = []

    for row in text_matches:
        category_type = str(row.get("category_type") or "")
        category_name = str(row.get("category_name") or "").strip()

        if category_type == "ingredient_group" and patterns_for_group(category_name):
            if category_name not in text_ingredient_groups:
                text_ingredient_groups.append(category_name)

            # Ingredient-group intent (例如「我想吃雞肉」) is authoritative as
            # an ingredient filter.  Do NOT also require recipe_categories here:
            # category mappings can be incomplete and would incorrectly reduce
            # valid ingredient matches to zero.
            continue

        category_id = row["category_id"]
        if category_id not in category_ids:
            category_ids.append(category_id)

    # Explicit ingredient groups (ingredients=["雞肉"]) are enforced only by
    # recipe_ingredients.  They must not implicitly become mandatory category
    # filters, otherwise incomplete recipe_categories mappings can return 0 rows.

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

    # Every explicitly supplied ingredient is required.
    for name in explicit_ingredients:
        group_patterns = patterns_for_group(name)
        if group_patterns:
            clause, clause_params = _ingredient_exists_clause(
                group_patterns,
                excludes_for_group(name),
            )
        else:
            clause, clause_params = _ingredient_exists_clause((f"%{name}%",))
        where_parts.append(clause)
        params.extend(clause_params)

    # Natural-language ingredient groups are required as real ingredients.
    for group_name in text_ingredient_groups:
        if group_name in explicit_ingredients:
            continue
        clause, clause_params = _ingredient_exists_clause(
            patterns_for_group(group_name),
            excludes_for_group(group_name),
        )
        where_parts.append(clause)
        params.extend(clause_params)

    # Normalized category filters remain AND conditions.
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

    ingredient_request_count = len(explicit_ingredients) + sum(
        1
        for group_name in text_ingredient_groups
        if group_name not in explicit_ingredients
    )

    return ingredient_request_count, category_ids, joins, where_parts, params


def recommend_recipes(
    ingredients: list[str] | None = None,
    limit: int = 10,
    categories: list[str] | None = None,
    text: str | None = None,
):
    """Hermes-compatible recommendation list."""
    ingredient_request_count, category_ids, joins, where_parts, params = (
        _build_recommendation_filters(
            ingredients=ingredients,
            categories=categories,
            text=text,
        )
    )

    if not ingredient_request_count and not category_ids:
        return []

    where_sql = " AND ".join(where_parts)
    matched_expr = str(ingredient_request_count)

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
        ORDER BY matched_ingredients DESC, r.seq
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
    """Stable paginated recommendation result for the web frontend."""
    ingredient_request_count, category_ids, joins, where_parts, base_params = (
        _build_recommendation_filters(
            ingredients=ingredients,
            categories=categories,
            text=text,
        )
    )

    page = max(int(page), 1)
    limit = min(max(int(limit), 1), 100)
    offset = (page - 1) * limit

    if not ingredient_request_count and not category_ids:
        return {
            "items": [],
            "page": page,
            "limit": limit,
            "total": 0,
            "total_pages": 0,
        }

    where_sql = " AND ".join(where_parts)
    matched_expr = str(ingredient_request_count)

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
