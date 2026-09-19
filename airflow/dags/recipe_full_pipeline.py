from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator, PythonOperator
from airflow.sensors.python import PythonSensor
from airflow.utils.trigger_rule import TriggerRule
from kafka import KafkaConsumer, KafkaProducer, TopicPartition
from pymongo import MongoClient

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
JOB_TOPIC = os.getenv("KAFKA_JOB_TOPIC", "crawler_jobs")
RESULT_TOPIC = os.getenv("KAFKA_RESULT_TOPIC", "ytower_recipe_results")
CRAWLER_GROUP = "ytower-crawler-group"
MONGO_WRITER_GROUP = "ytower-mongo-writer"
MAX_SEQ_NUMBER = int(os.getenv("MAX_SEQ_NUMBER", "5000"))
MAX_NOT_FOUND_LIMIT = int(os.getenv("MAX_NOT_FOUND_LIMIT", "50"))
FULL_CRAWL_CHUNK_SIZE = int(os.getenv("FULL_CRAWL_CHUNK_SIZE", "250"))
UPDATE_INTERVAL_DAYS = int(os.getenv("YTOWER_UPDATE_INTERVAL_DAYS", "7"))
CHECKPOINT_OVERLAP_DAYS = int(os.getenv("YTOWER_CHECKPOINT_OVERLAP_DAYS", "1"))
SEARCH_MAX_PAGES = int(os.getenv("YTOWER_SEARCH_MAX_PAGES", "100"))
DAG_SCHEDULE = os.getenv("YTOWER_DAG_SCHEDULE", "@daily").strip() or None
CRAWLER_WAIT_TIMEOUT_SECONDS = int(os.getenv("CRAWLER_WAIT_TIMEOUT_SECONDS", "604800"))  # 7 days
MONGO_WRITER_WAIT_TIMEOUT_SECONDS = int(os.getenv("MONGO_WRITER_WAIT_TIMEOUT_SECONDS", "86400"))  # 1 day

MONGO_HOST = os.getenv("MONGO_HOST", "mongodb")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "recipe_ai")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "raw_recipes")
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


def _as_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def decide_run_action():
    """Daily scheduler only decides what to do; it does not imply a daily crawl."""
    state = get_state()

    # Crawl already completed but post-processing failed: resume pipeline only.
    if state.get("pipeline_pending"):
        print("Previous crawl is complete but pipeline is pending: resume pipeline only")
        return "resume_pipeline"

    # New state field. Legacy successful deployments are accepted as migrated state.
    full_done = bool(state.get("initial_full_crawl_completed") or state.get("initial_full_completed"))
    if not full_done:
        print("No completed FULL crawl checkpoint: FULL crawl is due")
        return "dispatch_crawler_jobs"

    checkpoint = _as_utc(state.get("last_successful_crawl_at") or state.get("last_successful_checkpoint"))
    if not checkpoint:
        print("No crawl checkpoint timestamp: crawl is due")
        return "dispatch_crawler_jobs"

    due_at = checkpoint + timedelta(days=UPDATE_INTERVAL_DAYS)
    if utcnow() >= due_at:
        print(f"crawl checkpoint={checkpoint.isoformat()} due_at={due_at.isoformat()}: incremental crawl is due")
        return "dispatch_crawler_jobs"

    print(f"crawl checkpoint={checkpoint.isoformat()} due_at={due_at.isoformat()}: nothing to do today")
    return "no_action"


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
    full_done = bool(state.get("initial_full_crawl_completed") or state.get("initial_full_completed"))
    mode = "incremental" if full_done else "full"
    job_baseline = topic_end_offset(JOB_TOPIC)
    result_baseline = topic_end_offset(RESULT_TOPIC)
    if job_baseline < 0 or result_baseline < 0:
        raise RuntimeError("Kafka topics are not ready")

    client = mongo_client()
    try:
        raw_count_baseline = client[MONGO_DATABASE][MONGO_COLLECTION].count_documents({})
    finally:
        client.close()

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        acks="all", retries=5,
    )
    dispatched = 0
    try:
        if mode == "full":
            for prefix in generate_prefixes():
                first_end = min(FULL_CRAWL_CHUNK_SIZE, MAX_SEQ_NUMBER)
                job = {
                    "job_type": "prefix_chunk", "prefix": prefix, "start_num": 1,
                    "end_num": first_end, "max_seq_number": MAX_SEQ_NUMBER,
                    "chunk_size": FULL_CRAWL_CHUNK_SIZE,
                    "max_not_found_limit": MAX_NOT_FOUND_LIMIT, "consecutive_not_found": 0,
                }
                producer.send(JOB_TOPIC, key=prefix.encode(), value=job).get(timeout=30)
                dispatched += 1
        else:
            checkpoint = _as_utc(state.get("last_successful_crawl_at") or state.get("last_successful_checkpoint"))
            cutoff = checkpoint - timedelta(days=CHECKPOINT_OVERLAP_DAYS)
            job = {"job_type": "incremental_discovery", "cutoff_iso": cutoff.isoformat(), "search_max_pages": SEARCH_MAX_PAGES}
            producer.send(JOB_TOPIC, key=b"incremental-discovery", value=job).get(timeout=30)
            dispatched = 1
        producer.flush()
    finally:
        producer.close()

    now = utcnow()
    client = mongo_client()
    try:
        client[MONGO_DATABASE][STATE_COLLECTION].update_one(
            {"_id": STATE_ID},
            {"$set": {
                "last_run_started_at": now, "mode": mode, "status": "crawling",
                "pipeline_pending": False, "raw_count_baseline": raw_count_baseline,
            }, "$setOnInsert": {"initial_full_crawl_completed": False}},
            upsert=True,
        )
    finally:
        client.close()

    result = {
        "mode": mode, "dispatched": dispatched, "job_baseline": job_baseline,
        "result_baseline": result_baseline, "raw_count_baseline": raw_count_baseline,
    }
    print(result)
    return result


