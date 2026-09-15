USE recipe_ai;

-- 只保留熱量欄位。
-- MySQL 8.4 支援 DROP COLUMN IF EXISTS。

ALTER TABLE nutrition_source
    DROP COLUMN IF EXISTS protein_g,
    DROP COLUMN IF EXISTS fat_g,
    DROP COLUMN IF EXISTS carbohydrate_g,
    DROP COLUMN IF EXISTS sodium_mg;

ALTER TABLE recipe_nutrition_summary
    DROP COLUMN IF EXISTS protein_g,
    DROP COLUMN IF EXISTS fat_g,
    DROP COLUMN IF EXISTS carbohydrate_g,
    DROP COLUMN IF EXISTS sodium_mg;
