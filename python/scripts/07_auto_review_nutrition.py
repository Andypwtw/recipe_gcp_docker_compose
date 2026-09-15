from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from app.db import get_connection

AUDIT_OUT = Path("/workspace/data/processed/auto_review_result.json")

# 同義詞：沿用先前人工協助審核時採用的常見食材對應。
SYNONYM_GROUPS = [
    {"番茄", "蕃茄"},
    {"紅蘿蔔", "胡蘿蔔"},
    {"鮮奶", "鮮乳", "牛奶"},
    {"凱薩", "凱撒", "凱撒醬", "凱薩醬", "凱撒沙拉醬", "凱薩沙拉醬"},
    {"豆干", "豆乾"},
    {"再來米", "在來米", "秈米"},
    {"九層塔", "羅勒"},
    {"丁香魚", "日本銀帶鯡", "丁香魚脯"},
    {"海帶芽", "裙帶菜"},
    {"白木耳", "銀耳"},
    {"大黃瓜", "胡瓜"},
    {"喜相逢", "毛鱗魚"},
    {"尼羅紅魚", "紅色吳郭魚"},
    {"加州鱸魚", "大口黑鱸"},
    {"亞麻仁", "亞麻仁籽"},
    {"夏威夷豆", "原味夏威夷豆"},
    {"寬冬粉", "寬粉"},
    {"宜蘭蔥", "宜蘭粉蔥"},
    {"大骨湯", "豬大骨湯"},
    {"和風醬", "和風沙拉醬"},
    {"千島醬", "千島沙拉醬"},
]

CATEGORY_KEYWORDS = {
    "豬": ["豬", "五花", "里肌", "梅花", "排骨", "三層肉", "豬肉", "小排", "大排"],
    "牛": ["牛", "牛肉", "牛排", "牛腩", "牛五花", "牛腱"],
    "羊": ["羊", "羊肉", "羊排", "綿羊", "山羊"],
    "雞": ["雞", "雞肉", "雞胸", "雞腿", "雞翅", "土雞", "肉雞"],
    "魚": ["魚", "鱸", "鯛", "鯧", "鰆", "鱈", "吳郭", "鯡", "鮭", "鰻", "鯖", "鯉", "鯽", "鰹", "鱒", "鮪"],
    "蝦": ["蝦", "蝦仁", "草蝦", "對蝦", "櫻蝦"],
    "蟹": ["蟹", "蟹肉", "蟹腳"],
    "奶": ["奶", "牛奶", "鮮乳", "鮮奶", "奶粉", "乳酪", "起司", "鮮奶油"],
    "油": ["油", "沙拉油", "芝麻油", "香油", "橄欖油", "大豆油"],
    "醬": ["醬", "沙拉醬", "醬油", "辣醬", "咖哩", "千島", "凱撒", "凱薩"],
    "豆": ["豆", "豆腐", "豆干", "豆乾", "黃豆", "紅豆", "綠豆", "豆皮", "腐皮"],
    "米": ["米", "糯米", "糙米", "米粉", "秈米", "在來米"],
    "麵": ["麵", "麵粉", "麵條", "餛飩皮", "水餃皮"],
    "菜": ["菜", "白菜", "芥菜", "高麗菜", "萵苣", "菠菜", "芹菜", "九層塔", "蘆筍", "胡蘿蔔", "蘿蔔"],
    "瓜": ["瓜", "黃瓜", "胡瓜", "南瓜", "冬瓜", "苦瓜", "哈密瓜", "西瓜"],
    "果": ["果", "芒果", "奇異果", "芭樂", "李", "哈密瓜", "西瓜"],
    "椒": ["椒", "辣椒", "甜椒", "胡椒"],
    "菇": ["菇", "香菇", "木耳", "銀耳", "松茸"],
}

ANIMAL_CATEGORIES = {"豬", "牛", "羊", "雞", "魚", "蝦", "蟹"}

SOFT_SUFFIXES = [
    "厚片", "薄片", "丁", "塊", "條", "片", "絲", "末", "碎",
    "粒", "葉", "梗", "蒂", "圈", "段", "泥"
]


def normalize_name(value: str) -> str:
    s = unicodedata.normalize("NFKC", str(value or "")).strip()
    s = s.replace("蕃", "番")
    s = re.sub(r"20\d{2}年取樣", "", s)
    s = re.sub(r"平均值", "", s)
    s = re.sub(r"^[A-Za-z\.\s]+(?=[\u4e00-\u9fff])", "", s)
    s = re.sub(r"(?<=[\u4e00-\u9fff])[A-Za-z\d]+$", "", s)
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


def synonym_match(a: str, b: str) -> bool:
    na = normalize_name(a)
    nb = normalize_name(b)
    for group in SYNONYM_GROUPS:
        normalized_group = {normalize_name(x) for x in group}
        if na in normalized_group and nb in normalized_group:
            return True
    return False


def get_categories(name: str) -> set[str]:
    found = set()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in name for keyword in keywords):
            found.add(category)
    return found


def category_conflict(ingredient_name: str, candidate_name: str) -> bool:
    ing = get_categories(ingredient_name)
    cand = get_categories(candidate_name)

    ing_animals = ing & ANIMAL_CATEGORIES
    cand_animals = cand & ANIMAL_CATEGORIES

    # 動物種類互相衝突，例如雞 -> 魚。
    if ing_animals and cand_animals and not (ing_animals & cand_animals):
        return True

    # 原食材不是動物，候選卻明確是動物，例如花生 -> 豬肉、小黃瓜 -> 小黃魚。
    if not ing_animals and cand_animals:
        return True

    return False


