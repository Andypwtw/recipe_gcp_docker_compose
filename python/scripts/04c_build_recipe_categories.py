from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from app.db import get_connection


REPORT = Path("/workspace/data/processed/category_build_report.json")
KEYWORD_SPLIT_RE = re.compile(r"[,，、;/；|｜]+")

CATEGORY_GROUPS = {
    "cuisine": {
        "中式料理",
        "西式料理",
        "日式料理",
        "韓式料理",
        "南洋料理",
        "其他料理",
    },
    "course": {
        "主菜",
        "開胃前菜",
        "點心",
        "湯",
        "飯食",
        "麵食",
        "粥品",
        "小吃",
        "飲料",
        "冰品",
        "醬料",
        "配料",
        "蛋糕",
        "西點",
        "餅乾",
        "麵包",
        "西式點心",
        "中式點心",
        "其他點心",
    },
    "diet": {
        "葷食",
        "素食",
    },
    "ingredient_group": {
        "牛肉",
        "豬肉",
        "雞肉",
        "魚肉",
        "羊肉",
        "鴨肉",
        "蛋",
        "甲殼類",
        "貝類",
        "頭足類及軟體族類",
        "豆腐",
        "豆干",
        "豆包",
        "腐皮類製品",
        "根莖類",
        "葉菜類",
        "瓜果類",
        "水果類",
        "海菜菇蕈類",
        "種子核果類",
        "花菜花瓣類",
        "米食及米類製品",
        "麵食及麵粉類製品",
    },
    "theme": {
        "火鍋",
        "地方小吃",
        "乾貨",
        "廣式點心",
    },
}

CATEGORY_ALIASES = {
    "台式": "中式料理",
    "台式料理": "中式料理",
    "台灣料理": "中式料理",
    "台灣菜": "中式料理",
    "台菜": "中式料理",
    "中式": "中式料理",
    "西式": "西式料理",
    "日式": "日式料理",
    "日本料理": "日式料理",
    "韓式": "韓式料理",
    "韓國料理": "韓式料理",
    "南洋": "南洋料理",
    "前菜": "開胃前菜",
    "開胃菜": "開胃前菜",
    "湯品": "湯",
    "湯類": "湯",
    "飯": "飯食",
    "飯類": "飯食",
    "麵": "麵食",
    "麵類": "麵食",
    "粥": "粥品",
    "飲品": "飲料",
}


def split_keywords(raw_keywords: str) -> list[str]:
    seen = set()
    result = []

    for part in KEYWORD_SPLIT_RE.split(str(raw_keywords or "")):
        token = re.sub(r"\s+", " ", part).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)

    return result


def category_lookup() -> dict[str, str]:
    return {
        name: category_type
        for category_type, names in CATEGORY_GROUPS.items()
        for name in names
    }


def ensure_normalized_schema(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          category_name VARCHAR(100) NOT NULL UNIQUE,
          category_type VARCHAR(50) NOT NULL,
          is_active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
          INDEX idx_categories_type_name (
            category_type,
            category_name
          )
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS category_aliases (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          category_id BIGINT NOT NULL,
          alias_name VARCHAR(100) NOT NULL UNIQUE,
          priority INT NOT NULL DEFAULT 100,
          is_active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          INDEX idx_category_alias_name (alias_name),
          CONSTRAINT fk_category_alias_category
            FOREIGN KEY (category_id)
            REFERENCES categories(id)
            ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'recipe_categories'
          AND column_name = 'category_id'
        """
    )
    has_new_schema = cur.fetchone()["n"] > 0

    if not has_new_schema:
        cur.execute("DROP TABLE IF EXISTS recipe_categories")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recipe_categories (
          recipe_id BIGINT NOT NULL,
          category_id BIGINT NOT NULL,
          source_keyword VARCHAR(100) NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (recipe_id, category_id),
          INDEX idx_recipe_category_category (
            category_id,
            recipe_id
          ),
          CONSTRAINT fk_rc_recipe
            FOREIGN KEY (recipe_id)
            REFERENCES recipes(id)
            ON DELETE CASCADE,
          CONSTRAINT fk_rc_category
            FOREIGN KEY (category_id)
            REFERENCES categories(id)
            ON DELETE CASCADE
        )
        """
    )


def seed_categories_and_aliases(cur) -> dict[str, int]:
    lookup = category_lookup()

    for category_name, category_type in lookup.items():
        cur.execute(
            """
            INSERT INTO categories(
                category_name,
                category_type,
                is_active
            )
            VALUES(%s,%s,TRUE)
            ON DUPLICATE KEY UPDATE
                category_type=VALUES(category_type),
                is_active=TRUE
            """,
            (category_name, category_type),
        )

    cur.execute(
        """
        SELECT id, category_name
        FROM categories
        WHERE is_active = TRUE
        """
    )
    category_ids = {
        row["category_name"]: row["id"]
        for row in cur.fetchall()
    }

    aliases = {
        category_name: category_name
        for category_name in lookup
    }
    aliases.update(CATEGORY_ALIASES)

    for alias_name, category_name in aliases.items():
        category_id = category_ids.get(category_name)
        if not category_id:
            raise RuntimeError(
                f"Alias target missing: "
                f"{alias_name} -> {category_name}"
            )

        cur.execute(
            """
            INSERT INTO category_aliases(
                category_id,
                alias_name,
                priority,
                is_active
            )
            VALUES(%s,%s,100,TRUE)
            ON DUPLICATE KEY UPDATE
                category_id=VALUES(category_id),
                is_active=TRUE
            """,
            (category_id, alias_name),
        )

    return category_ids


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    type_counter = Counter()
    category_counter = Counter()
    inserted_links = 0

    with get_connection() as conn, conn.cursor() as cur:
        ensure_normalized_schema(cur)
        category_ids = seed_categories_and_aliases(cur)

        cur.execute("DELETE FROM recipe_categories")

        cur.execute(
            """
            SELECT id, seq, raw_keywords
            FROM recipes
            ORDER BY id
            """
        )
        recipes = cur.fetchall()

        lookup = category_lookup()

        for recipe in recipes:
            for keyword in split_keywords(recipe.get("raw_keywords")):
                category_type = lookup.get(keyword)
                if not category_type:
                    continue

                category_id = category_ids[keyword]

                cur.execute(
                    """
                    INSERT IGNORE INTO recipe_categories(
                        recipe_id,
                        category_id,
                        source_keyword
                    )
                    VALUES(%s,%s,%s)
                    """,
                    (
                        recipe["id"],
                        category_id,
                        keyword,
                    ),
                )

                if cur.rowcount:
                    inserted_links += 1
                    type_counter[category_type] += 1
                    category_counter[keyword] += 1

        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM categories
            WHERE is_active = TRUE
            """
        )
        category_count = cur.fetchone()["n"]

        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM category_aliases
            WHERE is_active = TRUE
            """
        )
        alias_count = cur.fetchone()["n"]

        conn.commit()

    REPORT.write_text(
        json.dumps(
            {
                "recipe_count": len(recipes),
                "category_count": category_count,
                "alias_count": alias_count,
                "recipe_category_links": inserted_links,
                "category_type_counts": dict(
                    type_counter.most_common()
                ),
                "category_name_counts": dict(
                    category_counter.most_common()
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"recipes={len(recipes)}")
    print(f"categories={category_count}")
    print(f"category aliases={alias_count}")
    print(f"recipe-category links={inserted_links}")
    print(f"report={REPORT}")


if __name__ == "__main__":
    main()
