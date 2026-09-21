USE recipe_ai;

CREATE TABLE IF NOT EXISTS ingredient_calculation_rules (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ingredient_id BIGINT NOT NULL,
  rule_key VARCHAR(100) NOT NULL,
  ingredient_category VARCHAR(60) NOT NULL DEFAULT 'FOOD',
  is_seasoning BOOLEAN NOT NULL DEFAULT FALSE,
  calorie_policy VARCHAR(20) NOT NULL DEFAULT 'INCLUDE',
  price_policy VARCHAR(20) NOT NULL DEFAULT 'INCLUDE',
  calorie_ignore_threshold_kcal DECIMAL(10,4) NOT NULL DEFAULT 5.0000,
  fallback_kcal_per_100g DECIMAL(12,4) NULL,
  confidence_score DECIMAL(6,2) NOT NULL DEFAULT 100.00,
  match_reason VARCHAR(255),
  source VARCHAR(255) NOT NULL DEFAULT 'ingredient_calculation_rules.json',
  note VARCHAR(1000),
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_icr_ingredient (ingredient_id),
  INDEX idx_icr_price_policy (price_policy,is_seasoning,status),
  INDEX idx_icr_calorie_policy (calorie_policy,status),
  CONSTRAINT fk_icr_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
    ON DELETE CASCADE
);

-- Price has a different line-coverage denominator from calories because all
-- recognized seasoning/condiment rows are excluded from price calculation.
SET @price_weight_line_column_exists = (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'recipe_nutrition_summary'
    AND column_name = 'price_weight_line_coverage_percent'
);

SET @price_weight_line_sql = IF(
  @price_weight_line_column_exists = 0,
  'ALTER TABLE recipe_nutrition_summary ADD COLUMN price_weight_line_coverage_percent DECIMAL(8,2) NULL AFTER weight_coverage_percent',
  'SELECT 1'
);

PREPARE stmt_price_weight_line FROM @price_weight_line_sql;
EXECUTE stmt_price_weight_line;
DEALLOCATE PREPARE stmt_price_weight_line;
