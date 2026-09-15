# Airflow 自動化 Pipeline

## DAG 1: recipe_full_pipeline

1. `crawl_ytower`
2. `wait_kafka_to_mongodb`
3. `run_recipe_pipeline`
4. `check_pipeline_result`

### Task 1
呼叫 `crawler.ytower_crawler.crawl_and_publish()`。

### Task 2
檢查 Kafka consumer group `recipe-mongodb-writer`。
當所有 partition 的 lag 都為 0 才成功。

### Task 3
執行：

```bash
cd /workspace/python
python scripts/10_pipeline.py
```

`10_pipeline.py` 使用 `sys.executable` 執行 01~07，
因此可以在 Python container 或 Airflow container 使用。

### Task 4
確認：

```text
/workspace/data/manual_review/manual_review.json
```

存在並可讀取。

## DAG 2: recipe_post_review_pipeline

1. `validate_manual_review`
2. `apply_manual_review`
3. `calculate_recipe_nutrition`

只有人工完成所有 `APPROVED` / `REJECTED` 後才手動 Trigger。

## 正式排程

初期：

```python
schedule=None
```

驗證後可改：

```python
schedule="0 3 * * *"
```

代表每天凌晨 3 點執行。
