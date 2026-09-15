from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.db import get_connection


AUTO_THRESHOLD = 0.92
REVIEW_THRESHOLD = 0.72
TOP_N = 3

COMMON_NAME_SPLIT = re.compile(r"[,，、;/；\n]+")

SOFT_SUFFIXES = [
    "厚片", "薄片", "切片", "切絲", "切丁",
    "丁", "塊", "條", "片", "絲", "末", "碎",
    "粒", "葉", "梗", "蒂", "圈", "段", "泥",
    "茸", "蓉",
]

INGREDIENT_MATCH_ALIASES = {
    # 常見來源名稱與食品資料庫名稱差異。
    # 只在原名稱 exact/normalized exact 都失敗後使用。
    "蛋": "雞蛋",
    "乾雞絲麵": "雞絲麵",
}


def normalize_name(value: str) -> str:
    s = unicodedata.normalize("NFKC", str(value or "")).strip()
    s = s.replace("蕃", "番")
    s = re.sub(r"20\d{2}年取樣", "", s)
    s = re.sub(r"平均值", "", s)
    s = re.sub(r"[()（）\[\]【】,，、\s_\-\.]+", "", s)

    changed = True
    while changed:
        changed = False
        for suffix in SOFT_SUFFIXES:
            if len(s) > len(suffix) + 1 and s.endswith(suffix):
                s = s[:-len(suffix)]
                changed = True
                break

    return s


def split_common_names(text: str) -> list[str]:
    return [
        part.strip()
        for part in COMMON_NAME_SPLIT.split(str(text or ""))
        if part.strip()
    ]


def sim(a: str, b: str) -> float:
    return SequenceMatcher(
        None,
        normalize_name(a),
        normalize_name(b),
    ).ratio()


def approve(cur, ingredient_id, nutrition_id, method, score):
    cur.execute(
        """
        INSERT INTO ingredient_nutrition_map
        (
            ingredient_id,
            nutrition_source_id,
            match_method,
            match_score,
            status
        )
        VALUES(%s,%s,%s,%s,'APPROVED')
        ON DUPLICATE KEY UPDATE
            nutrition_source_id=VALUES(nutrition_source_id),
            match_method=VALUES(match_method),
            match_score=VALUES(match_score),
            status='APPROVED'
        """,
        (
            ingredient_id,
            nutrition_id,
            method,
            score,
        )
    )


def main():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, canonical_name FROM ingredients ORDER BY id"
        )
        ingredients = cur.fetchall()

        cur.execute(
            """
            SELECT
                id,
                food_name,
                common_names
            FROM nutrition_source
            """
        )
        foods = cur.fetchall()

        # Exact indexes.
        food_name_index = {}
        common_name_index = {}
        normalized_index = {}

        for food in foods:
            food_name = str(food["food_name"] or "").strip()

            if food_name:
                food_name_index.setdefault(
                    food_name,
                    food,
                )

                normalized_index.setdefault(
                    normalize_name(food_name),
                    food,
                )

            for alias in split_common_names(food.get("common_names")):
                common_name_index.setdefault(
                    alias,
                    food,
                )
                normalized_index.setdefault(
                    normalize_name(alias),
                    food,
                )

        cur.execute(
            "DELETE FROM manual_review WHERE status='PENDING'"
        )

        stats = {
            "food_name_exact": 0,
            "common_name_exact": 0,
            "normalized_exact": 0,
            "fuzzy_approved": 0,
            "review_candidates": 0,
            "unmatched": 0,
        }

        for idx, ing in enumerate(ingredients, 1):
            name = str(ing["canonical_name"] or "").strip()

            # 1. Official food_name exact.
            food = food_name_index.get(name)
            if food:
                approve(
                    cur,
                    ing["id"],
                    food["id"],
                    "food_name_exact",
                    1.0,
                )
                stats["food_name_exact"] += 1
                continue

            # 2. Excel common_names exact.
            food = common_name_index.get(name)
            if food:
                approve(
                    cur,
                    ing["id"],
                    food["id"],
                    "common_name_exact",
                    1.0,
                )
                stats["common_name_exact"] += 1
                continue

            # 3. Normalized exact (official name or alias).
            normalized = normalize_name(name)
            food = normalized_index.get(normalized)
            if normalized and food:
                approve(
                    cur,
                    ing["id"],
                    food["id"],
                    "normalized_exact",
                    0.99,
                )
                stats["normalized_exact"] += 1
                continue

            # 4. Curated safe alias exact.
            alias_target = INGREDIENT_MATCH_ALIASES.get(name)
            if alias_target:
                food = (
                    food_name_index.get(alias_target)
                    or common_name_index.get(alias_target)
                    or normalized_index.get(normalize_name(alias_target))
                )
                if food:
                    approve(
                        cur,
                        ing["id"],
                        food["id"],
                        "curated_alias_exact",
                        0.98,
                    )
                    stats.setdefault("curated_alias_exact", 0)
                    stats["curated_alias_exact"] += 1
                    continue

            # 5. Fuzzy only after exact/alias paths failed.
            scored = sorted(
                (
                    (
                        max(
                            [sim(name, food["food_name"])]
                            + [
                                sim(name, alias)
                                for alias in split_common_names(
                                    food.get("common_names")
                                )
                            ]
                        ),
                        food,
                    )
                    for food in foods
                ),
                key=lambda x: x[0],
                reverse=True,
            )[:TOP_N]

            if scored and scored[0][0] >= AUTO_THRESHOLD:
                score, food = scored[0]
                approve(
                    cur,
                    ing["id"],
                    food["id"],
                    "fuzzy_after_alias",
                    score,
                )
                stats["fuzzy_approved"] += 1
            else:
                candidate_count = 0

                for score, food in scored:
                    if score < REVIEW_THRESHOLD:
                        continue

                    cur.execute(
                        """
                        INSERT INTO manual_review
                        (
                            ingredient_id,
                            candidate_nutrition_id,
                            candidate_name,
                            score,
                            status
                        )
                        VALUES(%s,%s,%s,%s,'PENDING')
                        """,
                        (
                            ing["id"],
                            food["id"],
                            food["food_name"],
                            score,
                        )
                    )

                    candidate_count += 1
                    stats["review_candidates"] += 1

                if candidate_count == 0:
                    stats["unmatched"] += 1

            if idx % 100 == 0:
                conn.commit()
                print(f"matched {idx}/{len(ingredients)}")

        conn.commit()

    print("ingredient nutrition matching finished")
    print(stats)


if __name__ == "__main__":
    main()
