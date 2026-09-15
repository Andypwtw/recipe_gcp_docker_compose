# Recipe Collections 正規化 ER Model 中文詳細說明

## 一、目前資料架構的設計目標

目前系統把資料分成兩個資料層：

1. **MongoDB Raw Layer**
   - 儲存 YTower 爬蟲剛取得、尚未清洗的原始 JSON 文件。
   - Collection：`recipe_ai.raw_recipes`
   - 這一層保留來源網站原始結構，適合 Kafka Consumer 直接寫入。

2. **MySQL Normalized Layer**
   - 儲存清洗、拆解、正規化後的正式資料。
   - 食譜、食材、單位、重量、食品營養資料彼此拆表，避免大量重複資料。
   - Flask API / Hermes / LINE Bot 主要查詢這一層。

---

## 二、為什麼營養 Excel 要重新正規化

2025 食品營養 Excel 共有大量欄位，包括：

- 熱量
- 修正熱量
- 水分
- 蛋白質
- 脂肪
- 碳水化合物
- 礦物質
- 各類維生素
- 脂肪酸
- 胺基酸
- 膽固醇
- 酒精含量
- 其他欄位

如果把每一項營養素都直接設成 `nutrition_source` 的固定欄位，
未來 Excel 新增營養項目時就必須 ALTER TABLE。

因此改成三層：

```text
nutrition_source
       │
       ├── 食品基本資料
       │
       └── 1:N
           nutrition_values
                 │
                 └── N:1
                     nutrient_definitions
```

### nutrition_source
一列代表「一種食品樣品」。

例如：

```text
A0100101 / 穀物類 / 大麥仁
```

只保存食品本身的識別資料。

### nutrient_definitions
一列代表「一個營養項目定義」。

例如：

```text
熱量 / kcal / 熱量(kcal)
粗蛋白 / g / 粗蛋白(g)
鈉 / mg / 鈉(mg)
維生素C / mg / 維生素C(mg)
```

同一個營養項目只定義一次。

### nutrition_values
真正保存：

```text
哪一個食品
+
哪一個營養項目
+
數值
```

例如：

```text
大麥仁 + 熱量 = 364.6228
大麥仁 + 粗蛋白 = 8.5578
大麥仁 + 鈉 = 12.6115
```

這是營養資料的正規化核心。

---

## 三、目前只使用熱量，但保留所有營養資料

目前產品功能只計算：

```text
熱量(kcal)
```

公式：

```text
食材熱量
=
每 100 g 熱量
× 食材 weight_g
÷ 100
```

整道食譜：

```text
SUM(每項食材熱量)
```

因此 `09_calculate_recipe_nutrition.py`
只會從 `nutrient_definitions.source_column_name = '熱量(kcal)'`
取得熱量數值。

但 `05_import_nutrition_excel.py` 會把 Excel 所有營養欄位匯入：

```text
nutrient_definitions
+
nutrition_values
```

所以未來要增加：

- 蛋白質
- 脂肪
- 碳水化合物
- 鈉
- 鈣
- 鐵
- 維生素
- 脂肪酸

都不需要重新設計營養資料表，也不用重新取得 Excel。

---

## 四、食譜主要資料表

### 1. recipes

用途：食譜主表。

一筆代表一道食譜。

重要欄位：

- `id`：MySQL 內部 PK
- `seq`：YTower 原始食譜識別碼
- `name`：食譜名稱
- `published_date`：上線日期
- `source_url`：來源網址
- `steps`：作法

關聯：

```text
recipes
  1 ─── N recipe_ingredients
  1 ─── N recipe_keywords
  1 ─── 1 recipe_nutrition_summary
```

---

### 2. keywords

關鍵字主檔。

例如：

```text
牛肉
中式料理
主菜
```

因為一個關鍵字可能出現在很多食譜，
所以不能直接每道食譜重複保存相同文字。

---

### 3. recipe_keywords

解決：

```text
recipes N:M keywords
```

多對多關係。

---

## 五、食材正規化

### 4. ingredients

食材主檔。

例如：

```text
雞胸肉
洋蔥
醬油
九層塔
```

同一食材只保存一次。

---

### 5. ingredient_aliases

保存同義名稱。

例如：

```text
番茄 → 番茄
蕃茄 → 番茄
鮮奶 → 牛奶
鮮乳 → 牛奶
```

