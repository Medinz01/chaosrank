from abc import ABC, abstractmethod
from pathlib import Path


class AsyncDepsAdapter(ABC):
    """Base class for async dependency format adapters.

    Each adapter converts a source format into a list of dependency dicts
    matching the async-deps.yaml schema expected by parse_async_deps().

    Adapters return raw names — normalization happens downstream in
    parse_async_deps(). Adapters are responsible for extraction only.

    Output schema per entry:
        {
            "producer": str,           # required
            "consumer": str,           # required
            "channel":  str,           # required (kafka/sqs/rabbitmq/etc.)
            "topic":    str | None,    # optional
            "queue":    str | None,    # optional
        }
    """

    @abstractmethod
    def convert(self, input_path: Path) -> list[dict]:
        """Parse input file and return list of dependency dicts.

        Must not raise on partial failures — skip malformed entries,
        log warnings, and continue. Raise only on fatal errors that
        make the entire file unprocessable.
        """

    @abstractmethod
    def source_format(self) -> str:
        """Return the --from flag value this adapter handles (e.g. 'asyncapi')."""