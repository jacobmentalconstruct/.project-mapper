"""Tests for SchemaToucherWindow and Spine integration (schema-driven design)."""

import gc
import json
import tkinter as tk
import unittest
from pathlib import Path

from tests.support import PumpMixin, temporary_directory, tk_root
from projectmapper.app import ProjectMapperApp
from projectmapper.tools.spine import Spine, SpineError
from projectmapper.tools.schema_toucher_ui import _field_widget, _get_value


_SCHEMA = {
    "id": "item",
    "version": 1,
    "properties": {
        "name": {"type": "string", "label": "Name"},
        "count": {"type": "integer", "label": "Count"},
        "active": {"type": "boolean", "label": "Active"},
        "status": {"type": "string", "label": "Status",
                   "enum": ["open", "closed", "pending"]},
        "meta": {"type": "object", "label": "Meta"},
    },
}

_TEXTAREA_SCHEMA = {
    "id": "note",
    "version": 1,
    "properties": {
        "title": {"type": "string", "label": "Title"},
        "body": {"type": "string", "format": "textarea", "label": "Body"},
    },
}


class TextareaWidgetTests(unittest.TestCase):
    """Unit tests for _field_widget / _get_value textarea path (no window needed)."""

    def setUp(self):
        self.root = tk_root(self)
        self.root.withdraw()
        self._colors = {
            "field_bg": "#ffffff", "field_text": "#000000",
            "text": "#000000", "selection": "#aaccff",
            "panel_bg": "#f0f0f0", "log_bg": "#f8f8f8",
        }

    def test_textarea_prop_creates_text_widget(self):
        prop = {"type": "string", "format": "textarea", "label": "Body"}
        w = _field_widget(self.root, prop, self._colors)
        self.assertIsInstance(w, tk.Text)
        self.assertTrue(getattr(w, "_textarea", False))

    def test_plain_string_prop_creates_entry(self):
        prop = {"type": "string", "label": "Name"}
        w = _field_widget(self.root, prop, self._colors)
        self.assertIsInstance(w, tk.Entry)

    def test_get_value_textarea_plain_string(self):
        prop = {"type": "string", "format": "textarea"}
        w = _field_widget(self.root, prop, self._colors)
        w.insert("1.0", "Hello\nworld")
        result = _get_value(w, prop)
        self.assertEqual(result, "Hello\nworld")
        self.assertIsInstance(result, str)

    def test_get_value_textarea_not_json_parsed(self):
        prop = {"type": "string", "format": "textarea"}
        w = _field_widget(self.root, prop, self._colors)
        # JSON-shaped input must still come back as a plain string.
        w.insert("1.0", '{"key": "val"}')
        result = _get_value(w, prop)
        self.assertIsInstance(result, str)
        self.assertEqual(result, '{"key": "val"}')


class SpineToucherTests(unittest.TestCase):
    """Spine data-layer integration (no Tk)."""

    def setUp(self):
        self.tmp = temporary_directory(self)
        self.folder = Path(self.tmp.name)

    def test_create_and_reopen(self):
        spine = Spine.create(self.folder / "s.jsonl")
        spine.register_schema(_SCHEMA)
        spine.append_entry("item@1", {"name": "hello"})
        del spine

        reopened = Spine.open(self.folder / "s.jsonl")
        self.assertEqual(reopened.name, "s")
        self.assertEqual(reopened.list_schemas(), ["item@1"])
        entries = reopened.read_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["data"]["name"], "hello")

    def test_version_immutability(self):
        spine = Spine.create(self.folder / "s.jsonl")
        spine.register_schema(_SCHEMA)
        with self.assertRaises(SpineError):
            spine.register_schema(_SCHEMA)

    def test_new_version_ok(self):
        spine = Spine.create(self.folder / "s.jsonl")
        spine.register_schema(_SCHEMA)
        schema_v2 = dict(_SCHEMA, version=2)
        key2 = spine.register_schema(schema_v2)
        self.assertEqual(key2, "item@2")

    def test_exists(self):
        self.assertFalse(Spine.exists(self.folder / "s.jsonl"))
        Spine.create(self.folder / "s.jsonl")
        self.assertTrue(Spine.exists(self.folder / "s.jsonl"))


