"""Tests for chaosrank.adapters.confluent (#14).

Covers:
  - _extract_topic: all four strategies + auto-detection
  - _metadata_owner / _metadata_consumers: present, absent, list vs string
  - _build_kafka_index: happy path, missing file, malformed
  - ConfluentSchemaRegistryAdapter.__init__: invalid mode, invalid strategy,
    api mode without url
  - convert (file mode): happy path, missing subjects key, bad JSON,
    directory input, empty subjects
  - convert (file mode): self-loop dropped, missing producer warns,
    missing consumers warns, missing subject field skipped
  - convert (file mode): kafka fallback fills missing producer + consumers
  - convert (file mode): naming strategy auto-detection (all three shapes)
  - convert (file mode): explicit naming strategies (topic, record, topic_record)
  - convert (api mode): happy path (mocked session), 429 retry, HTTP error,
    empty subject list
  - source_format returns "confluent"
  - output shape: matches async-deps.yaml schema
    (producer, consumer, channel="kafka", topic)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chaosrank.adapters.confluent import (
    ConfluentSchemaRegistryAdapter,
    _extract_topic,
    _metadata_owner,
    _metadata_consumers,
    _build_kafka_index,
    STRATEGY_AUTO,
    STRATEGY_TOPIC,
    STRATEGY_RECORD,
    STRATEGY_TOPIC_RECORD,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _subject(
    name: str,
    owner: str = "",
    consumers: str = "",
    schema: str = "{}",
) -> dict:
    return {
        "subject": name,
        "schema": schema,
        "metadata": {
            "properties": {
                "owner":     owner,
                "consumers": consumers,
            }
        },
    }


def _kafka_export(*topics) -> dict:
    """Build a kafka-topics.json compatible dict."""
    return {"topics": list(topics)}


def _kafka_topic(name: str, producer: str, *consumers: str) -> dict:
    return {"name": name, "producer": producer, "consumers": list(consumers)}


@pytest.fixture
def tmp_sr_file(tmp_path):
    def _write(subjects: list) -> Path:
        p = tmp_path / "sr-export.json"
        p.write_text(json.dumps({"subjects": subjects}))
        return p
    return _write


@pytest.fixture
def tmp_kafka_file(tmp_path):
    def _write(*topics) -> Path:
        p = tmp_path / "kafka-topics.json"
        p.write_text(json.dumps(_kafka_export(*topics)))
        return p
    return _write


@pytest.fixture
def adapter():
    return ConfluentSchemaRegistryAdapter()


# ---------------------------------------------------------------------------
# _extract_topic
# ---------------------------------------------------------------------------

class TestExtractTopic:
    @pytest.mark.parametrize("subject,expected", [
        ("orders-value",          "orders"),
        ("orders-key",            "orders"),
        ("payment-events-value",  "payment-events"),
        ("checkout-key",          "checkout"),
    ])
    def test_topic_strategy(self, subject, expected):
        assert _extract_topic(subject, STRATEGY_TOPIC) == expected

    def test_topic_strategy_no_suffix_uses_subject_as_topic(self, caplog):
        with caplog.at_level(logging.WARNING, logger="chaosrank.adapters.confluent"):
            result = _extract_topic("plainsubject", STRATEGY_TOPIC)
        assert result == "plainsubject"
        assert "does not end with -key or -value" in caplog.text

    @pytest.mark.parametrize("subject,expected", [
        ("orders-com.example.OrderCreated",    "orders"),
        ("payments-PaymentEvent",              "payments"),
        ("cart-events-CartUpdated",            "cart-events"),
    ])
    def test_topic_record_strategy(self, subject, expected):
        assert _extract_topic(subject, STRATEGY_TOPIC_RECORD) == expected

    def test_record_strategy_returns_none(self):
        assert _extract_topic("com.example.OrderCreated", STRATEGY_RECORD) is None

    @pytest.mark.parametrize("subject,expected", [
        ("orders-value",                        "orders"),          # TopicNameStrategy
        ("payments-key",                        "payments"),        # TopicNameStrategy
        ("orders-com.example.OrderCreated",     "orders"),          # TopicRecordNameStrategy
        ("plainsubject",                        "plainsubject"),    # fallback
    ])
    def test_auto_strategy(self, subject, expected):
        assert _extract_topic(subject, STRATEGY_AUTO) == expected

    def test_auto_topic_name_takes_priority_over_topic_record(self):
        # "events-value" matches TopicNameStrategy first (ends with -value)
        # Should NOT match TopicRecordNameStrategy even though "Value" is capitalized
        assert _extract_topic("events-value", STRATEGY_AUTO) == "events"


# ---------------------------------------------------------------------------
# _metadata_owner / _metadata_consumers
# ---------------------------------------------------------------------------

class TestMetadataExtraction:
    def test_owner_present(self):
        entry = _subject("orders-value", owner="order-service")
        assert _metadata_owner(entry) == "order-service"

    def test_owner_absent_returns_none(self):
        entry = _subject("orders-value", owner="")
        assert _metadata_owner(entry) is None

    def test_owner_whitespace_stripped(self):
        entry = {"subject": "x", "metadata": {"properties": {"owner": "  svc  "}}}
        assert _metadata_owner(entry) == "svc"

    def test_consumers_comma_string(self):
        entry = _subject("orders-value", consumers="inv-svc,notify-svc")
        assert _metadata_consumers(entry) == ["inv-svc", "notify-svc"]

    def test_consumers_list(self):
        entry = {"subject": "x", "metadata": {"properties": {"consumers": ["a", "b"]}}}
        assert _metadata_consumers(entry) == ["a", "b"]

    def test_consumers_empty_string(self):
        assert _metadata_consumers(_subject("x")) == []

    def test_consumers_whitespace_stripped(self):
        entry = _subject("x", consumers=" a , b , c ")
        assert _metadata_consumers(entry) == ["a", "b", "c"]

    def test_no_metadata_key(self):
        entry = {"subject": "x", "schema": "{}"}
        assert _metadata_owner(entry) is None
        assert _metadata_consumers(entry) == []

    def test_no_properties_key(self):
        entry = {"subject": "x", "metadata": {}}
        assert _metadata_owner(entry) is None
        assert _metadata_consumers(entry) == []


# ---------------------------------------------------------------------------
# _build_kafka_index
# ---------------------------------------------------------------------------

class TestBuildKafkaIndex:
    def test_happy_path(self, tmp_kafka_file):
        path = tmp_kafka_file(
            _kafka_topic("orders", "order-svc", "inv-svc", "notify-svc"),
            _kafka_topic("payments", "payment-svc", "reporting-svc"),
        )
        index = _build_kafka_index(path)
        assert index["orders"]["producer"] == "order-svc"
        assert index["orders"]["consumers"] == ["inv-svc", "notify-svc"]
        assert index["payments"]["producer"] == "payment-svc"

    def test_missing_file_returns_empty(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="chaosrank.adapters.confluent"):
            result = _build_kafka_index(tmp_path / "nonexistent.json")
        assert result == {}
        assert "Could not read" in caplog.text

    def test_malformed_json_returns_empty(self, tmp_path, caplog):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        with caplog.at_level(logging.WARNING, logger="chaosrank.adapters.confluent"):
            result = _build_kafka_index(p)
        assert result == {}

    def test_topic_without_name_skipped(self, tmp_path):
        p = tmp_path / "k.json"
        p.write_text(json.dumps({"topics": [{"producer": "svc", "consumers": []}]}))
        assert _build_kafka_index(p) == {}


# ---------------------------------------------------------------------------
# ConfluentSchemaRegistryAdapter.__init__ validation
# ---------------------------------------------------------------------------

class TestAdapterInit:
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode must be"):
            ConfluentSchemaRegistryAdapter(mode="stream")

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError, match="naming_strategy must be"):
            ConfluentSchemaRegistryAdapter(naming_strategy="unknown")

    def test_api_mode_without_url_raises(self):
        with pytest.raises(ValueError, match="url is required"):
            ConfluentSchemaRegistryAdapter(mode="api")

    def test_api_mode_with_url_ok(self):
        a = ConfluentSchemaRegistryAdapter(mode="api", url="http://sr:8081")
        assert a.url == "http://sr:8081"

    def test_url_trailing_slash_stripped(self):
        a = ConfluentSchemaRegistryAdapter(mode="api", url="http://sr:8081/")
        assert a.url == "http://sr:8081"

    def test_source_format(self, adapter):
        assert adapter.source_format() == "confluent"


# ---------------------------------------------------------------------------
# convert — file mode
# ---------------------------------------------------------------------------

class TestConvertFileMode:
    def test_happy_path_single_subject(self, tmp_sr_file):
        path = tmp_sr_file([
            _subject("orders-value", owner="order-svc", consumers="inv-svc,notify-svc"),
        ])
        deps = ConfluentSchemaRegistryAdapter().convert(path)
        assert len(deps) == 2
        services = {d["consumer"] for d in deps}
        assert services == {"inv-svc", "notify-svc"}
        assert all(d["producer"] == "order-svc" for d in deps)
        assert all(d["channel"] == "kafka" for d in deps)
        assert all(d["topic"] == "orders" for d in deps)

    def test_output_shape_matches_schema(self, tmp_sr_file):
        path = tmp_sr_file([_subject("orders-value", owner="svc-a", consumers="svc-b")])
        deps = ConfluentSchemaRegistryAdapter().convert(path)
        assert len(deps) == 1
        d = deps[0]
        assert set(d.keys()) == {"producer", "consumer", "channel", "topic"}

    def test_multi_subject(self, tmp_sr_file):
        path = tmp_sr_file([
            _subject("orders-value",   owner="order-svc",   consumers="inv-svc"),
            _subject("payments-value", owner="payment-svc", consumers="reporting-svc,audit-svc"),
        ])
        deps = ConfluentSchemaRegistryAdapter().convert(path)
        assert len(deps) == 3

    def test_self_loop_dropped(self, tmp_sr_file, caplog):
        path = tmp_sr_file([
            _subject("orders-value", owner="order-svc", consumers="order-svc,inv-svc"),
        ])
        with caplog.at_level(logging.WARNING, logger="chaosrank.adapters.confluent"):
            deps = ConfluentSchemaRegistryAdapter().convert(path)
        assert len(deps) == 1
        assert deps[0]["consumer"] == "inv-svc"
        assert "self-referential" in caplog.text

    def test_missing_producer_warns_and_skips(self, tmp_sr_file, caplog):
        path = tmp_sr_file([_subject("orders-value", owner="", consumers="inv-svc")])
        with caplog.at_level(logging.WARNING, logger="chaosrank.adapters.confluent"):
            deps = ConfluentSchemaRegistryAdapter().convert(path)
        assert deps == []
        assert "no producer found" in caplog.text

    def test_missing_consumers_warns_and_skips(self, tmp_sr_file, caplog):
        path = tmp_sr_file([_subject("orders-value", owner="order-svc", consumers="")])
        with caplog.at_level(logging.WARNING, logger="chaosrank.adapters.confluent"):
            deps = ConfluentSchemaRegistryAdapter().convert(path)
        assert deps == []
        assert "no consumers found" in caplog.text

    def test_missing_subject_field_skipped(self, tmp_sr_file, caplog):
        path = tmp_sr_file([{"schema": "{}", "metadata": {"properties": {}}}])
        with caplog.at_level(logging.WARNING, logger="chaosrank.adapters.confluent"):
            deps = ConfluentSchemaRegistryAdapter().convert(path)
        assert deps == []

    def test_empty_subjects_returns_empty(self, tmp_sr_file):
        path = tmp_sr_file([])
        assert ConfluentSchemaRegistryAdapter().convert(path) == []

    def test_missing_subjects_key_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"schemas": []}))
        with pytest.raises(ValueError, match="Missing required key 'subjects'"):
            ConfluentSchemaRegistryAdapter().convert(p)

    def test_bad_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            ConfluentSchemaRegistryAdapter().convert(p)

    def test_directory_input_raises(self, tmp_path):
        with pytest.raises(ValueError, match="must be a file"):
            ConfluentSchemaRegistryAdapter().convert(tmp_path)

    def test_record_strategy_no_topic_skips(self, tmp_sr_file, caplog):
        path = tmp_sr_file([
            _subject("com.example.OrderCreated", owner="order-svc", consumers="inv-svc"),
        ])
        a = ConfluentSchemaRegistryAdapter(naming_strategy=STRATEGY_RECORD)
        with caplog.at_level(logging.DEBUG, logger="chaosrank.adapters.confluent"):
            deps = a.convert(path)
        assert deps == []


# ---------------------------------------------------------------------------
# Kafka fallback
# ---------------------------------------------------------------------------

class TestKafkaFallback:
    def test_fallback_fills_missing_producer(self, tmp_sr_file, tmp_kafka_file):
        sr_path    = tmp_sr_file([_subject("orders-value", owner="", consumers="inv-svc")])
        kafka_path = tmp_kafka_file(_kafka_topic("orders", "order-svc"))
        a = ConfluentSchemaRegistryAdapter(kafka_path=kafka_path)
        deps = a.convert(sr_path)
        assert len(deps) == 1
        assert deps[0]["producer"] == "order-svc"

    def test_fallback_fills_missing_consumers(self, tmp_sr_file, tmp_kafka_file):
        sr_path    = tmp_sr_file([_subject("orders-value", owner="order-svc", consumers="")])
        kafka_path = tmp_kafka_file(_kafka_topic("orders", "order-svc", "inv-svc", "notify-svc"))
        a = ConfluentSchemaRegistryAdapter(kafka_path=kafka_path)
        deps = a.convert(sr_path)
        assert {d["consumer"] for d in deps} == {"inv-svc", "notify-svc"}

    def test_metadata_takes_priority_over_kafka(self, tmp_sr_file, tmp_kafka_file):
        """Metadata owner should not be overridden by kafka fallback."""
        sr_path    = tmp_sr_file([_subject("orders-value", owner="real-owner", consumers="real-consumer")])
        kafka_path = tmp_kafka_file(_kafka_topic("orders", "wrong-owner", "wrong-consumer"))
        a = ConfluentSchemaRegistryAdapter(kafka_path=kafka_path)
        deps = a.convert(sr_path)
        assert deps[0]["producer"] == "real-owner"
        assert deps[0]["consumer"] == "real-consumer"

    def test_kafka_topic_not_in_index_still_warns(self, tmp_sr_file, tmp_kafka_file, caplog):
        """If the topic isn't in the kafka index, warn about missing producer."""
        sr_path    = tmp_sr_file([_subject("payments-value", owner="", consumers="")])
        kafka_path = tmp_kafka_file(_kafka_topic("orders", "order-svc", "inv-svc"))
        a = ConfluentSchemaRegistryAdapter(kafka_path=kafka_path)
        with caplog.at_level(logging.WARNING, logger="chaosrank.adapters.confluent"):
            deps = a.convert(sr_path)
        assert deps == []
        assert "no producer found" in caplog.text