def crawler_jobs_are_drained(**context):
    dispatch = context["ti"].xcom_pull(task_ids="dispatch_crawler_jobs") or {}
    baseline = int(dispatch.get("job_baseline") or 0)
    dispatched = int(dispatch.get("dispatched") or 0)
    lag, end = consumer_group_lag(JOB_TOPIC, CRAWLER_GROUP)
    return dispatched > 0 and end >= baseline + dispatched and lag == 0


def mongodb_writer_is_synced(**context):
    lag, _ = consumer_group_lag(RESULT_TOPIC, MONGO_WRITER_GROUP)
    return lag == 0


def mark_crawl_completed(**context):
    dispatch = context["ti"].xcom_pull(task_ids="dispatch_crawler_jobs") or {}
    mode = dispatch.get("mode", "unknown")
    baseline = int(dispatch.get("raw_count_baseline") or 0)
    now = utcnow()

    client = mongo_client()
    try:
        db = client[MONGO_DATABASE]
        current_count = db[MONGO_COLLECTION].count_documents({})
        new_count = max(current_count - baseline, 0)
        update = {
            "last_successful_crawl_at": now,
            "last_crawl_completed_at": now,
            "last_new_recipe_count": new_count,
            "pipeline_pending": (mode == "full" or new_count > 0),
            "status": "pipeline_pending" if (mode == "full" or new_count > 0) else "crawl_success_no_new_recipes",
            "mode": mode,
        }
        if mode == "full":
            update["initial_full_crawl_completed"] = True
            update["initial_full_crawl_completed_at"] = now
        db[STATE_COLLECTION].update_one({"_id": STATE_ID}, {"$set": update}, upsert=True)
    finally:
        client.close()

    result = {"mode": mode, "new_recipe_count": new_count, "completed_at": now.isoformat()}
    print(f"crawl completed independently from pipeline: {result}")
    return result


def choose_after_crawl(**context):
    crawl = context["ti"].xcom_pull(task_ids="mark_crawl_completed") or {}
    mode = crawl.get("mode")
    new_count = int(crawl.get("new_recipe_count") or 0)
    if mode == "full" or new_count > 0:
        print(f"mode={mode}, new_recipe_count={new_count}: run post-processing pipeline")
        return "pipeline_after_crawl"
    print("incremental crawl found 0 new recipes: skip post-processing pipeline")
    return "mark_no_new_recipes"


