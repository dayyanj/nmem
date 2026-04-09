"""Import result dataclass shared by all importers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImportResult:
    """Result of an import operation."""

    imported: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)
