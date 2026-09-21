from __future__ import annotations

from flask import Flask, jsonify, request
import os
import hmac

from app.db import get_connection
from app.services.category_resolver import resolve_categories_from_text
from app.services.recommendation import (
    recommend_recipes,
    recommend_recipes_page,
    resolve_explicit_category_ids,
)


app = Flask(__name__)


def _payload_string_list(value) -> list[str]:
    """Normalize optional JSON string/list fields into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _payload_text(payload: dict) -> str:
    """Accept both current `text` and legacy Hermes `message` payload keys."""
    return str(payload.get("text") or payload.get("message") or "").strip()


def _parse_positive_int(name: str, default: int, maximum: int | None = None) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    value = max(value, 1)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _attach_categories(cur, items: list[dict]) -> list[dict]:
    if not items:
        return items

    recipe_ids = [item.get("id") for item in items if item.get("id") is not None]
    if not recipe_ids:
        for item in items:
            item.setdefault("categories", [])
        return items

    placeholders = ",".join(["%s"] * len(recipe_ids))
    cur.execute(
        f"""
        SELECT
            rc.recipe_id,
            c.id AS category_id,
            c.category_name,
            c.category_type
        FROM recipe_categories rc
        JOIN categories c
          ON c.id = rc.category_id
        WHERE rc.recipe_id IN ({placeholders})
          AND c.is_active = TRUE
        ORDER BY c.category_type, c.category_name
        """,
        tuple(recipe_ids),
    )

    category_map: dict[int, list[dict]] = {rid: [] for rid in recipe_ids}
    for row in cur.fetchall():
        category_map.setdefault(row["recipe_id"], []).append(
            {
                "category_id": row["category_id"],
                "category_name": row["category_name"],
                "category_type": row["category_type"],
            }
        )

    for item in items:
        rid = item.get("id")
        item["categories"] = category_map.get(rid, [])
        item.pop("id", None)

    return items


def _category_exists_clauses(category_ids: list[int]) -> tuple[str, list[int]]:
    parts = []
    params: list[int] = []
    for category_id in category_ids:
        parts.append(
            """
            EXISTS (
                SELECT 1
                FROM recipe_categories rc_filter
                WHERE rc_filter.recipe_id = r.id
                  AND rc_filter.category_id = %s
            )
            """
        )
        params.append(category_id)
    return " AND ".join(parts), params


@app.get("/health")
def health():
    status = {
        "api": "ok",
        "mysql": "unknown",
        "mode": "recipe_project_v11",
    }

    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        status["mysql"] = "ok"
    except Exception as exc:
        status["mysql"] = f"error: {exc}"

    code = 200 if status["mysql"] == "ok" else 503
    return jsonify(status), code


@app.get("/api/v1/categories")
def list_categories():
    category_type = (request.args.get("type") or "").strip()

    sql = """
        SELECT
            id AS category_id,
            category_name,
            category_type
        FROM categories
        WHERE is_active = TRUE
    """
    params: list[object] = []

    if category_type:
        sql += " AND category_type = %s"
        params.append(category_type)

    sql += " ORDER BY category_type, category_name"

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        items = cur.fetchall()

    return jsonify({"items": items})


@app.get("/api/v1/recipes")
def list_recipes():
    page = _parse_positive_int("page", 1)
    limit = _parse_positive_int("limit", 12, maximum=100)
    offset = (page - 1) * limit

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM recipes")
        total = int((cur.fetchone() or {}).get("total") or 0)

        cur.execute(
            """
            SELECT
                r.id,
                r.seq,
                r.name,
                r.raw_keywords,
                r.source_url,
                CAST(TRUNCATE(rns.energy_kcal, 0) AS SIGNED) AS energy_kcal,
                CAST(TRUNCATE(rns.ingredient_energy_kcal, 0) AS SIGNED) AS ingredient_energy_kcal,
                CAST(TRUNCATE(rns.seasoning_energy_kcal, 0) AS SIGNED) AS seasoning_energy_kcal,
                CAST(rns.estimated_price AS SIGNED) AS estimated_price,
                CASE WHEN rns.estimated_price IS NULL THEN '$無資料' ELSE CONCAT('$', CAST(rns.estimated_price AS SIGNED)) END AS total_price_display,
                ROUND(rns.coverage_percent, 2) AS calorie_coverage_percent,
                ROUND(rns.price_coverage_percent, 2) AS price_coverage_percent,
                rns.calorie_status,
                rns.price_status
            FROM recipes r
            LEFT JOIN recipe_nutrition_summary rns
              ON rns.recipe_id = r.id
            ORDER BY r.seq
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        items = _attach_categories(cur, cur.fetchall())

    total_pages = (total + limit - 1) // limit if total else 0
    return jsonify(
        {
            "items": items,
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
        }
    )


