# Calories Only Mode

本專案目前只保留「熱量」功能。

## Nutrition Matching

名稱 Matching 流程仍然需要：

```text
ingredients
→ nutrition_source.food_name
→ 07_auto_review_nutrition.py
→ ingredient_nutrition_map
```

但真正使用的營養欄位只有：

```text
nutrition_source.energy_kcal
```

## 熱量計算

每項食材：

```text
食材熱量 = 每 100g 熱量 × weight_g / 100
```

整道食譜：

```text
recipe energy_kcal = SUM(所有可計算食材熱量)
```

## recipe_nutrition_summary

只保留：

```text
recipe_id
energy_kcal
coverage_percent
calculated_at
```

`coverage_percent` 不是營養成分，而是資料完整率，
用於判斷這個熱量值有多少比例的食材成功計入。

## API

搜尋與食譜詳細資料只顯示：

```json
{
  "energy_kcal": 520.35,
  "calorie_coverage_percent": 92.86
}
```

不再回傳蛋白質、脂肪、碳水化合物、鈉等營養資訊。
