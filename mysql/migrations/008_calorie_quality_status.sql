USE recipe_ai;

ALTER TABLE recipe_nutrition_summary
  ADD COLUMN IF NOT EXISTS calorie_status VARCHAR(30)
  NOT NULL DEFAULT 'INSUFFICIENT'
  AFTER coverage_percent;
