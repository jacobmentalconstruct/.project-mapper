"""Tests for inference_spine.resolve_entries — T02 verification.

All tests use disposable fixtures; no production spine files are touched.
"""

import unittest
from pathlib import Path

from tests.support import temporary_directory
from projectmapper.tools.spine import Spine
from projectmapper.tools.inference_spine import (
    SpineContextError,
    ResolvedEntry,
    resolve_entries,
)

_SCHEMA_V1 = {"id": "note", "version": 1, "properties": {"body": {"type": "string"}}}
_SCHEMA_V2 = {"id": "note", "version": 2, "properties": {"body": {"type": "string"}, "tag": {"type": "string"}}}
_SCHEMA_B  = {"id": "task", "version": 1, "properties": {"title": {"type": "string"}}}


def _fixture_spine(folder: Path) -> tuple[Path, list[dict]]:
    """Create a 4-entry spine with two schema versions and return (path, entries)."""
    spine = Spine.create(folder / "fixture.jsonl")
    spine.register_schema(_SCHEMA_V1)
    spine.register_schema(_SCHEMA_V2)
    spine.register_schema(_SCHEMA_B)
    spine.append_entry("note@1", {"body": "alpha"})       # index 0
    spine.append_entry("task@1", {"title": "buy milk"})   # index 1
    spine.append_entry("note@2", {"body": "beta", "tag": "important"})  # index 2
    spine.append_entry("note@1", {"body": "gamma"})       # index 3
    return spine.path, spine.read_entries()


class ResolveEntriesHappyPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(self)
        self.path, _ = _fixture_spine(Path(self.tmp.name))

    def test_single_entry(self):
        results = resolve_entries(self.path, [0])
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertIsInstance(r, ResolvedEntry)
        self.assertEqual(r.index, 0)
        self.assertEqual(r.schema_key, "note@1")
        self.assertEqual(r.data, {"body": "alpha"})

    def test_selection_order_preserved(self):
        results = resolve_entries(self.path, [3, 1, 0])
        self.assertEqual([r.index for r in results], [3, 1, 0])

    def test_schema_def_attached(self):
        results = resolve_entries(self.path, [2])
        self.assertIn("tag", results[0].schema_def.get("properties", {}))

    def test_multiple_schema_versions(self):
        results = resolve_entries(self.path, [0, 2])
        self.assertEqual(results[0].schema_key, "note@1")
        self.assertEqual(results[1].schema_key, "note@2")

    def test_empty_selection(self):
        self.assertEqual(resolve_entries(self.path, []), [])

    def test_repeated_index(self):
        results = resolve_entries(self.path, [1, 1])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].index, results[1].index)

    def test_str_path_accepted(self):
        results = resolve_entries(str(self.path), [0])
        self.assertEqual(len(results), 1)


class ResolveEntriesErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(self)
        self.path, _ = _fixture_spine(Path(self.tmp.name))

    def test_missing_file(self):
        bad = Path(self.tmp.name) / "nonexistent.jsonl"
        with self.assertRaises(SpineContextError) as ctx:
            resolve_entries(bad, [0])
        self.assertIn("nonexistent", str(ctx.exception))

    def test_index_out_of_range(self):
        with self.assertRaises(SpineContextError) as ctx:
            resolve_entries(self.path, [99])
        self.assertIn("out of range", str(ctx.exception))

    def test_negative_index(self):
        with self.assertRaises(SpineContextError) as ctx:
            resolve_entries(self.path, [-1])
        self.assertIn("Invalid entry index", str(ctx.exception))

    def test_non_integer_index(self):
        with self.assertRaises(SpineContextError) as ctx:
            resolve_entries(self.path, ["first"])  # type: ignore[list-item]
        self.assertIn("Invalid entry index", str(ctx.exception))

    def test_no_spine_writes_on_error(self):
        """Errors during resolution must not touch the spine file."""
        before = self.path.read_bytes()
        try:
            resolve_entries(self.path, [99])
        except SpineContextError:
            pass
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_entry_data(self):
        """An entry whose 'data' is not a dict raises SpineContextError."""
        bad_path = Path(self.tmp.name) / "bad.jsonl"
        spine = Spine.create(bad_path)
        spine.register_schema(_SCHEMA_V1)
        # Manually write a malformed entry (data is a string, not dict).
        import json
        with bad_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "entry", "schema": "note@1",
                                 "ts": "2025-01-01T00:00:00+00:00",
                                 "data": "not-a-dict"}) + "\n")
        with self.assertRaises(SpineContextError) as ctx:
            resolve_entries(bad_path, [0])
        self.assertIn("malformed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
