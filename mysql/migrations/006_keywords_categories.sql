USE recipe_ai;

CREATE TABLE IF NOT EXISTS recipe_categories (
  recipe_id BIGINT NOT NULL,
  category_type VARCHAR(50) NOT NULL,
  category_name VARCHAR(100) NOT NULL,
  source_keyword VARCHAR(100) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (recipe_id, category_type, category_name),
  INDEX idx_recipe_categories_name (category_name),
  INDEX idx_recipe_categories_type_name (category_type, category_name),
  CONSTRAINT fk_rc_recipe
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
    ON DELETE CASCADE
);
