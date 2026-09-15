from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, ShortCircuitOperator
from airflow.sensors.python import PythonSensor
from kafka import KafkaConsumer, KafkaProducer, TopicPartition
from pymongo import MongoClient

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
JOB_TOPIC = os.getenv("KAFKA_JOB_TOPIC", "crawler_jobs")
RESULT_TOPIC = os.getenv("KAFKA_RESULT_TOPIC", "ytower_recipe_results")
CRAWLER_GROUP = "ytower-crawler-group"
MONGO_WRITER_GROUP = "ytower-mongo-writer"
MAX_SEQ_NUMBER = int(os.getenv("MAX_SEQ_NUMBER", "5000"))
MAX_NOT_FOUND_LIMIT = int(os.getenv("MAX_NOT_FOUND_LIMIT", "50"))
UPDATE_INTERVAL_DAYS = int(os.getenv("YTOWER_UPDATE_INTERVAL_DAYS", "7"))
CHECKPOINT_OVERLAP_DAYS = int(os.getenv("YTOWER_CHECKPOINT_OVERLAP_DAYS", "1"))
SEARCH_MAX_PAGES = int(os.getenv("YTOWER_SEARCH_MAX_PAGES", "100"))
DAG_SCHEDULE = os.getenv("YTOWER_DAG_SCHEDULE", "@daily").strip() or None

MONGO_HOST = os.getenv("MONGO_HOST", "mongodb")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "recipe_ai")
MONGO_APP_USER = os.environ["MONGO_APP_USER"]
MONGO_APP_PASSWORD = os.environ["MONGO_APP_PASSWORD"]
STATE_COLLECTION = "crawl_state"
STATE_ID = "ytower_recipe_crawler"


def utcnow():
    return datetime.now(timezone.utc)


def mongo_client():
    uri = (
        f"mongodb://{quote_plus(MONGO_APP_USER)}:{quote_plus(MONGO_APP_PASSWORD)}"
        f"@{MONGO_HOST}:{MONGO_PORT}/{MONGO_DATABASE}?authSource={MONGO_DATABASE}"
    )
    return MongoClient(uri, serverSelectionTimeoutMS=10000)


def get_state():
    client = mongo_client()
    try:
        return client[MONGO_DATABASE][STATE_COLLECTION].find_one({"_id": STATE_ID}) or {}
    finally:
        client.close()


def pipeline_is_due() -> bool:
    state = get_state()
    if not state.get("initial_full_completed"):
        print("No successful FULL checkpoint yet: FULL crawl is due")
        return True
    checkpoint = state.get("last_successful_checkpoint")
    if not checkpoint:
        return True
    if checkpoint.tzinfo is None:
        checkpoint = checkpoint.replace(tzinfo=timezone.utc)
    due_at = checkpoint + timedelta(days=UPDATE_INTERVAL_DAYS)
    due = utcnow() >= due_at
    print(f"checkpoint={checkpoint.isoformat()} due_at={due_at.isoformat()} due={due}")
    return due


def generate_prefixes(letters="ABCDEFGHI", num1_range=(1, 10)):
    return [f"{letter}{n:02d}" for letter in letters for n in range(num1_range[0], num1_range[1] + 1)]


def topic_end_offset(topic: str) -> int:
    consumer = KafkaConsumer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS, enable_auto_commit=False)
    try:
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return -1
        tps = [TopicPartition(topic, p) for p in sorted(partitions)]
        consumer.assign(tps)
        return sum(consumer.end_offsets(tps).values())
    finally:
        consumer.close()


def consumer_group_lag(topic: str, group_id: str):
    consumer = KafkaConsumer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS, group_id=group_id, enable_auto_commit=False)
    try:
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return 0, -1
        tps = [TopicPartition(topic, p) for p in sorted(partitions)]
        consumer.assign(tps)
        ends = consumer.end_offsets(tps)
        total_end = total_lag = 0
        for tp in tps:
            committed = consumer.committed(tp) or 0
            end = ends[tp]
            total_end += end
            total_lag += max(end - committed, 0)
        return total_lag, total_end
    finally:
        consumer.close()


