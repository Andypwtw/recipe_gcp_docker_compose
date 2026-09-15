# Recipe Collections V11 Architecture

V11 uses the permission-safe Docker architecture as the active infrastructure layer and preserves the V10 normalized MySQL / nutrition / API layer.

## Active flow

```text
Airflow Scheduler
  -> crawler_jobs (5 partitions)
  -> ytower-crawler-group
       -> crawler-direct
       -> crawler-worker-1..4 (dynamic proxy pool)
  -> ytower_recipe_results
  -> mongo-writer
  -> MongoDB recipe_ai.raw_recipes
  -> V10 ETL 01..09 via scripts/10_pipeline.py
  -> MySQL recipe_ai
  -> Flask API / Hermes API
```

## Docker-internal addresses

- Kafka: `kafka:9092`
- MongoDB: `mongodb:27017`
- MySQL: `mysql:3306`
- PostgreSQL: `postgres:5432`

Host ports are for host-side tools only and must not be used for container-to-container communication.

## Storage

Runtime DB/broker/Airflow data uses Docker named volumes. Git-managed source files are not chowned by containers.

## Data contract

Crawler recipe records preserve the V10-required fields:

- `SEQ`
- `食譜名稱`
- `上線日期`
- `關鍵字`
- `食譜網址`
- `材料`
- `做法步驟`

Crawler metadata such as `prefix`, `seq_num`, worker, network mode, and proxy metadata is retained in MongoDB but ignored by downstream ETL unless explicitly used.
