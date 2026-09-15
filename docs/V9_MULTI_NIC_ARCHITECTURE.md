# Recipe Collections V9 — Multi-NIC Crawler Architecture

## Scope

V9 changes ONLY the crawler infrastructure layer.

Preserved from formal V8:

- PostgreSQL remains Airflow metadata DB.
- MySQL `recipe_ai` remains the application database.
- MongoDB remains raw recipe storage.
- Kafka-to-MongoDB keeps upsert-by-SEQ semantics.
- V8 ETL `01 -> 09` remains the official data pipeline.
- V8 ER Model, nutrition import/matching, categories, API and Hermes remain unchanged.

## Kafka topology

Job topics:

- `crawler_jobs_ip1`
- `crawler_jobs_ip2`
- `crawler_jobs_ip3`
- `crawler_jobs_ip4`

Unified result topic:

- `ytower-recipes`

Flow:

```text
Airflow dispatch_crawler_jobs
        |
        +--> crawler_jobs_ip1 --> Worker 1 --> PRIVATE_IP_1 --> NIC1 --> Cloud NAT 1
        +--> crawler_jobs_ip2 --> Worker 2 --> PRIVATE_IP_2 --> NIC2 --> Cloud NAT 2
        +--> crawler_jobs_ip3 --> Worker 3 --> PRIVATE_IP_3 --> NIC3 --> Cloud NAT 3
        +--> crawler_jobs_ip4 --> Worker 4 --> PRIVATE_IP_4 --> NIC4 --> Cloud NAT 4

Worker 1..4
        |
        v
ytower-recipes
        |
        v
kafka-to-mongodb
        |
        v
MongoDB
        |
        v
V8 ETL 01..09
        |
        v
MySQL recipe_ai
```

## Why workers use `network_mode: host`

A normal Docker bridge container does not own the GCP VM's NIC private address.
Therefore `curl --interface 10.x.x.x` inside a bridge container is not a reliable
way to bind to that host NIC.

V9 GCP crawler workers use Linux host networking. Each worker creates an HTTP
session bound to its assigned `PRIVATE_IP_n`; Linux policy routing then selects
the matching routing table/NIC, and GCP Cloud NAT maps that NIC's private source
address to the desired public egress IP.

## Important crawler limitation

V9 deliberately does NOT invent YTower selectors.

You must put the verified production logic into:

- `crawler/ytower_crawler.py::build_crawl_jobs()`
- `crawler/ytower_crawler.py::crawl_job(job, session)`

All HTTP calls inside `crawl_job()` must use the supplied `session`.
