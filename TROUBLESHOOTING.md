# Recipe Crawler --- 問題與解決紀錄

**整理日期：2026-09-16**

## 系統架構

``` text
Airflow
   ↓
Kafka crawler_jobs
   ↓
crawler-direct + crawler-worker-1~4 (Proxy)
   ↓
YTower 爬蟲
   ↓
Kafka ytower_recipe_results
   ↓
mongo-writer
   ↓
MongoDB / recipe_ai.raw_recipes
```

## 1. Docker 權限錯誤

### 問題

GCP VM 執行 Docker 時出現：

``` text
permission denied while trying to connect to the docker API
unix:///var/run/docker.sock
```

### 原因

目前登入的 Linux user 沒有 Docker daemon 權限。

### 解決

將 user 加入 `docker` group，重新登入 SSH 後確認：

``` bash
docker ps
```

正常顯示 container 即代表解決。

## 2. Git Pull 出現 Permission denied

### 問題

曾出現 MongoDB/MySQL data directory 無法讀取，以及 Airflow DAG 被
container UID 改變後 Git 無法修改。

### 原因

Docker container 與 Git repository 共用 host directory，但 container
使用不同 UID，例如 Airflow UID 50000。

### 解決

持久資料使用 Docker named volume。Airflow DAG 維持唯讀 bind mount：

``` yaml
./airflow/dags:/opt/airflow/dags:ro
```

必要時修復 DAG ownership：

``` bash
sudo chown -R $USER:$USER airflow/dags
```

正常更新：

``` bash
git pull origin main
docker compose up -d --build
```

除非確定要清空資料，否則不要使用：

``` bash
docker compose down -v
```

`-v` 會刪除 MongoDB、MySQL、Kafka 等 named volume 資料。

## 3. YTower Direct IP HTTP 403 / Challenge

### 問題

GCP Direct Worker 曾遇到 HTTP 403 / challenge。

### 處理方式

``` text
404 / 410
→ not_found
→ 才計算 consecutive missing

403 / 429 / challenge
→ blocked
→ 不計算 missing

5xx / timeout / ProxyError
→ retryable
→ 不計算 missing
```

Direct Worker 遇到 blocked 時採 cooldown/retry，不把該 SEQ 當成不存在。

## 4. Free Proxy 數量不足

### 現象

Proxy Manager 曾出現大量候選 Proxy，但實際通過驗證的 TW Proxy 很少。

### 原因

候選 Proxy 還必須通過：

``` text
Discovery
→ Deduplicate
→ TCP check
→ HTTPS request
→ Exit IP
→ GeoIP = TW
→ Latency
→ MongoDB proxy_pool
```

### 解決

Proxy Manager 整合多個來源並統一驗證。Proxy worker 沒有 verified Proxy
時不加入 Kafka consumer group，避免沒有 Proxy 的 worker 搶 Kafka job。

## 5. requests.ProxyError 寫法錯誤

### 問題

``` text
module 'requests' has no attribute 'ProxyError'
```

### 原因

原本使用：

``` python
requests.ProxyError
```

### 解決

``` python
import requests
from requests import exceptions as req_exc
```

並改成：

``` python
except (
    req_exc.Timeout,
    req_exc.ConnectionError,
    req_exc.ProxyError,
    req_exc.HTTPError,
) as exc:
```

HTTPError 同樣使用：

``` python
raise req_exc.HTTPError(...)
```

## 6. Kafka Consumer 長時間 PreparingRebalance

### 原因一

Proxy worker 原本沒有 message 時會關閉 consumer，再重新加入
group，造成反覆 Join/Leave/Rebalance。

### 解決

consumer 保持存在並持續 `poll()`；沒有 message 時直接 `continue`。

### 原因二

原本一個 Kafka message 代表整個 prefix，例如 A01 1-5000，單一 job
可能執行數小時。

### 解決

Full Crawl 改成 chunk relay：

``` text
A01 1-250
↓ 成功
enqueue A01 251-500
↓
commit A01 1-250
```

目前：

``` text
FULL_CRAWL_CHUNK_SIZE=250
```

## 7. 50 筆連續不存在必須跨 Chunk 保留

下一個 Kafka chunk 會攜帶：

``` json
{
  "consecutive_not_found": 30
}
```

流程：

``` text
Chunk N missing_out
→ Kafka
→ Chunk N+1 missing_in
```

因此即使下一個 chunk 被不同 worker 處理，也能保留連續 missing 狀態。

## 8. Chunk 250 與 Kafka Commit

目前 Kafka consumer：

``` python
enable_auto_commit=False
max_poll_records=1
```

完成一個 chunk job 後才執行：

``` python
consumer.commit()
```

若 B02 1-250 在 B02-67 因 Proxy 全部失敗：

``` text
job failed
offset not committed
```

Kafka 之後可重新投遞 B02 1-250，因此前面已處理的部分可能重抓。

