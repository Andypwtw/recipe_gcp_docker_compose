"""每 14 天執行價格更新，成功後接續食譜資料處理 pipeline。"""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator


TAIPEI_TZ = pendulum.timezone("Asia/Taipei")

default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


with DAG(
    dag_id="price_scripts_pipeline",
    description="每 14 天更新 MOA 價格，成功後執行 scripts/10_pipeline.py",
    default_args=default_args,
    schedule=timedelta(days=14),
    start_date=pendulum.datetime(2026, 9, 22, 0, 0, tz=TAIPEI_TZ),
    catchup=False,
    max_active_runs=1,
    tags=["price", "scripts", "14days"],
) as dag:

    price_pipeline = BashOperator(
        task_id="price_09_pipeline",
        bash_command="""
        set -euo pipefail
        cd /workspace/python
        python /workspace/python/price/09_pipeline.py
        """,
    )

    scripts_pipeline = BashOperator(
        task_id="scripts_10_pipeline",
        bash_command="""
        set -euo pipefail
        cd /workspace/python
        python /workspace/python/scripts/10_pipeline.py
        """,
    )

    price_pipeline >> scripts_pipeline
