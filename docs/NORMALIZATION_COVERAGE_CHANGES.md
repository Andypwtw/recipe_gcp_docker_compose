# Normalization / Nutrition Coverage Fix

## 主要修改

- 食材群組前綴清洗強化：A.-I. / 甲乙 / 數字群組標記可移除，但保留真正的 `X.O醬` 類名稱。
- 數字型商品名稱解析修正：`A1醬 1大匙`、`8吋戚風蛋糕 1個` 不再把商品名稱中的數字誤當數量。
- `03a_build_unit_weight_map.py`：由原始食譜明確「數量 + 約克數」標示產生 ingredient + count-unit 候選表。
- 新增 `qualitative_amount_rules`：只允許 confidence >= 80 且 auto_convert=true 的高信心定性用量自動轉 weight_g。
- 高信心規則資料：`data/reference/qualitative_amount_rules.json` + Excel 研究表。
- `適量` 不做無依據固定克數換算。
- Nutrition Matching 強化：exact/common-name/normalized variant/curated safe mapping/guarded fuzzy。
- 熱量 coverage 改為重量型 coverage，另保留已知重量食材列比例 guard。
- 價格 coverage 改為重量型 coverage，水/冰不列入價格 denominator。
- `recipe_nutrition_summary` 新增 `weight_coverage_percent`。
- Migration 010 改為 MySQL 8.x 可重複執行的 information_schema + prepared ALTER 寫法。
- 保留 Hermes `text` / `message` 相容與 ingredient intent 推薦修正。

## 最終 SQL 修正

`09_calculate_recipe_nutrition.py` 的 `INSERT INTO recipe_nutrition_summary` 已確認欄位與 SELECT 對應為 9 欄：

1. recipe_id
2. energy_kcal
3. estimated_price
4. coverage_percent
5. price_coverage_percent
6. weight_coverage_percent
7. calorie_status
8. price_status
9. calculated_at

不存在重複 `coverage_percent` SELECT 欄位。

## 離線驗證

詳見：

`docs/NORMALIZATION_COVERAGE_VALIDATION.json`

目前驗證為 23 PASS / 0 FAIL。

尚未執行 Docker + MySQL runtime E2E；需在 GCP 啟動後跑完整 pipeline 取得最終熱量/價格成功率。
