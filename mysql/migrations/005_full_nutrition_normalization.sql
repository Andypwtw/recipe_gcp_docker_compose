USE recipe_ai;

CREATE TABLE IF NOT EXISTS nutrient_definitions (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  nutrient_name VARCHAR(255) NOT NULL,
  unit VARCHAR(50),
  source_column_name VARCHAR(255) NOT NULL,
  nutrient_group VARCHAR(100),
  display_order INT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_nutrient_source_column (source_column_name)
);

CREATE TABLE IF NOT EXISTS nutrition_values (
  nutrition_source_id BIGINT NOT NULL,
  nutrient_id BIGINT NOT NULL,
  value_numeric DECIMAL(24,10),
  value_text VARCHAR(255),
  PRIMARY KEY (nutrition_source_id, nutrient_id),
  CONSTRAINT fk_nv_source
    FOREIGN KEY (nutrition_source_id) REFERENCES nutrition_source(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_nv_definition
    FOREIGN KEY (nutrient_id) REFERENCES nutrient_definitions(id)
    ON DELETE CASCADE
);

ALTER TABLE nutrition_source
  ADD COLUMN IF NOT EXISTS food_category VARCHAR(255) NULL AFTER food_code,
  ADD COLUMN IF NOT EXISTS content_description TEXT NULL AFTER food_name,
  ADD COLUMN IF NOT EXISTS common_names TEXT NULL AFTER content_description,
  ADD COLUMN IF NOT EXISTS waste_percent DECIMAL(18,6) NULL AFTER common_names,
  ADD COLUMN IF NOT EXISTS raw_data JSON NULL AFTER waste_percent;

ALTER TABLE recipe_nutrition_summary
  ADD COLUMN IF NOT EXISTS energy_kcal DECIMAL(18,6) NULL,
  ADD COLUMN IF NOT EXISTS coverage_percent DECIMAL(8,2) NULL,
  ADD COLUMN IF NOT EXISTS calculated_at TIMESTAMP NULL;