class SchemaToucherWindowTests(PumpMixin, unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.tmp = temporary_directory(self)
        self.folder = Path(self.tmp.name)
        self.root = tk_root(self)
        self.root.withdraw()
        self.app = ProjectMapperApp(self.root)
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.app.selected_root = self.folder
        # Open without a pre-existing spine → window starts empty
        self.win = self.app.open_schema_toucher(self.folder)
        self.root.update_idletasks()

    def _create_spine(self, stem="test-spine"):
        """Helper: create a spine file and switch the window to it."""
        spine = Spine.create(self.folder / f"{stem}.jsonl")
        self.win._switch_spine(spine)
        self.root.update_idletasks()

    def _put_schema_in_editor(self, schema=None):
        schema = schema or _SCHEMA
        self.win._schema_editor.delete("1.0", "end")
        self.win._schema_editor.insert("1.0", json.dumps(schema, indent=2))
        self.root.update_idletasks()

    def _setup_schema(self):
        self._create_spine()
        self._put_schema_in_editor()
        self.win._register_schema()
        self.root.update_idletasks()

    # ── window opens ──────────────────────────────────────────────────────

    def test_window_opens_without_spine(self):
        self.assertTrue(self.win.top.winfo_exists())
        self.assertIsNone(self.win._spine)

    def test_no_spine_status(self):
        self.assertIn("spine", self.win._status.get().lower())

    # ── spine creation ────────────────────────────────────────────────────

    def test_switch_spine_sets_name_label(self):
        self._create_spine("my-spine")
        self.assertIn("my-spine", self.win._spine_name_var.get())

    def test_switch_spine_sets_path_label(self):
        self._create_spine("my-spine")
        self.assertIn("my-spine", self.win._spine_path_var.get())

    def test_switch_spine_updates_title(self):
        self._create_spine("special-spine")
        self.assertIn("special-spine", self.win.top.title())

    def test_switch_spine_loads_existing_entries(self):
        # Pre-populate a spine outside the window, then switch to it.
        spine = Spine.create(self.folder / "prepop.jsonl")
        spine.register_schema(_SCHEMA)
        spine.append_entry("item@1", {"name": "preloaded"})
        del spine
        reopened = Spine.open(self.folder / "prepop.jsonl")
        self.win._switch_spine(reopened)
        self.root.update_idletasks()
        self.assertEqual(self.win._entry_count, 1)

    def test_auto_loads_last_schema_on_switch(self):
        """Switching to a spine defaults the Entry Form to the most recently registered schema."""
        spine = Spine.create(self.folder / "with-schema.jsonl")
        spine.register_schema(_SCHEMA)
        schema_v2 = dict(_SCHEMA, version=2)
        spine.register_schema(schema_v2)
        self.win._switch_spine(spine)
        self.root.update_idletasks()
        # Most recently registered schema is item@2.
        self.assertEqual(self.win._schema_key, "item@2")

    # ── schema registration ───────────────────────────────────────────────

    def test_register_schema_builds_form(self):
        self._setup_schema()
        self.assertEqual(self.win._schema_key, "item@1")
        self.assertIn("name", self.win._field_widgets)

    def test_register_schema_updates_combo(self):
        self._setup_schema()
        self.assertIn("item@1", self.win._schema_combo["values"])

    def test_register_bad_json_sets_status(self):
        self._create_spine()
        self.win._schema_editor.delete("1.0", "end")
        self.win._schema_editor.insert("1.0", "not-json")
        self.win._register_schema()
        self.assertIn("Invalid JSON", self.win._status.get())

    def test_register_duplicate_sets_status(self):
        self._setup_schema()
        self._put_schema_in_editor()
        self.win._register_schema()
        self.assertIn("Register failed", self.win._status.get())

    def test_register_without_spine_sets_status(self):
        self._put_schema_in_editor()
        self.win._register_schema()
        self.assertIn("No spine", self.win._status.get())

    # ── entry append ──────────────────────────────────────────────────────

    def test_append_entry_creates_record(self):
        self._setup_schema()
        self.win._field_widgets["name"]._var.set("Alice")
        self.win._append_entry()
        entries = Spine.open(self.folder / "test-spine.jsonl").read_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["data"]["name"], "Alice")

    def test_append_without_spine_sets_status(self):
        self.win._append_entry()
        self.assertIn("No spine", self.win._status.get())

    def test_append_updates_entry_count(self):
        self._setup_schema()
        self.win._append_entry()
        self.assertEqual(self.win._entry_count, 1)

    # ── browse ────────────────────────────────────────────────────────────

    def test_browse_shows_appended_record(self):
        self._setup_schema()
        self.win._field_widgets["name"]._var.set("Bob")
        self.win._append_entry()
        self.root.update_idletasks()
        text = self.win._browse_text.get("1.0", "end-1c")
        self.assertIn("Bob", text)

    def test_browse_navigation(self):
        self._setup_schema()
        for name in ("A", "B", "C"):
            self.win._field_widgets["name"]._var.set(name)
            self.win._append_entry()
        self.root.update_idletasks()
        self.assertEqual(self.win._browse_index, 2)
        self.win._browse_prev()
        self.assertEqual(self.win._browse_index, 1)
        self.win._browse_next()
        self.assertEqual(self.win._browse_index, 2)

    def test_browse_navigation_does_not_change_entry_form_schema(self):
        """Navigating Browse must never change the active Entry Form schema."""
        self._setup_schema()
        # Register a second schema and switch Entry Form to it.
        schema_v2 = dict(_SCHEMA, version=2)
        self.win._schema_editor.delete("1.0", "end")
        self.win._schema_editor.insert("1.0", json.dumps(schema_v2, indent=2))
        self.win._register_schema()
        self.win._schema_var.set("item@2")
        self.win._on_schema_selected()
        self.root.update_idletasks()
        self.assertEqual(self.win._schema_key, "item@2")

        # Append some entries and navigate.
        self.win._field_widgets["name"]._var.set("X")
        self.win._append_entry()
        self.win._field_widgets["name"]._var.set("Y")
        self.win._append_entry()
        self.win._browse_prev()
        self.root.update_idletasks()

        # Entry Form schema must still be item@2.
        self.assertEqual(self.win._schema_key, "item@2")

    # ── browse renders entry's own schema, not form schema ────────────────

    def test_browse_uses_entry_schema_for_labels(self):
        """Each entry is rendered using its own recorded schema, not the current form schema."""
        self._setup_schema()
        self.win._field_widgets["name"]._var.set("Charlie")
        self.win._append_entry()

        # Register a different schema and switch the form to it.
        schema_v2 = dict(_SCHEMA, version=2, properties={
            "title": {"type": "string", "label": "Title"}})
        self.win._schema_editor.delete("1.0", "end")
        self.win._schema_editor.insert("1.0", json.dumps(schema_v2, indent=2))
        self.win._register_schema()
        self.win._schema_var.set("item@2")
        self.win._on_schema_selected()
        self.root.update_idletasks()

        # Browse the original entry (schema item@1).
        self.win._browse_index = 0
        self.win._refresh_browse()
        self.root.update_idletasks()

        text = self.win._browse_text.get("1.0", "end-1c")
        # The original entry's label "Name" should appear, not v2's "Title".
        self.assertIn("Name", text)
        self.assertIn("Charlie", text)

    # ── full roundtrip: create → append → reopen → read ──────────────────

    def test_full_roundtrip(self):
        self._setup_schema()
        self.win._field_widgets["name"]._var.set("Roundtrip")
        self.win._append_entry()

        reopened = Spine.open(self.folder / "test-spine.jsonl")
        entries = reopened.read_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["data"]["name"], "Roundtrip")
        schemas = reopened.list_schemas()
        self.assertEqual(schemas, ["item@1"])

    def test_browse_shows_label_not_raw_key(self):
        """Browse panel renders the schema label, not the bare field key."""
        self._setup_schema()
        self.win._field_widgets["name"]._var.set("Charlie")
        self.win._append_entry()
        self.root.update_idletasks()
        text = self.win._browse_text.get("1.0", "end-1c")
        self.assertIn("Name", text)   # schema label
        self.assertNotIn('"name"', text)  # raw JSON key must not appear

    def _setup_textarea_schema(self):
        """Create a spine and register the textarea schema."""
        self._create_spine()
        self.win._schema_editor.delete("1.0", "end")
        self.win._schema_editor.insert("1.0", json.dumps(_TEXTAREA_SCHEMA, indent=2))
        self.win._register_schema()
        self.root.update_idletasks()

    def test_textarea_roundtrip_in_browse(self):
        """Multiline textarea value survives append → browse."""
        self._setup_textarea_schema()
        self.win._field_widgets["title"]._var.set("My note")
        body_widget = self.win._field_widgets["body"]
        body_widget.insert("1.0", "Line one\nLine two")
        self.win._append_entry()
        self.root.update_idletasks()
        text = self.win._browse_text.get("1.0", "end-1c")
        self.assertIn("Line one", text)
        self.assertIn("Line two", text)
        # Verify the stored value is a plain string, not JSON-encoded.
        entries = self.win._spine.read_entries()
        self.assertIsInstance(entries[0]["data"]["body"], str)
        self.assertIn("Line one", entries[0]["data"]["body"])

    # ── close guard ───────────────────────────────────────────────────────

    def test_close_cleans_up(self):
        self.win._mark_dirty(False)
        self.win.close()
        self.assertFalse(self.win.top.winfo_exists())
