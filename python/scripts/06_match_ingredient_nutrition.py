from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.db import get_connection
from app.services.normalization import nutrition_match_base, nutrition_match_variants

AUTO_THRESHOLD = 0.92
REVIEW_THRESHOLD = 0.72
TOP_N = 3
COMMON_NAME_SPLIT = re.compile(r"[,，、;/；\n]+")

# Only aliases with a clear representative in the Taiwan nutrition workbook.
CURATED_SAFE_TARGETS = {
    "蛋": "雞蛋平均值",
    "全蛋": "雞蛋平均值",
    "蛋液": "雞蛋平均值",
    "蛋白": "雞蛋白平均值",
    "蛋黃": "雞蛋黃平均值",
    "鮮奶": "全脂鮮乳平均值",
    "無鹽奶油": "奶油(固態,不加鹽)",
    "有鹽奶油": "奶油(固態,加鹽)",
    "洋蔥": "黃洋蔥",
    "番茄": "大番茄平均值(紅色系)",
    "小番茄": "小番茄平均值(紅色系)",
    "雞胸": "去皮清肉(肉雞)",
    "雞胸肉": "去皮清肉(肉雞)",
    # Composition proxies with close macronutrient profiles. These are mainly
    # high-frequency seasonings; their recipe-scale contribution is usually small.
    "糖": "方糖",
    "砂糖": "方糖",
    "細砂糖": "方糖",
    "細糖": "方糖",
    "白砂糖": "方糖",
    "胡椒粉": "白胡椒粉",
    "白胡椒": "白胡椒粉",
    "黑胡椒": "黑胡椒粉",
    "麻油": "黑芝麻油",
    "鮮香菇": "香菇平均值",
    "動物性鮮奶油": "奶油(液態)",
    "西芹": "西洋芹菜",
}

ANIMAL_GROUPS = {
    "雞": ("雞",),
    "豬": ("豬", "五花", "里肌", "梅花", "排骨", "三層肉"),
    "牛": ("牛",),
    "羊": ("羊",),
    "魚": ("魚", "鱸", "鯛", "鯧", "鰆", "鱈", "鮭", "鯖", "鮪", "鰻"),
    "蝦": ("蝦",),
    "蟹": ("蟹",),
}


def split_common_names(text: str) -> list[str]:
    return [p.strip() for p in COMMON_NAME_SPLIT.split(str(text or "")) if p.strip()]


def sim(a: str, b: str) -> float:
    aa = nutrition_match_base(a)
    bb = nutrition_match_base(b)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def animal_categories(name: str) -> set[str]:
    n = nutrition_match_base(name)
    return {cat for cat, keys in ANIMAL_GROUPS.items() if any(k in n for k in keys)}


def category_conflict(ingredient_name: str, candidate_name: str) -> bool:
    ing = animal_categories(ingredient_name)
    cand = animal_categories(candidate_name)
    if ing and cand and not (ing & cand):
        return True
    if not ing and cand:
        return True
    return False


def representative_rank(food: dict) -> tuple:
    name = str(food.get("food_name") or "")
    category = str(food.get("food_category") or "")
    # Prefer workbook averages, then ordinary raw-food categories, then shorter names.
    average_penalty = 0 if "平均值" in name else 1
    processed_penalty = 1 if any(x in category for x in ("加工調理", "糕餅", "飲料")) else 0
    return (average_penalty, processed_penalty, len(name), int(food["id"]))


def choose_representative(foods: list[dict]) -> dict | None:
    return min(foods, key=representative_rank) if foods else None


def approve(cur, ingredient_id, nutrition_id, method, score):
    cur.execute(
        """
        INSERT INTO ingredient_nutrition_map
        (ingredient_id,nutrition_source_id,match_method,match_score,status)
        VALUES(%s,%s,%s,%s,'APPROVED')
        ON DUPLICATE KEY UPDATE
          nutrition_source_id=VALUES(nutrition_source_id),
          match_method=VALUES(match_method),
          match_score=VALUES(match_score),
          status='APPROVED'
        """,
        (ingredient_id, nutrition_id, method, score),
    )