def core_similarity(a: str, b: str) -> float:
    na = normalize_name(a)
    nb = normalize_name(b)

    if not na or not nb:
        return 0.0

    if na == nb:
        return 1.0

    if synonym_match(a, b):
        return 0.98

    if na in nb or nb in na:
        shorter = min(len(na), len(nb))
        longer = max(len(na), len(nb))
        return 0.85 + 0.15 * (shorter / longer)

    sa = set(na)
    sb = set(nb)
    return (len(sa & sb) / max(1, len(sa | sb))) * 0.8


def choose_group(rows: list[dict]) -> tuple[int | None, dict[int, str]]:
    """
    回傳：
      best_review_id: 應自動 APPROVED 的 review_id；若無可靠候選則 None
      notes: 每筆 review_id 的說明
    """
    ingredient_name = rows[0]["ingredient_name"]
    ingredient_categories = get_categories(ingredient_name)

    scored = []

    for row in rows:
        candidate_name = row["candidate_name"]
        existing_score = float(row["score"] or 0)
        lexical = core_similarity(ingredient_name, candidate_name)
        conflict = category_conflict(ingredient_name, candidate_name)

        candidate_categories = get_categories(candidate_name)
        category_bonus = 0.12 if (ingredient_categories & candidate_categories) else 0.0

        value = 0.55 * lexical + 0.45 * existing_score + category_bonus

        if synonym_match(ingredient_name, candidate_name):
            value += 0.20

        if conflict:
            value -= 0.70

        scored.append(
            {
                "value": value,
                "row": row,
                "lexical": lexical,
                "conflict": conflict,
                "existing_score": existing_score,
            }
        )

    scored.sort(key=lambda x: x["value"], reverse=True)
    best = scored[0]

    # 保守自動判斷：
    # 1. 不可有明顯類別衝突
    # 2. 名稱核心要夠接近；或原 fuzzy 分數高且名稱仍有一定關聯
    approve = (
        not best["conflict"]
        and (
            synonym_match(
                ingredient_name,
                best["row"]["candidate_name"],
            )
            or best["lexical"] >= 0.58
            or (
                best["existing_score"] >= 0.84
                and best["lexical"] >= 0.38
            )
        )
    )

    best_id = best["row"]["review_id"] if approve else None
    notes = {}

    for item in scored:
        row = item["row"]
        rid = row["review_id"]

        if best_id is not None and rid == best_id:
            notes[rid] = (
                "AUTO_APPROVED：同組候選中名稱核心最接近，"
                "且未發現明顯食材類別衝突。"
            )
        elif item["conflict"]:
            notes[rid] = (
                "AUTO_REJECTED：候選與原食材類別明顯不一致。"
            )
        elif best_id is not None:
            notes[rid] = (
                "AUTO_REJECTED：同一食材已有更合理候選。"
            )
        else:
            notes[rid] = (
                "AUTO_REJECTED：名稱不足以可靠確認一致，採保守拒絕。"
            )

    return best_id, notes


def main():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                mr.id AS review_id,
                mr.ingredient_id,
                i.canonical_name AS ingredient_name,
                mr.candidate_nutrition_id,
                mr.candidate_name,
                mr.score,
                mr.status
            FROM manual_review mr
            JOIN ingredients i
              ON i.id = mr.ingredient_id
            WHERE mr.status = 'PENDING'
            ORDER BY mr.ingredient_id, mr.score DESC, mr.id
            """
        )
        rows = cur.fetchall()

        groups: dict[int, list[dict]] = defaultdict(list)
        for row in rows:
            groups[row["ingredient_id"]].append(row)

        approved_count = 0
        rejected_count = 0
        audit_rows = []

        for ingredient_id, group in groups.items():
            best_review_id, notes = choose_group(group)

            for row in group:
                decision = (
                    "APPROVED"
                    if row["review_id"] == best_review_id
                    else "REJECTED"
                )

                if decision == "APPROVED":
                    approved_count += 1
                else:
                    rejected_count += 1

                note = notes[row["review_id"]]

                cur.execute(
                    """
                    UPDATE manual_review
                    SET status = %s,
                        note = %s,
                        reviewed_at = NOW()
                    WHERE id = %s
                    """,
                    (decision, note, row["review_id"]),
                )

                audit_rows.append(
                    {
                        "review_id": row["review_id"],
                        "ingredient_id": row["ingredient_id"],
                        "ingredient_name": row["ingredient_name"],
                        "candidate_nutrition_id": row["candidate_nutrition_id"],
                        "candidate_name": row["candidate_name"],
                        "score": str(row["score"]),
                        "decision": decision,
                        "note": note,
                    }
                )

        conn.commit()

    AUDIT_OUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUT.write_text(
        json.dumps(audit_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Automatic nutrition review finished.")
    print(f"pending candidates reviewed = {len(rows)}")
    print(f"APPROVED = {approved_count}")
    print(f"REJECTED = {rejected_count}")
    print(f"audit file = {AUDIT_OUT}")


if __name__ == "__main__":
    main()
