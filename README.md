# Recipe Collections V11

V11 是正式整合版：以 permission-safe Docker / crawler / proxy 架構為主，整合 V10 的正規化、MySQL ER Model、營養計算、Flask API 與 Hermes API。

## 正式資料流

```text
Airflow
  -> crawler_jobs (5 partitions)
  -> crawler-direct + 4 proxy workers
  -> ytower_recipe_results
  -> mongo-writer
  -> MongoDB recipe_ai.raw_recipes
  -> ETL 01~09 / 10_pipeline.py
  -> MySQL recipe_ai
  -> Flask API / Hermes
```

## V11 核心變更

- 單一 `crawler_jobs` Topic，5 partitions / 同一 crawler consumer group。
- 1 個 Direct Worker + 4 個動態 TW Proxy Workers。
- Result Topic 統一為 `ytower_recipe_results`。
- Mongo Writer 寫入 `recipe_ai.raw_recipes`，以 `SEQ` idempotent upsert。
- Docker runtime data 改用 named volumes，不再 chown Git source tree。
- MySQL / MongoDB application service 使用 application user，不使用 root。
- Airflow 拆為 init / webserver / scheduler，DAG 串完整 crawler -> MongoDB -> ETL -> MySQL。
- 修正 Direct Worker job failure offset 風險。
- `published_date` 正式匯入 MySQL。
- 食材 canonical name 移除尾端「約」。
- Direct crawler 補 Browser headers。
- `MAX_POLL_INTERVAL_MS` 預設提高為 16 小時。
- `HERMES_API_KEY` 設定後會實際驗證，原 `{items:[...]}` response 契約保持不變。
- 網頁端不包含在本專案；網頁由另一位組員透過 Flask API 獨立開發。

## 啟動

```bash
bash scripts/bootstrap-env.sh
bash scripts/first-start.sh
```

或自行：

```bash
cp .env.example .env
# 修改所有 CHANGE_ME_* secrets
docker compose --env-file .env up -d --build
```

## Docker 內部連線

- MySQL: `mysql:3306`
- MongoDB: `mongodb:27017`
- Kafka: `kafka:9092`
- PostgreSQL: `postgres:5432`

Host 預設：MySQL 3307、MongoDB 27018、Kafka 9094、Airflow 8080、Flask 5001。

## API

V10 已正式加入的 API 在 V11 全部保留：

- `GET /health`
- `GET /api/v1/recipes`
- `GET /api/v1/recipes/search`
- `GET /api/v1/recipes/random`
- `GET /api/v1/recipes/{seq}`
- `GET /api/v1/categories`
- `POST /api/v1/categories/resolve`
- `POST /api/v1/recommend`
- `POST /api/v1/hermes/recommend`

## 文件

- `docs/V11_ARCHITECTURE.md`
- `docs/V11_INTEGRATION_FIXES.md`
- `docs/V11_FORMAL_VALIDATION.json`
- `docs/er_model.dbml`

舊 V9 Multi-NIC / V10 crawler 元件已移入 `legacy/`，不再由 active compose 使用。