def dispatch_crawler_jobs():
    state = get_state()
    mode = "incremental" if state.get("initial_full_completed") else "full"
    job_baseline = topic_end_offset(JOB_TOPIC)
    result_baseline = topic_end_offset(RESULT_TOPIC)
    if job_baseline < 0 or result_baseline < 0:
        raise RuntimeError("Kafka topics are not ready")

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        acks="all", retries=5,
    )
    dispatched = 0
    try:
        if mode == "full":
            for prefix in generate_prefixes():
                job = {
                    "job_type": "prefix_range", "prefix": prefix,
                    "start_num": 1, "end_num": MAX_SEQ_NUMBER,
                    "max_not_found_limit": MAX_NOT_FOUND_LIMIT,
                }
                producer.send(JOB_TOPIC, key=prefix.encode(), value=job).get(timeout=30)
                dispatched += 1
        else:
            checkpoint = state["last_successful_checkpoint"]
            if checkpoint.tzinfo is None:
                checkpoint = checkpoint.replace(tzinfo=timezone.utc)
            cutoff = checkpoint - timedelta(days=CHECKPOINT_OVERLAP_DAYS)
            job = {
                "job_type": "incremental_discovery",
                "cutoff_iso": cutoff.isoformat(),
                "search_max_pages": SEARCH_MAX_PAGES,
            }
            producer.send(JOB_TOPIC, key=b"incremental-discovery", value=job).get(timeout=30)
            dispatched = 1
        producer.flush()
    finally:
        producer.close()

    client = mongo_client()
    try:
        client[MONGO_DATABASE][STATE_COLLECTION].update_one(
            {"_id": STATE_ID},
            {"$set": {"last_run_started_at": utcnow(), "mode": mode, "status": "running"},
             "$setOnInsert": {"initial_full_completed": False}},
            upsert=True,
        )
    finally:
        client.close()
    result = {"mode": mode, "dispatched": dispatched, "job_baseline": job_baseline, "result_baseline": result_baseline}
    print(result)
    return result


def crawler_jobs_are_drained(**context):
    dispatch = context["ti"].xcom_pull(task_ids="dispatch_crawler_jobs") or {}
    baseline = int(dispatch.get("job_baseline") or 0)
    dispatched = int(dispatch.get("dispatched") or 0)
    lag, end = consumer_group_lag(JOB_TOPIC, CRAWLER_GROUP)
    # incremental discovery may append seq_list jobs, so only require the original job(s) to have appeared and the final group lag to be zero.
    return dispatched > 0 and end >= baseline + dispatched and lag == 0


def mongodb_writer_is_synced(**context):
    # Zero new recipes is valid for an incremental run. Once crawler_jobs are drained,
    # result-topic lag=0 means every result that did exist has reached MongoDB.
    lag, _ = consumer_group_lag(RESULT_TOPIC, MONGO_WRITER_GROUP)
    return lag == 0


def check_pipeline_result():
    from app.db import get_connection
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM recipe_nutrition_summary")
        row = cur.fetchone()
    if int(row["cnt"] or 0) <= 0:
        raise RuntimeError("recipe_nutrition_summary is empty after pipeline")


def mark_checkpoint_success(**context):
    dispatch = context["ti"].xcom_pull(task_ids="dispatch_crawler_jobs") or {}
    mode = dispatch.get("mode", "unknown")
    now = utcnow()
    client = mongo_client()
    try:
        update = {"last_successful_checkpoint": now, "last_run_completed_at": now, "status": "success", "mode": mode}
        if mode == "full":
            update["initial_full_completed"] = True
            update["initial_full_completed_at"] = now
        client[MONGO_DATABASE][STATE_COLLECTION].update_one({"_id": STATE_ID}, {"$set": update}, upsert=True)
    finally:
        client.close()
    print(f"checkpoint updated only after successful pipeline: {now.isoformat()} mode={mode}")


default_args = {"owner": "airflow", "depends_on_past": False, "email_on_failure": False,
                "email_on_retry": False, "retries": 1, "retry_delay": timedelta(minutes=5)}

with DAG(
    dag_id="recipe_full_pipeline", default_args=default_args,
    description="First FULL crawl; then checkpoint-based incremental discovery every 7 days with 1-day overlap",
    start_date=datetime(2026, 9, 15), schedule_interval=DAG_SCHEDULE, catchup=False, max_active_runs=1,
    tags=["recipe", "crawler", "checkpoint", "incremental", "kafka", "mongodb", "mysql"],
) as dag:
    due = ShortCircuitOperator(task_id="check_update_due", python_callable=pipeline_is_due)
    dispatch = PythonOperator(task_id="dispatch_crawler_jobs", python_callable=dispatch_crawler_jobs)
    wait_crawler = PythonSensor(task_id="wait_crawler_jobs", python_callable=crawler_jobs_are_drained,
                                poke_interval=30, timeout=172800, mode="reschedule")
    wait_mongo = PythonSensor(task_id="wait_mongodb_writer", python_callable=mongodb_writer_is_synced,
                              poke_interval=15, timeout=21600, mode="reschedule")
    pipeline = BashOperator(task_id="run_recipe_pipeline", bash_command="cd /workspace/python && python scripts/10_pipeline.py")
    check = PythonOperator(task_id="check_pipeline_result", python_callable=check_pipeline_result)
    checkpoint = PythonOperator(task_id="mark_checkpoint_success", python_callable=mark_checkpoint_success)
    due >> dispatch >> wait_crawler >> wait_mongo >> pipeline >> check >> checkpoint
