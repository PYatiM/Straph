"""Idempotently create Straph Kafka topics using the confluent-kafka admin client."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping

from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"
DEFAULT_PARTITIONS = 3  # Decision D8
DEFAULT_REPLICATION_FACTOR = 1
OPERATION_TIMEOUT_S = 30.0

DAY_MS = 24 * 60 * 60 * 1000

TOPIC_EVENTS = "api.events"
TOPIC_ALERTS = "straph.alerts"
TOPIC_DEADLETTER = "straph.deadletter"

log = logging.getLogger("straph.bootstrap_topics")


def build_topics(
    partitions: int, replication_factor: int = DEFAULT_REPLICATION_FACTOR
) -> list[NewTopic]:
    """Return the topic definitions; deadletter uses a single partition."""
    if partitions < 1:
        raise ValueError("KAFKA_PARTITIONS must be >= 1")
    specs: list[tuple[str, int, int]] = [
        (TOPIC_EVENTS, partitions, 7),
        (TOPIC_ALERTS, partitions, 14),
        (TOPIC_DEADLETTER, 1, 30),
    ]
    return [
        NewTopic(
            name,
            num_partitions=parts,
            replication_factor=replication_factor,
            config={"retention.ms": str(days * DAY_MS)},
        )
        for name, parts, days in specs
    ]


def ensure_topics(admin: AdminClient, topics: list[NewTopic]) -> dict[str, bool]:
    """Create topics; return {name: created}. Existing topics are left untouched."""
    futures = admin.create_topics(topics, operation_timeout=OPERATION_TIMEOUT_S)
    results: dict[str, bool] = {}
    for name, future in futures.items():
        try:
            future.result()
            results[name] = True
            log.info("created topic %s", name)
        except KafkaException as exc:
            err = exc.args[0]
            if err.code() == KafkaError.TOPIC_ALREADY_EXISTS:
                results[name] = False
                log.info("topic %s already exists", name)
            else:
                raise
    return results


def main(env: Mapping[str, str] | None = None) -> int:
    """Read configuration from the environment and bootstrap topics."""
    e = os.environ if env is None else env
    servers = e.get("KAFKA_BOOTSTRAP_SERVERS", DEFAULT_BOOTSTRAP_SERVERS)
    try:
        partitions = int(e.get("KAFKA_PARTITIONS", str(DEFAULT_PARTITIONS)))
        topics = build_topics(partitions)
    except ValueError as exc:
        print(f"invalid configuration: {exc}", file=sys.stderr)
        return 2
    try:
        ensure_topics(AdminClient({"bootstrap.servers": servers}), topics)
    except KafkaException as exc:
        print(f"topic creation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
