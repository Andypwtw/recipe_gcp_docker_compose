from __future__ import annotations

from app.db import get_connection
from app.services.category_resolver import resolve_categories_from_text
from app.services.ingredient_intent import excludes_for_group, patterns_for_group


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
    """Build one EXISTS condition for a logical ingredient request.

    Patterns inside one group are OR. Exclusions are AND NOT LIKE. The caller
    combines multiple EXISTS clauses with AND, so every requested ingredient
    group must be present in the recipe.
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

    # V11-T-3: ingredient_group detected from natural language is no longer
    # treated as a category-only filter. It becomes an actual ingredient
    # constraint so "我想吃雞肉" cannot return beef/pork-only recipes.
    text_ingredient_groups: list[str] = []
    category_ids: list[int] = []
    for row in text_matches:
        category_type = str(row.get("category_type") or "")
        category_name = str(row.get("category_name") or "").strip()
        if category_type == "ingredient_group" and patterns_for_group(category_name):
            if category_name not in text_ingredient_groups:
                text_ingredient_groups.append(category_name)
            # Generic ingredient requests such as 雞肉 should describe the
            # recipe's primary ingredient group, not merely an incidental line
            # item. Keep the normalized recipe category as an additional AND
            # condition while also requiring a real matching ingredient below.
            category_id = row["category_id"]
            if category_id not in category_ids:
                category_ids.append(category_id)
            continue
        category_id = row["category_id"]
        if category_id not in category_ids:
            category_ids.append(category_id)

    # If Hermes explicitly sends ingredients=["雞肉"], apply the same primary
    # ingredient-group category constraint used by natural-language detection.
    # This prevents recipes that contain a small incidental amount of chicken
    # from ranking as a chicken recommendation (e.g. a beef hot pot with chicken).
    explicit_group_names = [
        name for name in explicit_ingredients if patterns_for_group(name)
    ]
    explicit_group_ids = resolve_explicit_category_ids(explicit_group_names)
    for category_id in explicit_group_ids:
        if category_id not in category_ids:
            category_ids.append(category_id)

    # Explicit categories remain explicit category filters for backward
    # compatibility.
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

    # Every explicit ingredient is required (AND semantics across ingredients).
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

    # Ingredient groups recognized from text are also required.
    for group_name in text_ingredient_groups:
        # Avoid duplicating the same logical requirement when Hermes already
        # supplied ingredients=["雞肉"] together with text="我想吃雞肉".
        if group_name in explicit_ingredients:
            continue
        clause, clause_params = _ingredient_exists_clause(
            patterns_for_group(group_name),
            excludes_for_group(group_name),
        )
        where_parts.append(clause)
        params.extend(clause_params)

    # Non-ingredient categories continue to use normalized recipe categories.
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
        1 for group_name in text_ingredient_groups
        if group_name not in explicit_ingredients
    )

    return (
        ingredient_request_count,
        category_ids,
        joins,
        where_parts,
        params,
        text_ingredient_groups,
    )


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
    ingredient_request_count, category_ids, joins, where_parts, params, _ = (
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

    # V11-T-3: deterministic ordering avoids unrelated-looking random results.
    # Ingredient constraints already guarantee that requested ingredients exist.
    order_sql = "matched_ingredients DESC, r.seq"

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

    與 recommend_recipes 分開，保留網頁分頁契約；V11-T-3 的兩條推薦路徑
    都使用必要食材條件，避免自然語言食材需求被分類隨機結果稀釋。
    """
    ingredient_request_count, category_ids, joins, where_parts, base_params, _ = (
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
