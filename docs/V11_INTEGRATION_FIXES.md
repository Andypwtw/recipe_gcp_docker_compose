# V11 整合修正正式報告

## 目的

V11 以 `recipe_stack_PERMISSION_SAFE` 的 Docker / Crawler / Proxy / Kafka / Airflow 架構為主，整合 V10 的 MySQL Schema、ETL、營養計算、Flask API 與 Hermes API。下列內容為整合前全面檢查後，已納入 V11 的必要修正與建議修正。

## 一、整合前必須修正項目

### 1. MongoDB collection 契約統一

問題：新 Mongo Writer 原本寫入 `recipe_ai.recipes`，V10 ETL 固定讀取 `recipe_ai.raw_recipes`。

V11：新增 `MONGO_COLLECTION` 設定，Mongo Writer 正式寫入 `raw_recipes`；MongoDB 初始化同時建立 `SEQ` unique index 與 `(prefix, seq_num)` index。

### 2. Airflow 與 V10 ETL 的環境變數統一

問題：新 Stack 使用 `MYSQL_*` 與拆分的 Mongo 參數，V10 ETL 使用 `DB_*` 與 `MONGO_URI`。

V11：Airflow 同時提供 `DB_HOST/PORT/NAME/USER/PASSWORD`、`MYSQL_*`、`MONGO_URI`、`MONGO_COLLECTION`。Docker 內部連線固定為 `mysql:3306`、`mongodb:27017`、`kafka:9092`。

### 3. MySQL Schema 初始化

問題：新 Stack 原本只掛 MySQL named volume，沒有掛 V10 `mysql/init/001_schema.sql`。

V11：MySQL 同時掛載 `mysql_data:/var/lib/mysql` 與 `./mysql/init:/docker-entrypoint-initdb.d:ro`。新 volume 第一次建立時會建立 V10/V11 既有 Schema。

### 4. Airflow Full Pipeline 補完整

問題：新 Stack 原 DAG 只有 dispatch job，沒有等待 crawler、Mongo Writer、ETL 與結果檢查。

V11 DAG `recipe_full_pipeline`：

```text
dispatch_crawler_jobs
 -> wait_crawler_jobs
 -> wait_mongodb_writer
 -> run_recipe_pipeline
 -> check_pipeline_result
```

Job Topic 使用 `crawler_jobs` / group `ytower-crawler-group`；Result Topic 使用 `ytower_recipe_results` / group `ytower-mongo-writer`。Sensor 除了 lag=0，還檢查本次 dispatch/result 的 offset baseline，避免空 Topic 被誤判成功。

### 5. Direct Worker offset 安全性

問題：Direct Worker 原本 job 失敗後保持同一 consumer 繼續 poll，後續成功 commit 可能跨過前一筆未成功 job。

V11：Direct Worker 任何 prefix job 失敗時關閉並重建 consumer，使未 commit offset 重新投遞，不允許後續 commit 跨過失敗工作。

### 6. Airflow Image / Volume 補齊 V10 ETL 需求

問題：新 Stack Airflow Image 缺 `pandas/openpyxl/numpy/python-dateutil/curl-cffi`，且未掛 `/workspace/python` 與 `/workspace/data`。

V11：以 V10 Airflow dependencies 為基礎，加上 `requests[socks]`；Airflow 掛載 Python source（唯讀）與 data（可寫），可直接執行 `scripts/10_pipeline.py`。

### 7. Flask API service 加回正式 Stack

問題：permission-safe stack 本身沒有 V10 Flask API service。

V11：新增 `python` service，只依賴 MySQL healthy，使用 application DB user，外部預設 port 5001。

## 二、建議一起修正項目

### 1. published_date 不再丟失

V10 雖然正規化保留 `published_date`，但 MySQL import 寫死 `NULL`。V11 新增日期解析，可處理 `YYYY-M-D` 與後方含上午/下午時間的格式，最後寫入 MySQL `DATE`。

現有 29,597 筆 raw JSON：22,184 筆為純日期，7,413 筆為日期加時間，皆可由新規則抽取日期。

### 2. 食材名稱尾端「約」清理

V10 對 `牛肉 約1/2斤` 會得到 `canonical_name=牛肉 約`。V11 在 canonicalization 階段移除名稱尾端 `約`，避免 Nutrition Matching 被多餘字元干擾。

### 3. Direct Worker Browser headers

V11 Direct Session 加入與 Proxy Session 同級的 Browser User-Agent、Referer、Accept-Language，降低直接出口因預設 requests header 被辨識的機率。

### 4. Kafka max.poll.interval 提高

Prefix job 可能處理數千筆食譜。V11 預設 `MAX_POLL_INTERVAL_MS` 由 8 小時提高為 16 小時，降低長任務觸發 consumer-group rebalance 的風險。長期仍可再評估將 prefix 拆分成較小 job。

### 5. Hermes API Key 實際生效

V10 `.env` 有 `HERMES_API_KEY`，但 endpoint 未檢查。V11 保留原 request/response 契約，並採「有設定才驗證」策略：設定 key 時接受 `X-API-Key` 或 `Authorization: Bearer`；留空時維持本機開發向後相容。

### 6. MongoDB / MySQL application user

V11 application service 改用 `recipe_app` 類型帳號；root 僅供初始化與維運。`ensure-db-users.sh` 可用於既有 volume 更新 application user 密碼與權限。

### 7. Named Volumes

MySQL、MongoDB、Kafka、PostgreSQL 與 Airflow runtime data 全部使用 Docker named volumes，避免 container chown Git working tree。

## 三、已確認不需修改的資料契約

- Crawler `材料` 使用 ` | ` 串接，與 V10 parser 相容。
- `2∼3顆` 等 range 可被 V10 clean/normalize 流程正確轉成 `2~3` 並解析 midpoint。
- Crawler 多出的 `prefix/seq_num/created_at/crawler_worker/network_mode/proxy_*` 可留在 MongoDB，不影響 V10 ETL。
- 現有 raw JSON 有 31 個實際 prefix，全部落在 A01~I10 dispatch 範圍內。
- 現有最大 numeric SEQ=3899，因此 `MAX_SEQ_NUMBER=5000` 仍足夠。
- 現有最大連續缺號=14，因此 `MAX_NOT_FOUND_LIMIT=50` 保有充分安全距離。

## 四、仍需 GCP / Docker Runtime 驗證

下列項目無法只靠靜態檢查證明：

1. YTower 線上 HTML selector 與 Big5 encoding 是否仍完全有效。
2. 免費台灣 Proxy 的即時可用率、速度與 TLS 穩定度。
3. Docker Compose 全服務 build/up、Airflow sensor、Kafka consumer group 與 Mongo/MySQL live integration。

因此 V11 的封裝驗證會明確區分「靜態/邏輯驗證」與「目標主機 Runtime 驗證」。