@app.get("/api/v1/recipes/random")
def random_recipes():
    limit = _parse_positive_int("limit", 15, maximum=50)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.id,
                r.seq,
                r.name,
                r.raw_keywords,
                CAST(TRUNCATE(rns.energy_kcal, 0) AS SIGNED) AS energy_kcal,
                CAST(TRUNCATE(rns.ingredient_energy_kcal, 0) AS SIGNED) AS ingredient_energy_kcal,
                CAST(TRUNCATE(rns.seasoning_energy_kcal, 0) AS SIGNED) AS seasoning_energy_kcal,
                CAST(rns.estimated_price AS SIGNED) AS estimated_price,
                CASE WHEN rns.estimated_price IS NULL THEN '$無資料' ELSE CONCAT('$', CAST(rns.estimated_price AS SIGNED)) END AS total_price_display,
                ROUND(rns.coverage_percent, 2) AS calorie_coverage_percent,
                ROUND(rns.price_coverage_percent, 2) AS price_coverage_percent,
                rns.calorie_status,
                rns.price_status
            FROM recipes r
            LEFT JOIN recipe_nutrition_summary rns
              ON rns.recipe_id = r.id
            ORDER BY RAND()
            LIMIT %s
            """,
            (limit,),
        )
        items = _attach_categories(cur, cur.fetchall())

    return jsonify({"items": items, "limit": limit})


@app.get("/api/v1/recipes/search")
def search_recipes():
    q = (request.args.get("q") or "").strip()
    page = _parse_positive_int("page", 1)
    limit = _parse_positive_int("limit", 12, maximum=100)
    offset = (page - 1) * limit

    if not q:
        return jsonify(
            {
                "items": [],
                "page": page,
                "limit": limit,
                "total": 0,
                "total_pages": 0,
            }
        )

    # categories 可重複傳入，也可用逗號分隔；此擴充保持既有 q/limit 相容。
    raw_categories = request.args.getlist("categories")
    category_values: list[str] = []
    for raw in raw_categories:
        category_values.extend([v.strip() for v in raw.split(",") if v.strip()])
    category_ids = resolve_explicit_category_ids(category_values)
    category_sql, category_params = _category_exists_clauses(category_ids)

    pattern = f"%{q}%"
    search_where = """
        (
            r.name LIKE %s
            OR i.canonical_name LIKE %s
            OR ri.raw_text LIKE %s
        )
    """
    where_sql = search_where
    if category_sql:
        where_sql += " AND " + category_sql

    base_params: list[object] = [pattern, pattern, pattern, *category_params]

    count_sql = f"""
        SELECT COUNT(DISTINCT r.id) AS total
        FROM recipes r
        LEFT JOIN recipe_ingredients ri
          ON ri.recipe_id = r.id
        LEFT JOIN ingredients i
          ON i.id = ri.ingredient_id
        WHERE {where_sql}
    """

    data_sql = f"""
        SELECT DISTINCT
            r.id,
            r.seq,
            r.name,
            r.source_url,
            r.raw_keywords,
            CAST(TRUNCATE(rns.energy_kcal, 0) AS SIGNED) AS energy_kcal,
                CAST(TRUNCATE(rns.ingredient_energy_kcal, 0) AS SIGNED) AS ingredient_energy_kcal,
                CAST(TRUNCATE(rns.seasoning_energy_kcal, 0) AS SIGNED) AS seasoning_energy_kcal,
            CAST(rns.estimated_price AS SIGNED) AS estimated_price,
                CASE WHEN rns.estimated_price IS NULL THEN '$無資料' ELSE CONCAT('$', CAST(rns.estimated_price AS SIGNED)) END AS total_price_display,
            ROUND(rns.coverage_percent, 2) AS calorie_coverage_percent,
            ROUND(rns.price_coverage_percent, 2) AS price_coverage_percent,
            rns.calorie_status,
            rns.price_status
        FROM recipes r
        LEFT JOIN recipe_ingredients ri
          ON ri.recipe_id = r.id
        LEFT JOIN ingredients i
          ON i.id = ri.ingredient_id
        LEFT JOIN recipe_nutrition_summary rns
          ON rns.recipe_id = r.id
        WHERE {where_sql}
        ORDER BY r.seq
        LIMIT %s OFFSET %s
    """

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(count_sql, tuple(base_params))
        total = int((cur.fetchone() or {}).get("total") or 0)

        cur.execute(data_sql, tuple([*base_params, limit, offset]))
        items = _attach_categories(cur, cur.fetchall())

    total_pages = (total + limit - 1) // limit if total else 0
    return jsonify(
        {
            "items": items,
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
        }
    )


@app.get("/api/v1/recipes/<seq>")
def get_recipe(seq: str):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.*,
                CAST(TRUNCATE(rns.energy_kcal, 0) AS SIGNED) AS energy_kcal,
                CAST(TRUNCATE(rns.ingredient_energy_kcal, 0) AS SIGNED) AS ingredient_energy_kcal,
                CAST(TRUNCATE(rns.seasoning_energy_kcal, 0) AS SIGNED) AS seasoning_energy_kcal,
                CAST(rns.estimated_price AS SIGNED) AS estimated_price,
                CASE WHEN rns.estimated_price IS NULL THEN '$無資料' ELSE CONCAT('$', CAST(rns.estimated_price AS SIGNED)) END AS total_price_display,
                ROUND(rns.coverage_percent, 2) AS calorie_coverage_percent,
                ROUND(rns.price_coverage_percent, 2) AS price_coverage_percent,
                rns.calorie_status,
                rns.price_status
            FROM recipes r
            LEFT JOIN recipe_nutrition_summary rns
              ON rns.recipe_id = r.id
            WHERE r.seq = %s
            """,
            (seq,),
        )
        recipe = cur.fetchone()

        if not recipe:
            return jsonify({"error": "not found"}), 404

        cur.execute(
            """
            SELECT
                ri.line_no,
                ri.raw_text,
                i.canonical_name,
                ri.weight_g,
                CASE
                    WHEN ri.weight_g IS NOT NULL AND ns.price_per_100g IS NOT NULL
                    THEN CAST(CEILING(ns.price_per_100g * ri.weight_g / 100) AS SIGNED)
                    ELSE NULL
                END AS estimated_price,
                CASE
                    WHEN ri.weight_g IS NOT NULL AND ns.price_per_100g IS NOT NULL
                    THEN CONCAT('$', CAST(CEILING(ns.price_per_100g * ri.weight_g / 100) AS SIGNED))
                    ELSE '$無資料'
                END AS price_display,
                CONCAT(
                    COALESCE(ri.raw_text, ''),
                    ' ',
                    CASE
                        WHEN ri.weight_g IS NOT NULL AND ns.price_per_100g IS NOT NULL
                        THEN CONCAT('$', CAST(CEILING(ns.price_per_100g * ri.weight_g / 100) AS SIGNED))
                        ELSE '$無資料'
                    END
                ) AS display_text
            FROM recipe_ingredients ri
            JOIN ingredients i
              ON i.id = ri.ingredient_id
            LEFT JOIN ingredient_nutrition_map inm
              ON inm.ingredient_id = ri.ingredient_id
             AND inm.status = 'APPROVED'
            LEFT JOIN nutrition_source ns
              ON ns.id = inm.nutrition_source_id
            WHERE ri.recipe_id = %s
            ORDER BY ri.line_no
            """,
            (recipe["id"],),
        )
        recipe["ingredients"] = cur.fetchall()

        category_holder = [{"id": recipe["id"]}]
        _attach_categories(cur, category_holder)
        recipe["categories"] = category_holder[0]["categories"]

    return jsonify(recipe)