def mark_no_new_recipes():
    now = utcnow()
    client = mongo_client()
    try:
        client[MONGO_DATABASE][STATE_COLLECTION].update_one(
            {"_id": STATE_ID},
            {"$set": {
                "last_run_completed_at": now, "status": "success_no_new_recipes",
                "pipeline_pending": False, "last_new_recipe_count": 0,
            }}, upsert=True,
        )
    finally:
        client.close()
    print(f"incremental crawl completed with 0 new recipes; pipeline skipped at {now.isoformat()}")


def check_pipeline_result():
    from app.db import get_connection
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM recipe_nutrition_summary")
        row = cur.fetchone()
    if int(row["cnt"] or 0) <= 0:
        raise RuntimeError("recipe_nutrition_summary is empty after pipeline")


def mark_pipeline_success():
    now = utcnow()
    state = get_state()
    mode = state.get("mode", "unknown")
    client = mongo_client()
    try:
        update = {
            "last_successful_checkpoint": now,
            "last_pipeline_completed_at": now,
            "last_run_completed_at": now,
            "pipeline_pending": False,
            "status": "success",
            "mode": mode,
        }
        # Keep the legacy field for compatibility with older tooling.
        if mode == "full":
            update["initial_full_completed"] = True
            update["initial_full_completed_at"] = now
        client[MONGO_DATABASE][STATE_COLLECTION].update_one({"_id": STATE_ID}, {"$set": update}, upsert=True)
    finally:
        client.close()
    print(f"pipeline checkpoint updated: {now.isoformat()} mode={mode}")


default_args = {
    "owner": "airflow", "depends_on_past": False, "email_on_failure": False,
    "email_on_retry": False, "retries": 1, "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="recipe_full_pipeline",
    default_args=default_args,
    description="Daily state check; one FULL crawl, then 7-day incremental crawls; failed pipeline resumes without re-crawling",
    start_date=datetime(2026, 9, 15),
    schedule_interval=DAG_SCHEDULE,
    catchup=False,
    max_active_runs=1,
    tags=["recipe", "crawler", "checkpoint", "incremental", "kafka", "mongodb", "mysql"],
) as dag:
    decide = BranchPythonOperator(task_id="decide_run_action", python_callable=decide_run_action)
    no_action = EmptyOperator(task_id="no_action")
    resume_pipeline = EmptyOperator(task_id="resume_pipeline")

    dispatch = PythonOperator(task_id="dispatch_crawler_jobs", python_callable=dispatch_crawler_jobs)
    wait_crawler = PythonSensor(
        task_id="wait_crawler_jobs", python_callable=crawler_jobs_are_drained,
        poke_interval=30, timeout=CRAWLER_WAIT_TIMEOUT_SECONDS, mode="reschedule",
    )
    wait_mongo = PythonSensor(
        task_id="wait_mongodb_writer", python_callable=mongodb_writer_is_synced,
        poke_interval=15, timeout=MONGO_WRITER_WAIT_TIMEOUT_SECONDS, mode="reschedule",
    )
    mark_crawl = PythonOperator(task_id="mark_crawl_completed", python_callable=mark_crawl_completed)
    after_crawl = BranchPythonOperator(task_id="check_new_recipes", python_callable=choose_after_crawl)
    pipeline_after_crawl = EmptyOperator(task_id="pipeline_after_crawl")
    no_new = PythonOperator(task_id="mark_no_new_recipes", python_callable=mark_no_new_recipes)

    pipeline = BashOperator(
        task_id="run_recipe_pipeline",
        bash_command="cd /workspace/python && python scripts/10_pipeline.py",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )
    check = PythonOperator(task_id="check_pipeline_result", python_callable=check_pipeline_result)
    checkpoint = PythonOperator(task_id="mark_checkpoint_success", python_callable=mark_pipeline_success)

    decide >> no_action
    decide >> dispatch >> wait_crawler >> wait_mongo >> mark_crawl >> after_crawl
    after_crawl >> no_new
    after_crawl >> pipeline_after_crawl
    decide >> resume_pipeline
    [resume_pipeline, pipeline_after_crawl] >> pipeline >> check >> checkpoint