避免名稱不同造成 Nutrition Matching 失敗。

---

### 6. units

單位主檔。

例如：

```text
g
kg
ml
大匙
小匙
顆
片
朵
```

---

### 7. recipe_ingredients

這是「食譜與食材的關聯明細」。

例如：

```text
牛肉麵
→ 牛肉 300 g
→ 麵條 200 g
→ 醬油 30 ml
```

主要保存：

- `recipe_id`
- `ingredient_id`
- `quantity_value`
- `unit_id`
- `weight_g`
- 原始文字

真正計算熱量最重要的是：

```text
weight_g
```

---

## 六、重量換算資料

### 8. ingredient_unit_weights

處理：

```text
顆
片
朵
根
```

這種不是直接重量單位的資料。

例如：

```text
香菇 + 朵 = 15 g/朵
```

---

### 9. ingredient_densities

處理體積轉重量。

公式：

```text
weight_g
=
volume_ml
× density_g_ml
```

例如：

```text
30 ml 醬油
×
1.16 g/ml
=
34.8 g
```

---

## 七、營養資料

### 10. nutrition_source

食品樣品主表。

Excel 基本欄位：

- 整合編號
- 食品分類
- 樣品名稱
- 內容物描述
- 俗名
- 廢棄率

另外 `raw_data` 保存整列原始 Excel JSON，
作為來源追溯與稽核使用。

---

### 11. nutrient_definitions

所有營養欄位的定義表。

好處：

- 不必為 100 多個營養項目建立 100 多個固定欄位。
- 新版 Excel 增加營養素時可直接新增 definition。
- 可保存 unit 與 group。

---

### 12. nutrition_values

食品與營養項目的交叉表。

關係：

```text
nutrition_source 1:N nutrition_values
nutrient_definitions 1:N nutrition_values
```

實際形成：

```text
nutrition_source N:M nutrient_definitions
```

---

## 八、食材與食品營養資料的 Matching

### 13. ingredient_nutrition_map

正式 Mapping。

例如：

```text
ingredients.雞胸肉
→
nutrition_source.雞胸肉平均值
```

一個 canonical ingredient 目前只選一個正式營養來源。

---

### 14. manual_review

名稱 Matching 的候選與審核歷程。

目前已改成 Auto Review，
所以名稱雖然叫 `manual_review`，
但它實際扮演的是：

```text
Matching Candidate Audit Log
```

會保存：

- candidate
- score
- APPROVED / REJECTED
- note

即使全自動仍保留稽核紀錄。

---

## 九、目前實際使用的熱量結果

### 15. recipe_nutrition_summary

目前只保存：

```text
recipe_id
energy_kcal
coverage_percent
calculated_at
```

`energy_kcal`：
整道食譜估算熱量。

`coverage_percent`：
食材中有成功取得 weight_g 且成功 Matching 熱量資料的比例。

這不是另一項營養成分，
而是「熱量計算完整度」。

---

## 十、ETL 稽核

### 16. etl_runs

可保存每一次 Airflow / ETL 執行狀態。

例如：

```text
CRAWLER
MONGODB_TO_MYSQL
NUTRITION_IMPORT
CALORIE_CALCULATION
```

有助於 GCP 正式部署後追蹤失敗紀錄。

---

# 十一、正規化結果

目前 MySQL 設計大致符合：

### 1NF
欄位保存單一值，
材料不再全部塞在 recipes 的一格內。

### 2NF
多對多關係透過關聯表處理：

```text
recipe_keywords
nutrition_values
```

### 3NF
食材名稱、單位、營養項目等資訊各自只保存在自己的主表：

```text
ingredients
units
nutrient_definitions
nutrition_source
```

其他表只用 FK 指向它們，
避免大量重複。

---

# 十二、目前資料流

```text
Airflow
  ↓
Crawler
  ↓
Kafka
  ↓
MongoDB raw_recipes
  ↓
01 Profile
  ↓
02 Clean
  ↓
03 Normalize
  ↓
MySQL Recipe Tables
  ↓
05 完整營養 Excel Import
  ↓
nutrition_source
nutrient_definitions
nutrition_values
  ↓
06 Matching
  ↓
07 Auto Review
  ↓
08 Apply Mapping
  ↓
09 目前只讀取「熱量(kcal)」
  ↓
recipe_nutrition_summary
  ↓
Flask API
  ↓
Hermes / LINE
```