注意：250 代表最多 250 個 numeric SEQ，不代表一定產生 250 筆食譜。例如
A01 1-250 可能 `produced=236`。

目前決定：維持 chunk=250，暫時不修改 commit 設計。

## 9. Worker 成功後不應離開 Kafka Group

成功後保留 Kafka consumer、HTTP session 與 Proxy，直接回到
`poll()`，避免每個 chunk 都 LeaveGroup。

失敗 job 則不 commit，關閉 consumer，讓 Kafka 從最後 committed offset
重新投遞該 chunk。

## 10. Proxy 失敗後一直拿到同一支 Proxy

### 問題

``` text
Proxy A failed
→ Proxy B failed
→ 又租到 Proxy B
→ 再次失敗
```

`MAX_PROXY_SWITCHES_PER_SEQ=5` 可能實際變成 A → B → B → B → B。

### 原因

原本 Proxy 要累積到 `PROXY_MAX_FAILURES`
才會進入較長隔離，因此第一次、第二次 runtime failure
後仍可能立即被重新租用。

### 解決

修改 `proxy_pool.py`：

``` text
Proxy runtime failure
→ consecutive_failures + 1
→ 立即 cooldown 5 分鐘
→ cooldown 期間 lease_proxy 不再取得它
```

若累積失敗 \>= 3：

``` text
is_alive = false
quarantine = 30 minutes
```

目前不修改 Kafka commit / chunk 250。

## 11. 修改 Worker / proxy_pool.py 後需要 Build

Crawler Python 程式是 build 進 Docker image，因此修改：

``` text
ytower_crawler_worker.py
proxy_pool.py
```

後需重新 build/recreate crawler：

``` bash
docker compose up -d --build crawler-worker-1 crawler-worker-2 crawler-worker-3 crawler-worker-4
```

不需要 `docker compose down`，也不要使用 `docker compose down -v`。

## 12. Docker Desktop 重開機後無法 Compose Up

### 原因

Windows 重開機後 Docker Desktop daemon 尚未啟動。

### 解決

開啟 Docker Desktop，等待 Engine Running，再於 PowerShell：

``` powershell
docker info
docker ps
docker compose up -d
```

單純重開機不需要重新 build image。

## 13. MongoDB Unauthorized

### 問題

``` text
MongoServerError[Unauthorized]:
Command listCollections requires authentication
```

### 原因

MongoDB 啟用了 authentication，但 `mongosh` 沒有提供帳號密碼。

### 解決

PowerShell：

``` powershell
docker compose exec mongodb mongosh recipe_ai -u root -p --authenticationDatabase admin
```

輸入密碼後即可查詢。

## 14. MongoDB 食譜查到 0 筆

### 問題

``` javascript
db.recipes.countDocuments({})
```

得到 0。

### 原因

實際 collection 不是 `recipes`。

目前 `recipe_ai` collections：

``` text
crawl_state
proxy_pool
raw_recipes
```

### 正確查詢

``` javascript
db.raw_recipes.countDocuments({})
```

## 15. PowerShell 執行 mongosh 出現 Syntax error

### 問題

使用：

``` powershell
docker compose exec mongodb sh -c 'mongosh ... --eval "db.raw_recipes.countDocuments({})"'
```

出現：

``` text
sh: 1: Syntax error: "(" unexpected
```

### 原因

PowerShell → docker compose → sh -c → mongosh 多層引號解析後，`()` 被
`sh` 當成 shell syntax。

### 解決

不要經過 `sh -c`。

直接登入：

``` powershell
docker compose exec mongodb mongosh recipe_ai -u root -p --authenticationDatabase admin
```

登入後：

``` javascript
db.raw_recipes.countDocuments({})
```

或 PowerShell 一行：

``` powershell
docker compose exec mongodb mongosh recipe_ai -u root -p --authenticationDatabase admin --quiet --eval 'db.raw_recipes.countDocuments({})'
```

## 目前系統狀態總結

``` text
Airflow
↓
Kafka crawler_jobs
↓
1 Direct + 4 Proxy Workers
↓
Full Crawl：250 SEQ / chunk
↓
成功 → relay 下一個 chunk → commit
失敗 → 不 commit → Kafka 重新投遞
↓
Kafka ytower_recipe_results
↓
mongo-writer
↓
MongoDB recipe_ai.raw_recipes
```

目前維持：

-   chunk = 250
-   暫時不修改 Kafka commit
-   保留 50 consecutive missing
-   Proxy runtime failure 立即 cooldown
-   壞 Proxy 不立即重新租用
-   Kafka job failure 不 commit
-   MongoDB 食譜 collection 使用 `raw_recipes`

## 後續可再處理

目前可先讓爬蟲繼續執行，之後再處理：

1.  Proxy worker 在 `crawl_job()` 內切換 Proxy 後，外層 session/proxy
    context 的同步。
2.  Relay 下一個 chunk 已成功送出、但目前 chunk 尚未 commit
    時若程式崩潰，可能產生重複 chunk；目前屬於 at-least-once 行為。
