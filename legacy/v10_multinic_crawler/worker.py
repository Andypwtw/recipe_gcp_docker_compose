from __future__ import annotations

import json
import os
import socket

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager

from crawler.kafka_producer import (
    create_producer,
    publish_recipe,
)
from crawler.ytower_crawler import crawl_job


class SourceAddressAdapter(HTTPAdapter):
    """
    Bind urllib3 connections to a specific source/private IP.

    On the GCP Linux VM, policy routing rules map that source IP to
    the corresponding NIC and Cloud NAT public egress IP.
    """

    def __init__(
        self,
        source_ip: str,
        *args,
        **kwargs,
    ):
        self.source_ip = source_ip
        super().__init__(*args, **kwargs)

    def init_poolmanager(
        self,
        connections,
        maxsize,
        block=False,
        **pool_kwargs,
    ):
        pool_kwargs["source_address"] = (
            self.source_ip,
            0,
        )
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )


def create_source_bound_session(
    source_ip: str,
) -> requests.Session:
    try:
        socket.inet_aton(source_ip)
    except OSError as exc:
        raise ValueError(
            f"invalid PRIVATE_IP: {source_ip}"
        ) from exc

    session = requests.Session()
    adapter = SourceAddressAdapter(source_ip)

    session.mount(
        "http://",
        adapter,
    )
    session.mount(
        "https://",
        adapter,
    )

    session.headers.update(
        {
            "User-Agent": os.getenv(
                "CRAWLER_USER_AGENT",
                "recipe-collections-v9/1.0",
            )
        }
    )

    return session


def main():
    from kafka import KafkaConsumer

    worker_id = os.getenv(
        "WORKER_ID",
        "1",
    )
    source_ip = os.environ["PRIVATE_IP"]
    job_topic = os.environ["KAFKA_JOB_TOPIC"]

    result_topic = os.getenv(
        "KAFKA_RESULT_TOPIC",
        "ytower-recipes",
    )

    bootstrap = os.getenv(
        "KAFKA_BOOTSTRAP_SERVERS",
        "127.0.0.1:9094",
    )

    group_id = os.getenv(
        "KAFKA_JOB_GROUP_ID",
        f"crawler-worker-{worker_id}",
    )

    timeout = int(
        os.getenv(
            "CRAWLER_REQUEST_TIMEOUT",
            "30",
        )
    )

    session = create_source_bound_session(
        source_ip,
    )

    consumer = KafkaConsumer(
        job_topic,
        bootstrap_servers=bootstrap,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda b: json.loads(
            b.decode("utf-8")
        ),
    )

    producer = create_producer(
        bootstrap_servers=bootstrap,
    )

    print(
        f"crawler worker {worker_id} started "
        f"topic={job_topic} source_ip={source_ip} "
        f"group={group_id}",
        flush=True,
    )

    try:
        for message in consumer:
            job = message.value

            # crawl_job must use the provided source-bound session.
            recipe = crawl_job(
                job,
                session,
            )

            if not isinstance(recipe, dict):
                raise TypeError(
                    "crawl_job must return dict"
                )

            # Wait for Kafka ack BEFORE committing the job offset.
            future = publish_recipe(
                producer,
                recipe,
                topic=result_topic,
            )
            future.get(timeout=timeout)

            consumer.commit()

            print(
                f"worker={worker_id} "
                f"job_topic={message.topic} "
                f"partition={message.partition} "
                f"offset={message.offset} "
                f"seq={recipe.get('SEQ')}",
                flush=True,
            )
    finally:
        producer.flush()
        producer.close()
        consumer.close()
        session.close()


if __name__ == "__main__":
    main()
