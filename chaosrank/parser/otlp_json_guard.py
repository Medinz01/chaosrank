"""
Shared format-mismatch guard for JSON OTLP parser.

Called by cli.py when --otlp-format json is explicit (not default).
Warns if the file header looks like binary rather than JSON.
Kept separate so otlp.py has zero new imports.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_JSON_START_BYTES = (b"{", b"[", b" ", b"\t", b"\n", b"\r")


def warn_if_binary(path: Path) -> None:
    """Warn if file header does not look like JSON text."""
    try:
        with open(path, "rb") as f:
            header = f.read(4)
    except OSError:
        return
    if header and header[:1] not in _JSON_START_BYTES:
        logger.warning(
            "%s does not look like a JSON file (starts with bytes %r) but "
            "--otlp-format json was specified. "
            "If this is a binary protobuf export, use --otlp-format protobuf instead.",
            path,
            header[:1],
        )
