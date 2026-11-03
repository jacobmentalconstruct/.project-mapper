"""Read-only spine context resolver for inference steps.

Accepts a spine path and an ordered list of 0-based entry indices
(position in read_entries() stream order).  Returns resolved entries
with their schema definition attached, in selection order.

No spine writes, no context_refs traversal, no UI.
"""

from pathlib import Path

if __package__:
    from .spine import Spine, SpineError
else:
    from spine import Spine, SpineError

__all__ = ["SpineContextError", "ResolvedEntry", "resolve_entries"]


class SpineContextError(Exception):
    """Raised when spine context cannot be resolved cleanly."""


class ResolvedEntry:
    """One resolved spine entry with its schema definition attached."""

    __slots__ = ("index", "schema_key", "schema_def", "ts", "data")

    def __init__(self, index: int, schema_key: str, schema_def: dict,
                 ts: str, data: dict):
        self.index = index
        self.schema_key = schema_key
        self.schema_def = schema_def
        self.ts = ts
        self.data = data

    def __repr__(self) -> str:
        return (f"ResolvedEntry(index={self.index}, schema={self.schema_key!r}, "
                f"ts={self.ts!r})")


def resolve_entries(spine_path: Path | str,
                    indices: list[int]) -> list[ResolvedEntry]:
    """Resolve selected spine entries by their stream-order indices.

    Args:
        spine_path: Path to the .jsonl spine file.
        indices: Ordered list of 0-based entry indices to select.

    Returns:
        List of ResolvedEntry in the same order as *indices*.

    Raises:
        SpineContextError: If the file is missing, an index is out of range,
            a record is malformed, or a schema reference cannot be resolved.
    """
    spine_path = Path(spine_path)
    try:
        spine = Spine.open(spine_path)
    except SpineError as exc:
        raise SpineContextError(f"Cannot open spine '{spine_path.name}': {exc}") from exc
    except Exception as exc:
        raise SpineContextError(f"Unexpected error opening spine: {exc}") from exc

    all_entries = spine.read_entries()

    results: list[ResolvedEntry] = []
    for idx in indices:
        if not isinstance(idx, int) or idx < 0:
            raise SpineContextError(f"Invalid entry index: {idx!r} (must be a non-negative int)")
        if idx >= len(all_entries):
            raise SpineContextError(
                f"Entry index {idx} is out of range "
                f"(spine '{spine_path.name}' has {len(all_entries)} entries)"
            )
        record = all_entries[idx]
        schema_key = record.get("schema", "")
        if not schema_key:
            raise SpineContextError(
                f"Entry {idx} in '{spine_path.name}' has no 'schema' field"
            )
        try:
            schema_def = spine.load_schema(schema_key)
        except SpineError as exc:
            raise SpineContextError(
                f"Entry {idx}: cannot resolve schema '{schema_key}': {exc}"
            ) from exc
        data = record.get("data")
        if not isinstance(data, dict):
            raise SpineContextError(
                f"Entry {idx} in '{spine_path.name}' has malformed 'data' (expected dict)"
            )
        results.append(ResolvedEntry(
            index=idx,
            schema_key=schema_key,
            schema_def=schema_def,
            ts=record.get("ts", ""),
            data=data,
        ))

    return results
