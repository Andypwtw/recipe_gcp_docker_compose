"""每 14 天先比對萬家福食材價格，再重建食譜營養資料。"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator


TAIWAN_TIMEZONE = pendulum.timezone("Asia/Taipei")

DEFAULT_ARGS = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
}


with DAG(
    dag_id="wanjia_price_update",
    description="每 14 天比對萬家福營養價格，成功後重建食譜營養資料",
    default_args=DEFAULT_ARGS,
    start_date=pendulum.datetime(2026, 10, 16, tz=TAIWAN_TIMEZONE),
    schedule_interval=timedelta(days=14),
    catchup=False,
    max_active_runs=1,
    tags=["price", "wanjia", "nutrition", "recipe"],
) as dag:
    compare_wanjia_nutrition = BashOperator(
        task_id="compare_wanjia_nutrition",
        bash_command="""
            set -euo pipefail
            cd /workspace/python/Price
            python wanjia_ytower_nutrition_comparison.py
        """,
    )

    rebuild_recipe_nutrition = BashOperator(
        task_id="rebuild_recipe_nutrition",
        bash_command="""
            set -euo pipefail
            cd /workspace/python
            python scripts/10_pipeline.py
        """,
    )

    compare_wanjia_nutrition >> rebuild_recipe_nutrition
