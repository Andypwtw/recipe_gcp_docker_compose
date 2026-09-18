USE recipe_ai;

CREATE TABLE IF NOT EXISTS qualitative_amount_rules (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ingredient_id BIGINT NOT NULL,
  ingredient_alias VARCHAR(255) NOT NULL,
  qualitative_term VARCHAR(50) NOT NULL,
  canonical_rule_name VARCHAR(255),
  conversion_type VARCHAR(50) NOT NULL DEFAULT 'ESTIMATED_HIGH_CONFIDENCE',
  default_grams DECIMAL(18,6) NOT NULL,
  min_grams DECIMAL(18,6),
  max_grams DECIMAL(18,6),
  confidence_score DECIMAL(6,2) NOT NULL,
  auto_convert BOOLEAN NOT NULL DEFAULT FALSE,
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
  source_url_1 TEXT,
  source_url_2 TEXT,
  note VARCHAR(1000),
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_qar_alias_term (ingredient_alias, qualitative_term),
  INDEX idx_qar_ingredient_term (ingredient_id, qualitative_term, status),
  CONSTRAINT fk_qar_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
    ON DELETE CASCADE
);

-- MySQL 8.x-safe idempotent column migration.
-- Use information_schema + prepared ALTER so the migration stays idempotent
-- across the MySQL 8.x deployments used by this project.
SET @weight_coverage_column_exists = (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'recipe_nutrition_summary'
    AND column_name = 'weight_coverage_percent'
);

SET @weight_coverage_sql = IF(
  @weight_coverage_column_exists = 0,
  'ALTER TABLE recipe_nutrition_summary ADD COLUMN weight_coverage_percent DECIMAL(8,2) NULL AFTER price_coverage_percent',
  'SELECT 1'
);

PREPARE stmt_weight_coverage FROM @weight_coverage_sql;
EXECUTE stmt_weight_coverage;
DEALLOCATE PREPARE stmt_weight_coverage;
