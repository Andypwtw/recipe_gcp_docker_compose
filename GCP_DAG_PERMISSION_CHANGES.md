# DAG / GCP permission changes

## DAG behavior
- Airflow still checks once per day (`@daily`) with `max_active_runs=1`.
- First run performs FULL crawl.
- Crawl completion is checkpointed before the ETL pipeline.
- If ETL fails after crawl completion, the next daily run resumes ETL only and does not crawl again.
- Seven days are counted from `last_successful_crawl_at`.
- Incremental crawl compares MongoDB `raw_recipes` count before/after the run. If 0 new unique recipes were added, ETL is skipped and the crawl checkpoint remains successful.
- Crawler sensor timeout defaults to 7 days; Mongo writer sensor timeout defaults to 1 day.

## GCP / Docker permissions
- `init-permissions` creates `data/raw`, `data/reference`, and `data/processed` before services start.
- Airflow logs/plugins remain owned by Airflow UID and group 0 with group-write + setgid directories.
- `./data` is a cross-container/host exchange directory. It is made read/write for host + container UIDs to avoid UID 50000 vs UID 1000 bind-mount failures on GCP.
- Crawler containers run as UID 1000, GID 0 and mount `./data:/workspace/data`.
- Git-managed DAG/Python source is mounted read-only into Airflow; containers do not chown the source tree.

## Deployment checks
```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps

docker exec recipe-v11-crawler-direct ls -ld /workspace/data /workspace/data/raw /workspace/data/reference /workspace/data/processed
docker exec recipe-v11-airflow-scheduler sh -c 'touch /workspace/data/processed/.airflow_perm_test && rm /workspace/data/processed/.airflow_perm_test'
docker exec recipe-v11-crawler-direct sh -c 'touch /workspace/data/processed/.crawler_perm_test && rm /workspace/data/processed/.crawler_perm_test'
```
