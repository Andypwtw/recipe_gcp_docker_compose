from __future__ import annotations

"""Ingredient intent rules used by recommendation endpoints.

The category table contains ingredient-group labels such as 雞肉/牛肉.  Those
labels are useful for navigation, but using recipe_categories alone can return
recipes that do not actually contain the requested ingredient.  This module
converts common ingredient-group intents into canonical-name SQL LIKE patterns
that are applied against recipe_ingredients -> ingredients.
"""

# Each value is one logical ingredient group. A recipe must match at least one
# pattern in the selected group. Multiple requested groups are combined with
# AND by recommendation.py (e.g. 雞肉 + 洋蔥 means both must be present).
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

# Exclusions prevent broad chicken patterns such as %土雞% from matching eggs,
# stock/powders, sauces, vegetarian mock foods, etc.
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
