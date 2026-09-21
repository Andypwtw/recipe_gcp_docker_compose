-- 2026-09-21 calorie/price policy update
-- Adds explicit calorie split and main-ingredient missing-ratio diagnostics.

ALTER TABLE recipe_nutrition_summary
  ADD COLUMN IF NOT EXISTS ingredient_energy_kcal DECIMAL(18,6) NULL AFTER energy_kcal,
  ADD COLUMN IF NOT EXISTS seasoning_energy_kcal DECIMAL(18,6) NULL AFTER ingredient_energy_kcal,
  ADD COLUMN IF NOT EXISTS main_missing_ratio_percent DECIMAL(8,2) NULL AFTER price_weight_line_coverage_percent;
