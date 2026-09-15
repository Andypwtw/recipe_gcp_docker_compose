# API 原始材料顯示修改

## 修改目的

食譜是給使用者閱讀的，因此 API 顯示材料時，
不應使用清洗 / 標準化後的欄位重新組字。

## 修改前

```text
canonical_name
quantity_value
canonical_unit
```

例如：

```json
{
  "canonical_name": "雞蛋",
  "quantity_value": 8,
  "canonical_unit": "顆",
  "weight_g": 440
}
```

## 修改後

直接使用：

```text
recipe_ingredients.raw_text
```

例如：

```json
{
  "line_no": 1,
  "raw_text": "雞蛋 8顆"
}
```

## 資料流

```text
MongoDB 原始「材料」
        │
        ├── 原始材料 → raw_text → API 顯示
        │
        └── 清洗材料 → normalize → 計算 / 搜尋 / Matching
```

## 不新增的資料

依目前需求，不增加：

```text
食譜副標題
材料 A/B/C 分組
英文食材名稱
英文數量 / 單位
```

## 不受影響

以下資料仍保留：

```text
canonical_name
quantity_value
unit_id
weight_g
```

它們仍用於：

```text
搜尋
食材 Matching
重量換算
熱量計算
```
