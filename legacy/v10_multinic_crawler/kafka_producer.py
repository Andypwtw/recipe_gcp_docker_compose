from __future__ import annotations

import json
import os


def create_producer(bootstrap_servers: str | None = None):
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=(
            bootstrap_servers
            or os.getenv(
                "KAFKA_BOOTSTRAP_SERVERS",
                "kafka:9092",
            )
        ),
        key_serializer=lambda s: str(s).encode("utf-8"),
        value_serializer=lambda obj: json.dumps(
            obj,
            ensure_ascii=False,
        ).encode("utf-8"),
        acks="all",
    )


def publish_job(
    producer,
    topic: str,
    job: dict,
):
    job_id = (
        job.get("job_id")
        or job.get("SEQ")
        or job.get("url")
        or job.get("食譜網址")
    )

    if not job_id:
        raise ValueError(
            "crawler job requires job_id, SEQ, url, or 食譜網址"
        )

    return producer.send(
        topic,
        key=str(job_id),
        value=job,
    )


def publish_recipe(
    producer,
    recipe: dict,
    topic: str | None = None,
):
    seq = recipe.get("SEQ")
    if not seq:
        raise ValueError("recipe missing SEQ")

    result_topic = (
        topic
        or os.getenv(
            "KAFKA_RESULT_TOPIC",
            "ytower-recipes",
        )
    )

    return producer.send(
        result_topic,
        key=str(seq),
        value=recipe,
    )
