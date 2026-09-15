from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.sensors.python import PythonSensor
from kafka import KafkaConsumer, KafkaProducer, TopicPartition

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
JOB_TOPIC = os.getenv("KAFKA_JOB_TOPIC", "crawler_jobs")
RESULT_TOPIC = os.getenv("KAFKA_RESULT_TOPIC", "ytower_recipe_results")
CRAWLER_GROUP = "ytower-crawler-group"
MONGO_WRITER_GROUP = "ytower-mongo-writer"
MAX_SEQ_NUMBER = int(os.getenv("MAX_SEQ_NUMBER", "5000"))
MAX_NOT_FOUND_LIMIT = int(os.getenv("MAX_NOT_FOUND_LIMIT", "50"))
DAG_SCHEDULE = os.getenv("YTOWER_DAG_SCHEDULE", "").strip() or None


def generate_prefixes(letters="ABCDEFGHI", num1_range=(1, 10)) -> list[str]:
    return [
        f"{letter}{n1:02d}"
        for letter in letters
        for n1 in range(num1_range[0], num1_range[1] + 1)
    ]


def topic_end_offset(topic: str) -> int:
    consumer = KafkaConsumer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        enable_auto_commit=False,
    )
    try:
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return -1
        tps = [TopicPartition(topic, p) for p in sorted(partitions)]
        consumer.assign(tps)
        return sum(consumer.end_offsets(tps).values())
    finally:
        consumer.close()


def consumer_group_lag(topic: str, group_id: str) -> tuple[int, int]:
    consumer = KafkaConsumer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=group_id,
        enable_auto_commit=False,
    )
    try:
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return 0, -1

        tps = [TopicPartition(topic, p) for p in sorted(partitions)]
        consumer.assign(tps)
        end_offsets = consumer.end_offsets(tps)
        total_end = 0
        total_lag = 0

        for tp in tps:
            committed = consumer.committed(tp)
            if committed is None:
                committed = 0
            end = end_offsets[tp]
            lag = max(end - committed, 0)
            total_end += end
            total_lag += lag
            print(
                f"group={group_id} {tp.topic}[{tp.partition}] "
                f"committed={committed} end={end} lag={lag}"
            )

        return total_lag, total_end
    finally:
        consumer.close()


def dispatch_crawler_jobs() -> dict:
    prefixes = generate_prefixes()
    job_baseline = topic_end_offset(JOB_TOPIC)
    result_baseline = topic_end_offset(RESULT_TOPIC)
    if job_baseline < 0 or result_baseline < 0:
        raise RuntimeError("Kafka topics are not ready")

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        acks="all",
        retries=5,
    )
    try:
        for prefix in prefixes:
            job = {
                "prefix": prefix,
                "start_num": 1,
                "end_num": MAX_SEQ_NUMBER,
                "max_not_found_limit": MAX_NOT_FOUND_LIMIT,
            }
            producer.send(JOB_TOPIC, key=prefix.encode("utf-8"), value=job).get(timeout=30)
        producer.flush()
    finally:
        producer.close()

    result = {
        "dispatched": len(prefixes),
        "job_baseline": job_baseline,
        "result_baseline": result_baseline,
    }
    print(f"dispatch result={result}")
    return result


def crawler_jobs_are_drained(**context) -> bool:
    dispatch = context["ti"].xcom_pull(task_ids="dispatch_crawler_jobs") or {}
    dispatched = int(dispatch.get("dispatched") or 0)
    baseline = int(dispatch.get("job_baseline") or 0)
    lag, end = consumer_group_lag(JOB_TOPIC, CRAWLER_GROUP)

    if dispatched <= 0:
        return False
    if end < baseline + dispatched:
        print(f"Job topic has not received all dispatched jobs: end={end}, expected>={baseline + dispatched}")
        return False
    print(f"crawler job topic end={end}, lag={lag}")
    return lag == 0


def mongodb_writer_is_synced(**context) -> bool:
    dispatch = context["ti"].xcom_pull(task_ids="dispatch_crawler_jobs") or {}
    result_baseline = int(dispatch.get("result_baseline") or 0)
    lag, end = consumer_group_lag(RESULT_TOPIC, MONGO_WRITER_GROUP)

    # Because the crawler job queue is already drained before this sensor runs,
    # end > baseline proves this run actually produced at least one new recipe.
    if end <= result_baseline:
        print(f"No new crawler result yet: result_end={end}, baseline={result_baseline}")
        return False
    print(f"result topic end={end}, lag={lag}, baseline={result_baseline}")
    return lag == 0


def check_pipeline_result():
    from app.db import get_connection

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM recipe_nutrition_summary")
        row = cur.fetchone()

    count = int(row["cnt"] or 0)
    if count <= 0:
        raise RuntimeError("recipe_nutrition_summary is empty after V11 pipeline")
    print(f"V11 pipeline OK. recipe_nutrition_summary rows={count}")


default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="recipe_full_pipeline",
    default_args=default_args,
    description=(
        "V11: dispatch one 5-partition crawler job topic -> direct/proxy consumer group "
        "-> result Kafka -> MongoDB raw_recipes -> V10 ETL -> MySQL"
    ),
    start_date=datetime(2026, 9, 15),
    schedule_interval=DAG_SCHEDULE,
    catchup=False,
    max_active_runs=1,
    tags=["recipe", "v11", "crawler", "proxy", "kafka", "mongodb", "mysql", "etl"],
) as dag:
    dispatch_task = PythonOperator(
        task_id="dispatch_crawler_jobs",
        python_callable=dispatch_crawler_jobs,
    )

    wait_crawler_task = PythonSensor(
        task_id="wait_crawler_jobs",
        python_callable=crawler_jobs_are_drained,
        poke_interval=30,
        timeout=172800,
        mode="reschedule",
    )

    wait_mongo_task = PythonSensor(
        task_id="wait_mongodb_writer",
        python_callable=mongodb_writer_is_synced,
        poke_interval=15,
        timeout=21600,
        mode="reschedule",
    )

    pipeline_task = BashOperator(
        task_id="run_recipe_pipeline",
        bash_command="cd /workspace/python && python scripts/10_pipeline.py",
    )

    check_task = PythonOperator(
        task_id="check_pipeline_result",
        python_callable=check_pipeline_result,
    )

    dispatch_task >> wait_crawler_task >> wait_mongo_task >> pipeline_task >> check_task
