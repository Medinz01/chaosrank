from abc import ABC, abstractmethod

from chaosrank.parser.incidents import Incident

_TYPE_KEYWORDS: dict[str, list[str]] = {
    "latency": ["latency", "slow", "p99", "p95", "response_time", "duration", "high_latency"],
    "timeout": ["timeout", "timed_out", "connection_refused", "deadline", "connect_timeout"],
    "error": ["error", "fail", "failed", "5xx", "500", "exception", "crash", "panic"],
}

_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high":     "high",
    "warning":  "medium",
    "warn":     "medium",
    "medium":   "medium",
    "info":     "low",
    "informational": "low",
    "low":      "low",
    # Opsgenie priorities
    "p1": "critical",
    "p2": "high",
    "p3": "medium",
    "p4": "low",
    "p5": "low",
}


def infer_type(text: str) -> str:
    """Infer incident type (error/latency/timeout) from alert name or title via keyword matching."""
    lower = text.lower()
    for incident_type, keywords in _TYPE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return incident_type
    return "error"


def normalize_severity(raw: str) -> str:
    """Normalize severity from various conventions to critical/high/medium/low."""
    return _SEVERITY_MAP.get(raw.strip().lower(), "low")


class IncidentAdapter(ABC):
    @abstractmethod
    def fetch(self, window_days: int) -> list[Incident]:
        """Fetch incidents from the source within the lookback window.

        Must not raise on partial failures — skip malformed entries,
        log warnings, and continue. Raise only on fatal errors
        (auth failures, connection errors) that make the entire fetch unprocessable.
        """

    @abstractmethod
    def source_format(self) -> str:
        """Return the --from flag value this adapter handles."""
