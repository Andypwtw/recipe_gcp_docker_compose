from __future__ import annotations

"""Ingredient-intent rules for recommendation endpoints.

Recipe categories are useful metadata, but a request such as "我想吃雞肉"
should also require an actual chicken ingredient.  This module translates
common ingredient-group intents into canonical ingredient-name LIKE patterns.
"""

INGREDIENT_GROUP_PATTERNS: dict[str, tuple[str, ...]] = {
    "雞肉": (
        "%雞肉%",
        "%雞胸%",
        "%雞腿%",
        "%雞翅%",
        "%雞柳%",
        "%雞丁%",
        "%雞絞肉%",
        "%雞里肌%",
        "%全雞%",
        "%土雞%",
        "%烏骨雞%",
        "%春雞%",
        "%老母雞%",
        "%肉雞%",
    ),
    "牛肉": (
        "%牛肉%",
        "%牛腩%",
        "%牛腱%",
        "%牛排%",
        "%牛絞肉%",
        "%牛小排%",
        "%牛肋%",
    ),
    "豬肉": (
        "%豬肉%",
        "%豬里肌%",
        "%豬五花%",
        "%豬絞肉%",
        "%豬腳%",
        "%豬肋%",
        "%排骨%",
    ),
    "羊肉": (
        "%羊肉%",
        "%羊排%",
        "%羊腿%",
    ),
    "鴨肉": (
        "%鴨肉%",
        "%鴨胸%",
        "%鴨腿%",
        "%全鴨%",
    ),
}

INGREDIENT_GROUP_EXCLUDES: dict[str, tuple[str, ...]] = {
    "雞肉": (
        "%雞蛋%",
        "%雞粉%",
        "%雞精%",
        "%雞晶%",
        "%雞湯%",
        "%雞高湯%",
        "%雞骨%",
        "%雞油%",
        "%雞醬%",
        "%雞麵%",
        "%雞蛋豆腐%",
        "%素雞%",
        "%田雞%",
    ),
}


def patterns_for_group(group_name: str) -> tuple[str, ...]:
    return INGREDIENT_GROUP_PATTERNS.get(str(group_name or "").strip(), ())


def excludes_for_group(group_name: str) -> tuple[str, ...]:
    return INGREDIENT_GROUP_EXCLUDES.get(str(group_name or "").strip(), ())


def detect_ingredient_groups(text: str) -> list[str]:
    """Detect supported ingredient-group names directly from natural language.

    This is intentionally small and deterministic.  It gives the API a fallback
    even if category_aliases has not been populated yet.
    """
    query = str(text or "").strip()
    if not query:
        return []

    return [group for group in INGREDIENT_GROUP_PATTERNS if group in query]
