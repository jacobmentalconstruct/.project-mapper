"""Tests for projectmapper.tools.spine — pure Python, no Tk."""

import json
import unittest
from pathlib import Path

from tests.support import temporary_directory
from projectmapper.tools.spine import Spine, SpineError


class SpineSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(self)
        self.folder = Path(self.tmp.name)
        self.spine = Spine.create(self.folder / "test.jsonl")

    def _schema(self, id_="thing", version=1, **extra):
        s = {"id": id_, "version": version,
             "properties": {"name": {"type": "string", "label": "Name"}}}
        s.update(extra)
        return s

    def test_register_returns_key(self):
        key = self.spine.register_schema(self._schema())
        self.assertEqual(key, "thing@1")

    def test_schema_record_in_stream(self):
        self.spine.register_schema(self._schema())
        records = list(
            json.loads(line)
            for line in self.folder.joinpath("test.jsonl").read_text("utf-8").splitlines()
            if line.strip()
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "schema")
        self.assertEqual(records[0]["id"], "thing")

    def test_register_immutable(self):
        self.spine.register_schema(self._schema())
        with self.assertRaises(SpineError):
            self.spine.register_schema(self._schema())

    def test_register_different_versions_ok(self):
        self.spine.register_schema(self._schema(version=1))
        key2 = self.spine.register_schema(self._schema(version=2))
        self.assertEqual(key2, "thing@2")
        self.assertEqual(sorted(self.spine.list_schemas()), ["thing@1", "thing@2"])

    def test_load_schema_roundtrip(self):
        s = self._schema()
        self.spine.register_schema(s)
        loaded = self.spine.load_schema("thing@1")
        self.assertEqual(loaded, s)

    def test_load_schema_missing_raises(self):
        with self.assertRaises(SpineError):
            self.spine.load_schema("nope@1")

    def test_list_schemas_empty(self):
        empty_spine = Spine.create(self.folder / "empty.jsonl")
        self.assertEqual(empty_spine.list_schemas(), [])

    def test_register_bad_schema_raises(self):
        for bad in (
            None,
            {},
            {"id": "", "version": 1},
            {"id": "x", "version": 0},
            {"id": "x", "version": "1"},
            {"version": 1},
        ):
            with self.subTest(bad=bad), self.assertRaises(SpineError):
                self.spine.register_schema(bad)


class SpineEntriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(self)
        self.folder = Path(self.tmp.name)
        self.spine = Spine.create(self.folder / "entries.jsonl")
        self.spine.register_schema({"id": "thing", "version": 1, "properties": {}})

    def test_append_and_read_roundtrip(self):
        self.spine.append_entry("thing@1", {"name": "hello"})
        entries = self.spine.read_entries()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["schema"], "thing@1")
        self.assertEqual(e["data"], {"name": "hello"})
        self.assertIn("ts", e)

    def test_append_is_additive(self):
        for i in range(3):
            self.spine.append_entry("thing@1", {"n": i})
        entries = self.spine.read_entries()
        self.assertEqual(len(entries), 3)
        self.assertEqual([e["data"]["n"] for e in entries], [0, 1, 2])

    def test_read_empty(self):
        empty_spine = Spine.create(self.folder / "empty2.jsonl")
        empty_spine.register_schema({"id": "x", "version": 1, "properties": {}})
        self.assertEqual(empty_spine.read_entries(), [])

    def test_malformed_line_skipped(self):
        path = self.folder / "malformed.jsonl"
        path.write_text(
            '{"kind":"entry","schema":"thing@1","ts":"t","data":{}}\nnot-json\n',
            encoding="utf-8")
        spine = Spine.open(path)
        # We need the schema in memory to read entries; inject it manually.
        spine._schemas["thing@1"] = {}
        entries = spine.read_entries()
        self.assertEqual(len(entries), 1)

    def test_blank_lines_skipped(self):
        path = self.folder / "blanks.jsonl"
        path.write_text(
            '\n\n{"kind":"entry","schema":"thing@1","ts":"t","data":{}}\n\n',
            encoding="utf-8")
        spine = Spine.open(path)
        spine._schemas["thing@1"] = {}
        entries = spine.read_entries()
        self.assertEqual(len(entries), 1)

    def test_append_unknown_schema_raises(self):
        with self.assertRaises(SpineError):
            self.spine.append_entry("nope@99", {})


class SpineLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(self)
        self.folder = Path(self.tmp.name)

    def test_create_makes_file(self):
        spine = Spine.create(self.folder / "myspine.jsonl")
        self.assertTrue((self.folder / "myspine.jsonl").is_file())

    def test_name_is_stem(self):
        spine = Spine.create(self.folder / "dev-log.jsonl")
        self.assertEqual(spine.name, "dev-log")

    def test_create_duplicate_raises(self):
        Spine.create(self.folder / "s.jsonl")
        with self.assertRaises(SpineError):
            Spine.create(self.folder / "s.jsonl")

    def test_open_existing(self):
        Spine.create(self.folder / "s.jsonl")
        spine = Spine.open(self.folder / "s.jsonl")
        self.assertEqual(spine.name, "s")

    def test_open_missing_raises(self):
        with self.assertRaises(SpineError):
            Spine.open(self.folder / "nosuchfile.jsonl")

    def test_exists_true_and_false(self):
        self.assertFalse(Spine.exists(self.folder / "new.jsonl"))
        Spine.create(self.folder / "new.jsonl")
        self.assertTrue(Spine.exists(self.folder / "new.jsonl"))

    def test_rename_updates_file_and_name(self):
        spine = Spine.create(self.folder / "old.jsonl")
        spine.rename("new-name")
        self.assertFalse((self.folder / "old.jsonl").exists())
        self.assertTrue((self.folder / "new-name.jsonl").exists())
        self.assertEqual(spine.name, "new-name")

    def test_rename_strips_jsonl_suffix(self):
        spine = Spine.create(self.folder / "a.jsonl")
        spine.rename("b.jsonl")  # user accidentally includes extension
        self.assertEqual(spine.name, "b")

    def test_data_survives_reopen(self):
        target = self.folder / "persist.jsonl"
        spine = Spine.create(target)
        spine.register_schema({"id": "note", "version": 1, "properties": {}})
        spine.append_entry("note@1", {"body": "hello"})
        del spine

        reopened = Spine.open(target)
        self.assertEqual(reopened.name, "persist")
        self.assertEqual(reopened.list_schemas(), ["note@1"])
        entries = reopened.read_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["data"]["body"], "hello")

    def test_read_all_includes_schema_and_entry_records(self):
        spine = Spine.create(self.folder / "mixed.jsonl")
        spine.register_schema({"id": "x", "version": 1, "properties": {}})
        spine.append_entry("x@1", {"v": 1})
        records = spine.read_all()
        self.assertEqual(len(records), 2)
        kinds = [r["kind"] for r in records]
        self.assertEqual(kinds, ["schema", "entry"])


if __name__ == "__main__":
    unittest.main()
