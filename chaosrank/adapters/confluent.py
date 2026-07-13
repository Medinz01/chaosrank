"""
Extracts async producer/consumer relationships from Confluent Schema Registry
and converts them to async-deps.yaml format.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from chaosrank.adapters.base import AsyncDepsAdapter

logger = logging.getLogger(__name__)


STRATEGY_AUTO         = "auto"
STRATEGY_TOPIC        = "topic"           # <topic>-key / <topic>-value
STRATEGY_RECORD       = "record"          # com.example.OrderCreated
STRATEGY_TOPIC_RECORD = "topic_record"    # <topic>-com.example.OrderCreated

_VALID_STRATEGIES = (STRATEGY_AUTO, STRATEGY_TOPIC, STRATEGY_RECORD, STRATEGY_TOPIC_RECORD)

_TOPIC_NAME_SUFFIXES = ("-key", "-value")

_TOPIC_RECORD_RE = re.compile(
    r"^(.+)-"
    r"([A-Z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]*)*"
    r"|[a-z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]+)+)"
    r"$"
)



class ConfluentSchemaRegistryAdapter(AsyncDepsAdapter):
    """Convert Confluent Schema Registry subjects to async-deps.yaml entries.

    Args:
        mode:             "file" (default) or "api".
        url:              Schema Registry base URL. Required for mode="api".
        token:            Auth token. Passed as Bearer header, or as "user:pass"
                          for basic auth. Optional.
        naming_strategy:  "auto" | "topic" | "record" | "topic_record".
        kafka_path:       Optional Path to kafka-topics.json for service name
                          fallback when metadata tags are absent.
        timeout:          HTTP timeout in seconds. Default 30.
    """

    def __init__(
        self,
        mode: str = "file",
        url: str | None = None,
        token: str | None = None,
        naming_strategy: str = STRATEGY_AUTO,
        kafka_path: Path | None = None,
        timeout: int = 30,
    ) -> None:
        if mode not in ("file", "api"):
            raise ValueError(f"mode must be 'file' or 'api', got {mode!r}")
        if naming_strategy not in _VALID_STRATEGIES:
            raise ValueError(
                f"naming_strategy must be one of {_VALID_STRATEGIES}, got {naming_strategy!r}"
            )
        if mode == "api" and not url:
            raise ValueError("url is required for mode='api'")

        self.mode             = mode
        self.url              = url.rstrip("/") if url else None
        self.token            = token
        self.naming_strategy  = naming_strategy
        self.kafka_path       = kafka_path
        self._timeout         = timeout
        self._kafka_index: dict[str, dict] | None = None  # topic → {producer, consumers}


    def source_format(self) -> str:
        return "confluent"

    def convert(self, input_path: Path) -> list[dict]:
        """Convert SR subjects to async-deps.yaml dependency list.

        In file mode: reads from input_path.
        In api mode:  queries the SR REST API (input_path is accepted but ignored).
        """
        if self.kafka_path:
            self._kafka_index = _build_kafka_index(self.kafka_path)

        if self.mode == "api":
            subjects = self._fetch_subjects_from_api()
        else:
            subjects = self._load_subjects_from_file(input_path)

        if not subjects:
            logger.warning("No subjects found — output will be empty.")
            return []

        deps = self._build_dependencies(subjects)
        logger.info(
            "Confluent SR adapter: %d subjects → %d dependencies (mode=%s)",
            len(subjects), len(deps), self.mode,
        )
        return deps


    def _load_subjects_from_file(self, path: Path) -> list[dict]:
        if path.is_dir():
            raise ValueError(
                "--input must be a file for --from confluent. "
                "Pass the path to your sr-export.json."
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON in {path}: {e}") from e
        except Exception as e:
            raise ValueError(f"Failed to read {path}: {e}") from e

        if not isinstance(data, dict):
            raise ValueError(
                "Expected a JSON object with a 'subjects' key. "
                "See docs/async-deps-guide.md for the expected format."
            )
        subjects = data.get("subjects")
        if subjects is None:
            raise ValueError("Missing required key 'subjects' in SR export JSON.")
        if not isinstance(subjects, list):
            raise ValueError("'subjects' must be a list.")
        return subjects


    def _fetch_subjects_from_api(self) -> list[dict]:
        """Fetch all subjects + their latest schema + metadata from SR REST API."""
        import requests

        session = self._make_session()

        subject_names = self._get_json(session, f"{self.url}/subjects")
        if not isinstance(subject_names, list):
            raise ValueError(
                f"Expected a list from GET /subjects, got {type(subject_names).__name__}. "
                f"Check that {self.url} is a Schema Registry endpoint."
            )

        if not subject_names:
            logger.warning("Schema Registry returned no subjects.")
            return []

        logger.debug("Found %d subjects in Schema Registry", len(subject_names))

        subjects = []
        for name in subject_names:
            encoded = requests.utils.quote(name, safe="")
            url = f"{self.url}/subjects/{encoded}/versions/latest"
            record = self._get_json(session, url)
            if record is not None:
                record.setdefault("subject", name)
                subjects.append(record)

        return subjects

    def _make_session(self):
        import requests
        session = requests.Session()
        session.headers["Accept"] = "application/vnd.schemaregistry.v1+json"

        if self.token:
            if ":" in self.token:
                # basic auth: user:password
                user, password = self.token.split(":", 1)
                session.auth = (user, password)
            else:
                session.headers["Authorization"] = f"Bearer {self.token}"

        return session

    def _get_json(self, session, url: str) -> dict | list | None:
        """GET url → parsed JSON, or None on error."""
        try:
            resp = session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            import requests
            if isinstance(exc, requests.HTTPError):
                status = exc.response.status_code if exc.response is not None else "?"
                if status == 429:
                    retry = int(
                        exc.response.headers.get("Retry-After", 5)
                        if exc.response is not None else 5
                    )
                    logger.warning("SR API rate-limited — sleeping %ds", retry)
                    time.sleep(retry)
                    return self._get_json(session, url)
                logger.error("SR API HTTP %s on %s", status, url)
            else:
                logger.error("SR API request failed on %s: %s", url, exc)
            return None


    def _build_dependencies(self, subjects: list) -> list[dict]:
        deps = []

        for i, entry in enumerate(subjects):
            if not isinstance(entry, dict):
                logger.warning("Skipping subjects[%d] — not an object: %r", i, entry)
                continue

            subject_name = entry.get("subject", "")
            if not subject_name:
                logger.warning("Skipping subjects[%d] — missing 'subject' field.", i)
                continue

            topic = _extract_topic(subject_name, self.naming_strategy)
            if not topic:
                logger.debug(
                    "Could not extract topic from subject %r (strategy=%s) — skipping.",
                    subject_name, self.naming_strategy,
                )
                continue

            producer, consumers = _extract_services(entry, topic, self._kafka_index)

            if not producer:
                logger.warning(
                    "Subject %r: no producer found. "
                    "Add 'owner' to schema metadata.properties or provide --kafka fallback.",
                    subject_name,
                )
                continue

            if not consumers:
                logger.warning(
                    "Subject %r (topic=%r): no consumers found — skipping. "
                    "Add 'consumers' to schema metadata.properties or provide --kafka fallback.",
                    subject_name, topic,
                )
                continue

            for consumer in consumers:
                if consumer == producer:
                    logger.warning(
                        "Skipping self-referential dependency: %r → %r via topic %r",
                        producer, consumer, topic,
                    )
                    continue
                deps.append({
                    "producer": producer,
                    "consumer": consumer,
                    "channel":  "kafka",
                    "topic":    topic,
                })

        logger.debug("Extracted %d dependencies from %d subjects", len(deps), len(subjects))
        return deps



def _extract_topic(subject: str, strategy: str) -> str | None:
    """Extract topic name from a Schema Registry subject name."""
    if strategy == STRATEGY_TOPIC or strategy == STRATEGY_AUTO:
        for suffix in _TOPIC_NAME_SUFFIXES:
            if subject.endswith(suffix):
                return subject[: -len(suffix)]
        if strategy == STRATEGY_TOPIC:
            # Explicit topic strategy but no suffix — warn and return as-is
            logger.warning(
                "Subject %r does not end with -key or -value but "
                "--naming-strategy topic was set. Using subject name as topic.",
                subject,
            )
            return subject

    if strategy == STRATEGY_TOPIC_RECORD or strategy == STRATEGY_AUTO:
        m = _TOPIC_RECORD_RE.match(subject)
        if m:
            return m.group(1)

    if strategy == STRATEGY_RECORD:
        return None

    if strategy == STRATEGY_AUTO:
        logger.debug(
            "Subject %r: no naming strategy matched — using subject name as topic.", subject
        )
        return subject

    return None



def _extract_services(
    entry: dict,
    topic: str,
    kafka_index: dict[str, dict] | None,
) -> tuple[str | None, list[str]]:
    producer  = _metadata_owner(entry)
    consumers = _metadata_consumers(entry)

    if kafka_index and topic in kafka_index:
        kafka_entry = kafka_index[topic]
        if not producer:
            producer = kafka_entry.get("producer")
        if not consumers:
            consumers = kafka_entry.get("consumers", [])

    return producer, consumers


def _metadata_owner(entry: dict) -> str | None:
    """Extract owner (producer) from schema metadata.properties.owner."""
    props = entry.get("metadata", {}).get("properties", {})
    owner = props.get("owner", "").strip()
    return owner if owner else None


def _metadata_consumers(entry: dict) -> list[str]:
    """Extract consumers from schema metadata.properties.consumers.

    Accepts both a comma-separated string and a JSON list.
    """
    props = entry.get("metadata", {}).get("properties", {})
    raw = props.get("consumers", "")

    if isinstance(raw, list):
        return [c.strip() for c in raw if isinstance(c, str) and c.strip()]

    if isinstance(raw, str) and raw.strip():
        return [c.strip() for c in raw.split(",") if c.strip()]

    return []



def _build_kafka_index(kafka_path: Path) -> dict[str, dict]:
    """Parse a kafka-topics.json and build a topic → {producer, consumers} index."""
    try:
        data = json.loads(kafka_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not read Kafka fallback file %s: %s", kafka_path, e)
        return {}

    topics = data.get("topics", []) if isinstance(data, dict) else []
    index: dict[str, dict] = {}

    for entry in topics:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "").strip()
        if not name:
            continue
        index[name] = {
            "producer":  entry.get("producer", ""),
            "consumers": [
                c.strip() for c in entry.get("consumers", [])
                if isinstance(c, str) and c.strip()
            ],
        }

    logger.debug("Kafka fallback index: %d topics loaded from %s", len(index), kafka_path)
    return index