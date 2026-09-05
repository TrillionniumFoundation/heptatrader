"""The shared document-header contract; no repository I/O or mutable cache."""
from __future__ import annotations

META = ("Status:", "Applies to:", "Verification:", "Authority:")
METADATA_LINE_LIMIT = 14


def missing_metadata(text: str) -> list[str]:
    lines = text.splitlines()[:METADATA_LINE_LIMIT]
    return [
        field for field in META
        if not any(line.startswith(field) and line[len(field):].strip()
                   for line in lines)
    ]
