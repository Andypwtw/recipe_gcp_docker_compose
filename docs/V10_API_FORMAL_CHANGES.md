# V10 正式版 API 變更

V10 保留 V9 的完整 Crawler / Kafka / MongoDB / Airflow / ETL 架構，正式納入 V9-T 驗證過的後端 API 擴充。

## 新增 API

- `GET /api/v1/recipes`
- `GET /api/v1/categories`
- `GET /api/v1/recipes/random`

## 擴充 API

- `GET /api/v1/recipes/search`
  - `page`
  - `limit`
  - `total`
  - `total_pages`
  - `recipe_ingredients.raw_text` 搜尋
  - categories 篩選
- `GET /api/v1/recipes/<seq>`
  - 新增 `categories`
- `POST /api/v1/recommend`
  - 支援後端分頁

## Hermes 相容性

`POST /api/v1/hermes/recommend` 保持原有 request 內容與 `{ "items": [...] }` response 外層格式，並持續使用原有 `recommend_recipes()` 介面。網頁分頁使用獨立的 `recommend_recipes_page()`，避免前端需求破壞 Hermes 契約。

## 網頁端

V10 正式版不包含 Streamlit 或任何網頁程式。網頁由其他組員獨立維護，僅透過 HTTP API 存取本專案。