@app.post("/api/v1/categories/resolve")
def resolve_categories():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    return jsonify({"items": resolve_categories_from_text(text)})


@app.post("/api/v1/recommend")
def recommend_for_web():
    payload = request.get_json(silent=True) or {}
    text = _payload_text(payload)
    ingredients = _payload_string_list(payload.get("ingredients"))
    categories = _payload_string_list(payload.get("categories"))

    try:
        page = max(int(payload.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        limit = min(max(int(payload.get("limit", 12)), 1), 100)
    except (TypeError, ValueError):
        limit = 12

    result = recommend_recipes_page(
        ingredients=ingredients,
        categories=categories,
        text=text,
        page=page,
        limit=limit,
    )

    with get_connection() as conn, conn.cursor() as cur:
        # recommend_recipes_page 暫時會帶內部 id，僅用來附加 categories，輸出前移除。
        result["items"] = _attach_categories(cur, result["items"])

    return jsonify(result)


@app.post("/api/v1/hermes/recommend")
def recommend_for_hermes():
    """Hermes-compatible route. Existing payload/response contract is preserved.

    If HERMES_API_KEY is configured, callers must provide the same value via
    X-API-Key or Authorization: Bearer <key>. Leaving the environment variable
    blank keeps trusted local-development behavior backward compatible.
    """
    expected_key = (os.getenv("HERMES_API_KEY") or "").strip()
    if expected_key:
        supplied_key = (request.headers.get("X-API-Key") or "").strip()
        auth = (request.headers.get("Authorization") or "").strip()
        if not supplied_key and auth.lower().startswith("bearer "):
            supplied_key = auth[7:].strip()
        if not supplied_key or not hmac.compare_digest(supplied_key, expected_key):
            return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    text = _payload_text(payload)
    ingredients = _payload_string_list(payload.get("ingredients"))
    categories = _payload_string_list(payload.get("categories"))

    try:
        limit = int(payload.get("limit", 10))
    except (TypeError, ValueError):
        limit = 10

    return jsonify(
        {
            "items": recommend_recipes(
                ingredients=ingredients,
                categories=categories,
                text=text,
                limit=limit,
            )
        }
    )

if __name__ == "__main__":
    # Docker/GCP test entrypoint. Binding to 0.0.0.0 exposes the service
    # through Docker's published port; default API port is 5001.
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5001"))
    app.run(host=host, port=port)
