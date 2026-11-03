"""Unit tests for services/gateway/bootstrap_topics.py with a mocked AdminClient."""

from __future__ import annotations

import importlib.util
import sys
from concurrent.futures import Future
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest
from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import NewTopic

SCRIPT = Path(__file__).resolve().parents[2] / "services" / "gateway" / "bootstrap_topics.py"
DAY_MS = 86_400_000


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bootstrap_topics", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bt = _load()


def _ok() -> Future[None]:
    f: Future[None] = Future()
    f.set_result(None)
    return f


def _fail(code: int) -> Future[None]:
    f: Future[None] = Future()
    f.set_exception(KafkaException(KafkaError(code)))
    return f


def _admin(outcomes: dict[str, Future[None]]) -> mock.MagicMock:
    admin = mock.MagicMock()
    admin.create_topics.return_value = outcomes
    return admin


def _by_name(topics: list[NewTopic]) -> dict[str, NewTopic]:
    return {t.topic: t for t in topics}


def test_topic_configs_default() -> None:
    topics = _by_name(bt.build_topics(3))
    assert set(topics) == {"api.events", "straph.alerts", "straph.deadletter"}
    assert topics["api.events"].num_partitions == 3
    assert topics["api.events"].config == {"retention.ms": str(7 * DAY_MS)}
    assert topics["straph.alerts"].num_partitions == 3
    assert topics["straph.alerts"].config == {"retention.ms": str(14 * DAY_MS)}
    assert topics["straph.deadletter"].config == {"retention.ms": str(30 * DAY_MS)}


def test_partitions_from_config() -> None:
    topics = _by_name(bt.build_topics(12))
    assert topics["api.events"].num_partitions == 12
    assert topics["straph.alerts"].num_partitions == 12


def test_invalid_partitions_rejected() -> None:
    with pytest.raises(ValueError):
        bt.build_topics(0)


def test_ensure_creates_all() -> None:
    topics = bt.build_topics(3)
    admin = _admin({t.topic: _ok() for t in topics})
    assert bt.ensure_topics(admin, topics) == {t.topic: True for t in topics}
    admin.create_topics.assert_called_once()


def test_ensure_idempotent_when_topics_exist() -> None:
    topics = bt.build_topics(3)
    admin = _admin({t.topic: _fail(KafkaError.TOPIC_ALREADY_EXISTS) for t in topics})
    assert bt.ensure_topics(admin, topics) == {t.topic: False for t in topics}


def test_ensure_mixed_existing_and_new() -> None:
    topics = bt.build_topics(3)
    outcomes = {t.topic: _ok() for t in topics}
    outcomes["api.events"] = _fail(KafkaError.TOPIC_ALREADY_EXISTS)
    result = bt.ensure_topics(_admin(outcomes), topics)
    assert result["api.events"] is False
    assert result["straph.alerts"] is True


def test_ensure_reraises_other_errors() -> None:
    topics = bt.build_topics(3)
    admin = _admin({t.topic: _fail(KafkaError.INVALID_CONFIG) for t in topics})
    with pytest.raises(KafkaException):
        bt.ensure_topics(admin, topics)


def test_main_defaults_and_env() -> None:
    admin = _admin({})
    with mock.patch.object(bt, "AdminClient", return_value=admin) as cls:
        assert bt.main({}) == 0
        cls.assert_called_with({"bootstrap.servers": "localhost:9092"})
        names = _by_name(admin.create_topics.call_args.args[0])
        assert names["api.events"].num_partitions == 3

        env = {"KAFKA_BOOTSTRAP_SERVERS": "k:1", "KAFKA_PARTITIONS": "6"}
        assert bt.main(env) == 0
        cls.assert_called_with({"bootstrap.servers": "k:1"})
        names = _by_name(admin.create_topics.call_args.args[0])
        assert names["straph.alerts"].num_partitions == 6


def test_main_bad_partitions_exit_2() -> None:
    assert bt.main({"KAFKA_PARTITIONS": "abc"}) == 2
    assert bt.main({"KAFKA_PARTITIONS": "0"}) == 2


def test_main_kafka_failure_exit_1() -> None:
    admin = mock.MagicMock()
    admin.create_topics.side_effect = KafkaException(KafkaError(KafkaError._TRANSPORT))
    with mock.patch.object(bt, "AdminClient", return_value=admin):
        assert bt.main({}) == 1
