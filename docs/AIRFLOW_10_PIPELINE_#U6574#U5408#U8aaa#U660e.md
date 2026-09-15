# Airflow + 10_pipeline.py 整合

## 最終流程

```text
Airflow
→ crawl_ytower
→ Kafka
→ recipe-kafka-to-mongodb
→ MongoDB
→ wait_kafka_to_mongodb
→ Kafka consumer lag = 0
→ run_recipe_pipeline
→ 10_pipeline.py
→ 01 ~ 09
→ check_pipeline_result
```

## 為什麼不能爬蟲結束後直接跑 10

爬蟲結束只代表 Producer 不再送新訊息，
但 Kafka Consumer 可能仍在把訊息寫入 MongoDB。

因此要等：

```text
recipe-mongodb-writer consumer group lag = 0
```

才執行 10_pipeline.py。

## Kafka Consumer

現在採：

```text
MongoDB upsert 成功
→ consumer.commit()
```

所以 Airflow 的 lag=0 檢查能對應到 MongoDB 已完成寫入。

## 第一次執行

目前 DAG：

```python
schedule=None
```

先在 Airflow UI 手動 Trigger。

確認：

```text
crawl_ytower
wait_kafka_to_mongodb
run_recipe_pipeline
check_pipeline_result
```

四個 Task 都成功。

## 正式排程

全部測通後，例如每天凌晨 3 點：

```python
schedule="0 3 * * *"
```
