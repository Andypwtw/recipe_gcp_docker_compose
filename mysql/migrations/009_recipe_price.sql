USE recipe_ai;

-- food_nutrition_2025.xlsx 的「每100g的價格」
-- 單位固定為新台幣 / 100g。
ALTER TABLE nutrition_source
  ADD COLUMN IF NOT EXISTS price_per_100g DECIMAL(18,4) NULL
  AFTER waste_percent;

-- 每道食譜的估算價格與價格覆蓋率。
ALTER TABLE recipe_nutrition_summary
  ADD COLUMN IF NOT EXISTS estimated_price DECIMAL(18,2) NULL
  AFTER energy_kcal,
  ADD COLUMN IF NOT EXISTS price_coverage_percent DECIMAL(8,2) NULL
  AFTER coverage_percent,
  ADD COLUMN IF NOT EXISTS price_status VARCHAR(30)
  NOT NULL DEFAULT 'INSUFFICIENT'
  AFTER price_coverage_percent;
