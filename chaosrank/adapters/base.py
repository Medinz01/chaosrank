from abc import ABC, abstractmethod
from pathlib import Path


class AsyncDepsAdapter(ABC):
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