def main():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, canonical_name FROM ingredients ORDER BY id")
        ingredients = cur.fetchall()

        cur.execute(
            """
            SELECT id, food_category, food_name, common_names, price_per_100g
            FROM nutrition_source
            """
        )
        foods = cur.fetchall()

        food_name_exact: dict[str, dict] = {}
        common_name_exact: dict[str, list[dict]] = {}
        variant_index: dict[str, list[dict]] = {}

        for food in foods:
            food_name = str(food["food_name"] or "").strip()
            if food_name:
                food_name_exact.setdefault(food_name, food)
                for v in nutrition_match_variants(food_name):
                    variant_index.setdefault(v, []).append(food)

            for alias in split_common_names(food.get("common_names")):
                common_name_exact.setdefault(alias, []).append(food)
                for v in nutrition_match_variants(alias):
                    variant_index.setdefault(v, []).append(food)

        cur.execute("DELETE FROM manual_review WHERE status='PENDING'")

        stats = {
            "food_name_exact": 0,
            "common_name_exact": 0,
            "variant_exact": 0,
            "curated_safe_target": 0,
            "fuzzy_approved": 0,
            "review_candidates": 0,
            "unmatched": 0,
        }

        for idx, ing in enumerate(ingredients, 1):
            name = str(ing["canonical_name"] or "").strip()

            food = food_name_exact.get(name)
            if food:
                approve(cur, ing["id"], food["id"], "food_name_exact", 1.0)
                stats["food_name_exact"] += 1
                continue

            common_candidates = common_name_exact.get(name) or []
            food = choose_representative(common_candidates)
            if food:
                approve(cur, ing["id"], food["id"], "common_name_exact", 1.0)
                stats["common_name_exact"] += 1
                continue

            # Strong exact matching after removing sample-year/average/parenthetical
            # descriptors and applying only conservative aliases.
            exact_candidates = []
            seen_ids = set()
            for v in nutrition_match_variants(name):
                for candidate in variant_index.get(v, []):
                    if candidate["id"] not in seen_ids:
                        exact_candidates.append(candidate)
                        seen_ids.add(candidate["id"])
            exact_candidates = [
                f for f in exact_candidates
                if not category_conflict(name, f["food_name"])
            ]
            food = choose_representative(exact_candidates)
            if food:
                approve(cur, ing["id"], food["id"], "normalized_variant_exact", 0.99)
                stats["variant_exact"] += 1
                continue

            target = CURATED_SAFE_TARGETS.get(name)
            food = food_name_exact.get(target) if target else None
            if food:
                approve(cur, ing["id"], food["id"], "curated_safe_target", 0.98)
                stats["curated_safe_target"] += 1
                continue

            scored = []
            ing_variants = nutrition_match_variants(name) or {nutrition_match_base(name)}
            for food in foods:
                if category_conflict(name, food["food_name"]):
                    continue
                candidate_names = [food["food_name"], *split_common_names(food.get("common_names"))]
                score = 0.0
                for iv in ing_variants:
                    for candidate_name in candidate_names:
                        score = max(score, sim(iv, candidate_name))
                scored.append((score, food))

            scored.sort(key=lambda x: (x[0], -representative_rank(x[1])[0]), reverse=True)
            scored = scored[:TOP_N]

            if scored and scored[0][0] >= AUTO_THRESHOLD:
                score, food = scored[0]
                approve(cur, ing["id"], food["id"], "fuzzy_guarded", score)
                stats["fuzzy_approved"] += 1
            else:
                candidate_count = 0
                for score, food in scored:
                    if score < REVIEW_THRESHOLD:
                        continue
                    cur.execute(
                        """
                        INSERT INTO manual_review
                        (ingredient_id,candidate_nutrition_id,candidate_name,score,status)
                        VALUES(%s,%s,%s,%s,'PENDING')
                        """,
                        (ing["id"], food["id"], food["food_name"], score),
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
