# Auto Review Pipeline

## 原本流程

```text
06 Match
→ 07 Export Manual Review
→ 人工判定
→ 08 Apply
→ 09 Calculate
```

## 新流程

```text
06 Match
→ 07 Auto Review
→ 08 Apply Auto Review
→ 09 Calculate
```

### 07_auto_review_nutrition.py
- 對 `manual_review.status='PENDING'` 分組
- 每個 ingredient 最多選 1 個 APPROVED
- 其他全部 REJECTED
- 明顯類別衝突直接拒絕
- 無可靠候選時全部拒絕
- 輸出 `data/processed/auto_review_result.json`

### 08_apply_manual_review.py
- 讀 DB 中 APPROVED 結果
- 驗證每個 ingredient 最多一個 APPROVED
- UPSERT 至 `ingredient_nutrition_map`

### 09_calculate_recipe_nutrition.py
- 使用 `weight_g × 每100g營養 / 100`
- 聚合到 `recipe_nutrition_summary`
- 顯示 nutrition coverage 統計

### 10_pipeline.py
直接跑 01~09，不再人工停頓。
