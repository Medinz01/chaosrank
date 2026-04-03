"""Service name normalization and aliasing.
Consolidates semver, pod hashes, and version suffixes into canonical service names.
"""
from __future__ import annotations

import re

_VERSION_PATTERN = re.compile(r"-v\d+(\.\d+)*")
_SEMVER_PATTERN = re.compile(r"-\d+\.\d+\.\d+")
_POD_HASH_PATTERN = re.compile(r"-[a-f0-9]{5,10}$")

_aliases: dict[str, str] = {}


def load_aliases(aliases: dict[str, str]) -> None:
    """Load global service aliases for normalization."""
    _aliases.clear()
    _aliases.update({k.lower(): v.lower() for k, v in aliases.items()})


def normalize(name: str) -> str | None:
    """Strip versions, hashes, and whitespace from service names to return a canonical ID."""
    if not name or not name.strip():
        return None

    n = name.strip().lower()
    n = _VERSION_PATTERN.sub("", n)
    n = _SEMVER_PATTERN.sub("", n)
    n = _POD_HASH_PATTERN.sub("", n)
    n = n.strip("-")

    if not n:
        return None

    return _aliases.get(n, n)