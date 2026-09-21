from __future__ import annotations

import unittest

from app.services.recipe_calculation import (
    calculate_recipe,
    ingredient_price,
    is_main_coverage_line,
    price_display,
    should_include_seasoning_calorie,
)


def line(
    *,
    weight=100,
    kcal100=100,
    price100=None,
    category="FOOD",
    is_seasoning=False,
):
    return {
        "weight_g": weight,
        "energy_kcal_per_100g": kcal100,
        "price_per_100g": price100,
        "ingredient_category": category,
        "is_seasoning": is_seasoning,
        "fallback_kcal_per_100g": None,
    }


class RecipeCalculationPolicyTests(unittest.TestCase):
    def test_main_missing_exactly_20_percent_is_allowed(self):
        rows = [line(weight=100, kcal100=100) for _ in range(4)]
        rows.append(line(weight=100, kcal100=None))
        result = calculate_recipe(rows)
        self.assertEqual(result.main_missing_ratio_percent, 20.0)
        self.assertEqual(result.ingredient_energy_kcal, 400)
        self.assertEqual(result.energy_kcal, 400)

    def test_main_missing_over_20_and_known_kcal_not_over_500_is_null(self):
        rows = [
            line(weight=100, kcal100=200),
            line(weight=100, kcal100=150),
            line(weight=100, kcal100=100),
            line(weight=100, kcal100=None),
        ]
        result = calculate_recipe(rows)
        self.assertEqual(result.main_missing_ratio_percent, 25.0)
        self.assertEqual(result.ingredient_energy_kcal, None)
        self.assertEqual(result.energy_kcal, None)


    def test_main_missing_over_20_and_known_kcal_exactly_500_is_null(self):
        rows = [
            line(weight=100, kcal100=300),
            line(weight=100, kcal100=150),
            line(weight=100, kcal100=50),
            line(weight=100, kcal100=None),
        ]
        result = calculate_recipe(rows)
        self.assertEqual(result.main_missing_ratio_percent, 25.0)
        self.assertIsNone(result.ingredient_energy_kcal)
        self.assertIsNone(result.energy_kcal)

    def test_main_missing_over_20_but_known_kcal_over_500_is_kept(self):
        rows = [
            line(weight=100, kcal100=300),
            line(weight=100, kcal100=250),
            line(weight=100, kcal100=50),
            line(weight=100, kcal100=None),
        ]
        result = calculate_recipe(rows)
        self.assertEqual(result.main_missing_ratio_percent, 25.0)
        self.assertEqual(result.ingredient_energy_kcal, 600)
        self.assertEqual(result.energy_kcal, 600)

    def test_aromatic_79g_excluded_from_main_coverage_80g_included(self):
        aromatic_79 = line(weight=79, kcal100=40, category="AROMATIC_SEASONING", is_seasoning=True)
        aromatic_80 = line(weight=80, kcal100=40, category="AROMATIC_SEASONING", is_seasoning=True)
        self.assertFalse(is_main_coverage_line(aromatic_79))
        self.assertTrue(is_main_coverage_line(aromatic_80))

    def test_ordinary_seasoning_50g_boundary(self):
        low = line(weight=49, kcal100=60, category="LIQUID_SEASONING", is_seasoning=True)
        boundary = line(weight=50, kcal100=60, category="LIQUID_SEASONING", is_seasoning=True)
        self.assertFalse(should_include_seasoning_calorie(low))
        self.assertTrue(should_include_seasoning_calorie(boundary))

    def test_high_energy_seasoning_20g_boundary(self):
        low = line(weight=19, kcal100=900, category="FAT_SEASONING", is_seasoning=True)
        boundary = line(weight=20, kcal100=900, category="FAT_SEASONING", is_seasoning=True)
        self.assertFalse(should_include_seasoning_calorie(low))
        self.assertTrue(should_include_seasoning_calorie(boundary))

    def test_main_null_forces_total_null_even_when_seasoning_is_known(self):
        rows = [
            line(weight=100, kcal100=200),
            line(weight=100, kcal100=None),
            line(weight=50, kcal100=60, category="LIQUID_SEASONING", is_seasoning=True),
        ]
        result = calculate_recipe(rows)
        self.assertIsNone(result.ingredient_energy_kcal)
        self.assertEqual(result.seasoning_energy_kcal, 30)
        self.assertIsNone(result.energy_kcal)

    def test_missing_seasoning_does_not_null_valid_main_total(self):
        rows = [
            line(weight=200, kcal100=200),
            line(weight=None, kcal100=60, category="LIQUID_SEASONING", is_seasoning=True),
        ]
        result = calculate_recipe(rows)
        self.assertEqual(result.ingredient_energy_kcal, 400)
        self.assertIsNone(result.seasoning_energy_kcal)
        self.assertEqual(result.energy_kcal, 400)

    def test_price_is_ceiled_per_item_then_summed(self):
        rows = [
            line(weight=100, price100=12.01),
            line(weight=100, price100=2.30),
            line(weight=None, price100=10),
        ]
        result = calculate_recipe(rows)
        self.assertEqual(ingredient_price(100, 12.01), 13)
        self.assertEqual(ingredient_price(100, 2.30), 3)
        self.assertEqual(price_display(None), "$無資料")
        self.assertEqual(result.estimated_price, 16)
        self.assertEqual(result.price_status, "PARTIAL")

    def test_all_missing_prices_returns_null_total(self):
        rows = [line(weight=None, price100=10), line(weight=50, price100=None)]
        result = calculate_recipe(rows)
        self.assertIsNone(result.estimated_price)
        self.assertEqual(result.price_status, "INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
