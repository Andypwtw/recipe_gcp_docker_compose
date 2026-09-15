from __future__ import annotations

import os

from crawler.kafka_producer import (
    create_producer,
    publish_job,
)
from crawler.ytower_crawler import build_crawl_jobs


DEFAULT_JOB_TOPICS = [
    "crawler_jobs_ip1",
    "crawler_jobs_ip2",
    "crawler_jobs_ip3",
    "crawler_jobs_ip4",
]


def get_job_topics() -> list[str]:
    return [
        os.getenv(
            f"KAFKA_JOB_TOPIC_{index}",
            DEFAULT_JOB_TOPICS[index - 1],
        )
        for index in range(1, 5)
    ]


def select_topic(
    index: int,
    topics: list[str],
) -> str:
    if not topics:
        raise ValueError("topics must not be empty")
    return topics[index % len(topics)]


def dispatch_jobs() -> dict:
    jobs = build_crawl_jobs()
    topics = get_job_topics()

    producer = create_producer()
    counts = {topic: 0 for topic in topics}

    try:
        for index, job in enumerate(jobs):
            if not isinstance(job, dict):
                raise TypeError(
                    f"crawl job #{index} is not a dict"
                )

            topic = select_topic(
                index,
                topics,
            )

            publish_job(
                producer,
                topic,
                job,
            )
            counts[topic] += 1

        producer.flush()
    finally:
        producer.close()

    total = sum(counts.values())
    print(
        f"Dispatched {total} crawler jobs: "
        f"{counts}"
    )

    return {
        "total": total,
        "topics": counts,
    }


if __name__ == "__main__":
    dispatch_jobs()
