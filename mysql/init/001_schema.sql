CREATE DATABASE IF NOT EXISTS recipe_ai
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE recipe_ai;

CREATE TABLE IF NOT EXISTS recipes (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  seq VARCHAR(50) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  published_date DATE NULL,
  source_url VARCHAR(500),
  raw_keywords TEXT,
  steps LONGTEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keywords (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  keyword_name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS recipe_keywords (
  recipe_id BIGINT NOT NULL,
  keyword_id BIGINT NOT NULL,
  PRIMARY KEY (recipe_id, keyword_id),
  CONSTRAINT fk_rk_recipe
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_rk_keyword
    FOREIGN KEY (keyword_id) REFERENCES keywords(id)
    ON DELETE CASCADE
);




-- ============================================================
-- Hermes 正式分類主檔
-- ============================================================
CREATE TABLE IF NOT EXISTS categories (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  category_name VARCHAR(100) NOT NULL UNIQUE,
  category_type VARCHAR(50) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_categories_type_name (category_type, category_name)
);

-- ============================================================
-- 使用者自然語言分類同義詞
-- ============================================================
CREATE TABLE IF NOT EXISTS category_aliases (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  category_id BIGINT NOT NULL,
  alias_name VARCHAR(100) NOT NULL UNIQUE,
  priority INT NOT NULL DEFAULT 100,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_category_alias_name (alias_name),
  CONSTRAINT fk_category_alias_category
    FOREIGN KEY (category_id) REFERENCES categories(id)
    ON DELETE CASCADE
);

-- ============================================================
-- 食譜 × 正式分類 N:M 關聯
-- ============================================================
CREATE TABLE IF NOT EXISTS recipe_categories (
  recipe_id BIGINT NOT NULL,
  category_id BIGINT NOT NULL,
  source_keyword VARCHAR(100) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (recipe_id, category_id),
  INDEX idx_recipe_category_category (category_id, recipe_id),
  CONSTRAINT fk_rc_recipe
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_rc_category
    FOREIGN KEY (category_id) REFERENCES categories(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ingredients (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  canonical_name VARCHAR(255) NOT NULL UNIQUE,
  category VARCHAR(100),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingredient_aliases (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ingredient_id BIGINT NOT NULL,
  alias_name VARCHAR(255) NOT NULL UNIQUE,
  source VARCHAR(100),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_alias_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS units (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  canonical_unit VARCHAR(50) NOT NULL UNIQUE,
  unit_type VARCHAR(50) NOT NULL DEFAULT 'unknown',
  to_gram_factor DECIMAL(18,6),
  to_ml_factor DECIMAL(18,6),
  is_qualitative BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  recipe_id BIGINT NOT NULL,
  line_no INT NOT NULL,
  raw_text VARCHAR(1000),
  raw_name VARCHAR(255),
  ingredient_id BIGINT,
  raw_amount_text VARCHAR(255),
  quantity_min DECIMAL(18,6),
  quantity_max DECIMAL(18,6),
  quantity_value DECIMAL(18,6),
  unit_id BIGINT,
  weight_g DECIMAL(18,6),
  is_estimated BOOLEAN DEFAULT FALSE,
  needs_manual_review BOOLEAN DEFAULT FALSE,
  review_reason VARCHAR(500),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_recipe_line (recipe_id, line_no),
  CONSTRAINT fk_ri_recipe
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
    ON DELETE CASCADE,
  CONSTRAINT fk_ri_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id),
  CONSTRAINT fk_ri_unit
    FOREIGN KEY (unit_id) REFERENCES units(id)
);

CREATE TABLE IF NOT EXISTS ingredient_unit_weights (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ingredient_id BIGINT NOT NULL,
  unit_id BIGINT NOT NULL,
  grams_per_unit DECIMAL(18,6) NOT NULL,
  confidence VARCHAR(20),
  status VARCHAR(20) DEFAULT 'ACTIVE',
  source VARCHAR(255),
  note VARCHAR(500),
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_iuw (ingredient_id, unit_id),
  CONSTRAINT fk_iuw_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id),
  CONSTRAINT fk_iuw_unit
    FOREIGN KEY (unit_id) REFERENCES units(id)
);

CREATE TABLE IF NOT EXISTS ingredient_densities (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ingredient_id BIGINT NOT NULL UNIQUE,
  density_g_ml DECIMAL(18,6) NOT NULL,
  density_type VARCHAR(32) DEFAULT 'liquid',
  confidence VARCHAR(20),
  status VARCHAR(20) DEFAULT 'ACTIVE',
  source VARCHAR(255),
  note VARCHAR(500),
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_density_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
);


-- ============================================================
-- 高信心「少許」等定性用量換算規則
-- 僅 auto_convert=TRUE 且 confidence_score>=80 的規則
-- 才可自動寫入 recipe_ingredients.weight_g。
-- 「適量」預設不做固定克數換算。
-- ============================================================
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
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_qar_alias_term (ingredient_alias, qualitative_term),
  INDEX idx_qar_ingredient_term (ingredient_id, qualitative_term, status),
  CONSTRAINT fk_qar_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
    ON DELETE CASCADE
);

-- ============================================================
-- 食品營養資料：食品主檔
-- Excel 的食品識別/描述欄位放在這裡。
-- raw_data 另外保留整列 Excel 原始資料，確保來源資料無損保存。
-- ============================================================
CREATE TABLE IF NOT EXISTS nutrition_source (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  food_code VARCHAR(100) NOT NULL,
  food_category VARCHAR(255),
  food_name VARCHAR(255) NOT NULL,
  content_description TEXT,
  common_names TEXT,
  waste_percent DECIMAL(18,6),
  price_per_100g DECIMAL(18,4),
  raw_data JSON,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_nutrition_food_code (food_code),
  INDEX idx_nutrition_food_name (food_name)
);

-- ============================================================
-- 營養項目定義
-- 每一個 Excel 營養欄位只定義一次，例如：
-- 熱量(kcal)、粗蛋白(g)、鈉(mg)、維生素C(mg)...
-- ============================================================
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

-- ============================================================
-- 食品 × 營養項目 的數值
-- 所有 Excel 營養欄位都寫入此表。
-- value_numeric 適合數值欄位；
-- value_text 用來完整保留 P/M/S 等文字格式欄位。
-- ============================================================
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

CREATE TABLE IF NOT EXISTS ingredient_nutrition_map (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ingredient_id BIGINT NOT NULL UNIQUE,
  nutrition_source_id BIGINT NOT NULL,
  match_method VARCHAR(50),
  match_score DECIMAL(8,6),
  status VARCHAR(20),
  reviewed_at TIMESTAMP NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_inm_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id),
  CONSTRAINT fk_inm_source
    FOREIGN KEY (nutrition_source_id) REFERENCES nutrition_source(id)
);

CREATE TABLE IF NOT EXISTS manual_review (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ingredient_id BIGINT NOT NULL,
  candidate_nutrition_id BIGINT,
  candidate_name VARCHAR(255),
  score DECIMAL(8,6),
  status VARCHAR(20) DEFAULT 'PENDING',
  note VARCHAR(500),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TIMESTAMP NULL,
  CONSTRAINT fk_mr_ingredient
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(id),
  CONSTRAINT fk_mr_source
    FOREIGN KEY (candidate_nutrition_id) REFERENCES nutrition_source(id)
);

-- 目前產品功能只使用熱量。
-- 未來若要顯示蛋白質/脂肪/鈉等，可直接從 nutrition_values 取得，
-- 不需要重新匯入 Excel。
CREATE TABLE IF NOT EXISTS recipe_nutrition_summary (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  recipe_id BIGINT NOT NULL UNIQUE,
  energy_kcal DECIMAL(18,6),
  estimated_price DECIMAL(18,2),
  coverage_percent DECIMAL(8,2),
  price_coverage_percent DECIMAL(8,2),
  weight_coverage_percent DECIMAL(8,2),
  calorie_status VARCHAR(30) NOT NULL DEFAULT 'INSUFFICIENT',
  price_status VARCHAR(30) NOT NULL DEFAULT 'INSUFFICIENT',
  calculated_at TIMESTAMP NULL,
  CONSTRAINT fk_rns_recipe
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS etl_runs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  run_type VARCHAR(100) NOT NULL,
  status VARCHAR(30) NOT NULL,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP NULL,
  message TEXT
);