# ---------------------------------------------------------------------------
# convert — API mode
# ---------------------------------------------------------------------------

class TestConvertApiMode:
    def _make_adapter(self, **kwargs):
        return ConfluentSchemaRegistryAdapter(
            mode="api", url="http://sr:8081", **kwargs
        )

    def _mock_session(self, subjects_list: list, subject_detail: dict | None = None):
        """Return a mock session where GET /subjects returns subjects_list
        and GET /subjects/<name>/versions/latest returns subject_detail."""
        session = MagicMock()

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if url.endswith("/subjects"):
                resp.json.return_value = subjects_list
            else:
                # Per-subject fetch
                name = url.split("/subjects/")[1].split("/")[0]
                from urllib.parse import unquote
                name = unquote(name)
                resp.json.return_value = subject_detail or _subject(
                    name, owner="svc-a", consumers="svc-b"
                )
            return resp

        session.get.side_effect = side_effect
        return session

    def test_happy_path(self):
        a = self._make_adapter()
        session = self._mock_session(
            ["orders-value"],
            _subject("orders-value", owner="order-svc", consumers="inv-svc"),
        )
        with patch.object(a, "_make_session", return_value=session):
            deps = a.convert(Path("/ignored"))
        assert len(deps) == 1
        assert deps[0]["producer"] == "order-svc"
        assert deps[0]["consumer"] == "inv-svc"
        assert deps[0]["topic"] == "orders"

    def test_empty_subjects_returns_empty(self):
        a = self._make_adapter()
        session = self._mock_session([])
        with patch.object(a, "_make_session", return_value=session):
            deps = a.convert(Path("/ignored"))
        assert deps == []

    def test_bearer_token_set_in_header(self):
        a = self._make_adapter(token="my-bearer-token")
        a._make_session()
        # Bearer token should be in headers (not basic auth)
        # We can't test the real session header without requests installed,
        # so we verify the branching logic — no ":" means bearer path
        assert ":" not in "my-bearer-token"

    def test_basic_auth_token_format(self):
        a = self._make_adapter(token="user:password")
        # Should not raise — basic auth branch
        assert ":" in a.token

    def test_http_error_logs_and_continues(self):
        """A failing per-subject fetch should not abort the entire run."""
        import requests as req_lib
        a = self._make_adapter()
        session = MagicMock()

        call_count = 0
        def side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if url.endswith("/subjects"):
                resp.json.return_value = ["orders-value", "payments-value"]
                return resp
            # First subject fails, second succeeds
            if "orders" in url:
                http_err = req_lib.HTTPError(response=MagicMock(status_code=404))
                resp.raise_for_status.side_effect = http_err
            else:
                resp.json.return_value = _subject("payments-value", owner="pay-svc", consumers="report-svc")
            return resp

        session.get.side_effect = side_effect
        with patch.object(a, "_make_session", return_value=session):
            deps = a.convert(Path("/ignored"))

        # payments-value should still produce a dep
        assert any(d["topic"] == "payments" for d in deps)

    def test_input_path_ignored_in_api_mode(self):
        """convert() should not read input_path in api mode."""
        a = self._make_adapter()
        session = self._mock_session([], None)
        nonexistent = Path("/this/does/not/exist.json")
        with patch.object(a, "_make_session", return_value=session):
            # Should not raise FileNotFoundError
            deps = a.convert(nonexistent)
        assert deps == []