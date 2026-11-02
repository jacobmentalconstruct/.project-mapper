"""Schema-driven JSON record editor backed by a single-file append-only Spine."""

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .spine import Spine, SpineError
from .ui_base import ToolWindowMixin

# ── field-widget factory ───────────────────────────────────────────────────

_MULTILINE_TYPES = ("object", "array")
_BROWSE_ALL = "all"
_BROWSE_SINGLE = "single"
_BROWSE_RAW = "raw"


def _is_textarea(prop: dict) -> bool:
    return prop.get("type", "string") == "string" and prop.get("format") == "textarea"


def _field_widget(parent, prop: dict, colors: dict) -> tk.Widget:
    """Create and return the right input widget for a schema property."""
    ftype = prop.get("type", "string")
    if prop.get("enum"):
        var = tk.StringVar(parent)
        w = ttk.Combobox(parent, textvariable=var, values=prop["enum"],
                         state="readonly", width=24)
        w._var = var
        return w
    if ftype == "boolean":
        var = tk.BooleanVar(parent)
        w = tk.Checkbutton(parent, variable=var, bg=colors["panel_bg"],
                           fg=colors["text"], selectcolor=colors["panel_bg"],
                           activebackground=colors["panel_bg"])
        w._var = var
        return w
    if _is_textarea(prop):
        # Plain prose textarea — value stored as a plain string, not JSON.
        w = tk.Text(parent, height=5, wrap="word", font=("Arial", 10),
                    bg=colors["field_bg"], fg=colors["field_text"],
                    insertbackground=colors["text"],
                    selectbackground=colors["selection"],
                    relief="flat", padx=6, pady=4)
        w._var = None
        w._textarea = True
        return w
    if ftype in _MULTILINE_TYPES:
        w = tk.Text(parent, height=3, wrap="none", font=("Consolas", 9),
                    bg=colors["log_bg"], fg=colors["text"],
                    insertbackground=colors["text"],
                    selectbackground=colors["selection"],
                    relief="flat", padx=4, pady=3)
        w._var = None
        return w
    var = tk.StringVar(parent)
    w = tk.Entry(parent, textvariable=var, font=("Arial", 10), relief="flat",
                 bg=colors["field_bg"], fg=colors["field_text"],
                 insertbackground=colors["text"],
                 selectbackground=colors["selection"])
    w._var = var
    return w


def _get_value(w: tk.Widget, prop: dict):
    ftype = prop.get("type", "string")
    if hasattr(w, "_var") and w._var is not None:
        raw = w._var.get()
        if ftype == "boolean":
            return bool(raw)
        if ftype in ("number", "integer"):
            try:
                return int(raw) if ftype == "integer" else float(raw)
            except (ValueError, TypeError):
                return raw
        return raw
    # Text widget — textarea is plain string; object/array parses as JSON.
    raw = w.get("1.0", "end-1c")
    if getattr(w, "_textarea", False):
        return raw.rstrip("\n")
    raw = raw.strip()
    if not raw:
        return {} if ftype == "object" else []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _set_value(w: tk.Widget, value, prop: dict):
    if hasattr(w, "_var") and w._var is not None:
        if isinstance(value, bool):
            w._var.set(value)
        elif value is None:
            w._var.set("")
        else:
            w._var.set(str(value))
    else:
        w.configure(state="normal")
        w.delete("1.0", "end")
        if isinstance(value, (dict, list)):
            w.insert("1.0", json.dumps(value, indent=2))
        elif value is not None:
            w.insert("1.0", str(value))


class SchemaToucherWindow(ToolWindowMixin):
    def __init__(self, app, folder=None, path=None):
        self.app = app
        self.colors = app.theme
        self._default_folder = (Path(folder) if folder
                                 else (Path(path).parent if path else Path.home()))
        self._spine: Spine | None = None
        self._schema_key: str | None = None
        self._schema_def: dict | None = None
        self._field_widgets: dict = {}
        self._entry_count = 0
        self._browse_index = -1
        self._browse_mode = tk.StringVar(value=_BROWSE_SINGLE)
        self._syncing = False
        self.dirty = False

        self.top = tk.Toplevel(app.root)
        self.configure_tool_window("SchemaTOUCHER", "1000x700", (700, 500))
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self.top.update_idletasks()

    def close(self):
        self.top.destroy()

    def _mark_dirty(self, dirty=True):
        self.dirty = dirty

    # ── UI build ───────────────────────────────────────────────────────────

    def _build_ui(self):
        c = self.colors
        top = self.top
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)

        # ── Row 0: Spine header ───────────────────────────────────────────
        hdr = tk.Frame(top, bg=c["panel_alt_bg"], pady=5)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(2, weight=1)

        # Spine ▾ menu button
        self._spine_mb = tk.Menubutton(
            hdr, text="Spine ▾",
            bg=c["accent"], fg="#FFFFFF",
            activebackground=c["accent_hover"], activeforeground="#FFFFFF",
            relief="flat", padx=10, pady=4, font=("Arial", 9, "bold"),
            cursor="hand2")
        self._spine_mb.grid(row=0, column=0, padx=(10, 8), pady=2)
        spine_menu = tk.Menu(self._spine_mb, tearoff=False,
                             bg=c["panel_bg"], fg=c["text"],
                             activebackground=c["secondary"],
                             activeforeground=c["text"])
        spine_menu.add_command(label="New Spine…", command=self._new_spine)
        spine_menu.add_command(label="Open Spine…", command=self._open_spine)
        spine_menu.add_separator()
        self._rename_cmd_index = 3
        spine_menu.add_command(label="Rename Spine…", command=self._rename_spine,
                               state="disabled")
        self._spine_mb["menu"] = spine_menu
        self._spine_menu = spine_menu

        self._spine_name_var = tk.StringVar(value="(no spine loaded)")
        tk.Label(hdr, textvariable=self._spine_name_var,
                 bg=c["panel_alt_bg"], fg=c["heading_text"],
                 font=("Arial", 10, "bold")).grid(row=0, column=1, padx=(0, 10), sticky="w")

        self._spine_path_var = tk.StringVar()
        tk.Label(hdr, textvariable=self._spine_path_var,
                 bg=c["panel_alt_bg"], fg=c["muted_text"],
                 font=("Arial", 8)).grid(row=0, column=2, padx=(0, 10), sticky="w")

        # ── Row 1: Notebook ───────────────────────────────────────────────
        self._notebook = ttk.Notebook(top)
        self._notebook.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)

        self._entry_tab = tk.Frame(self._notebook, bg=c["panel_bg"])
        self._schema_tab = tk.Frame(self._notebook, bg=c["panel_bg"])
        self._browse_tab = tk.Frame(self._notebook, bg=c["panel_bg"])

        self._notebook.add(self._entry_tab, text="Entry Form")
        self._notebook.add(self._schema_tab, text="Schema Editor")
        self._notebook.add(self._browse_tab, text="Browse Entries")

        self._build_entry_tab()
        self._build_schema_tab()
        self._build_browse_tab()

        # ── Row 2: Status bar ─────────────────────────────────────────────
        self._status = tk.StringVar(value="Create or open a spine to begin.")
        tk.Label(top, textvariable=self._status,
                 bg=c["status_bg"], fg=c["status_text"],
                 anchor="w", padx=10, pady=5,
                 font=("Arial", 8)).grid(row=2, column=0, sticky="ew")

    # ── Entry Form tab ────────────────────────────────────────────────────

    def _build_entry_tab(self):
        c = self.colors
        t = self._entry_tab
        t.columnconfigure(0, weight=1)
        t.rowconfigure(1, weight=1)

        # Schema selector bar
        schema_bar = tk.Frame(t, bg=c["panel_alt_bg"], pady=5)
        schema_bar.grid(row=0, column=0, sticky="ew")
        schema_bar.columnconfigure(2, weight=1)

        self.label(schema_bar, text="Active Schema:", panel=False).grid(
            row=0, column=0, padx=(12, 6), sticky="w")
        self._schema_var = tk.StringVar()
        self._schema_combo = ttk.Combobox(schema_bar, textvariable=self._schema_var,
                                          state="readonly", width=26)
        self._schema_combo.grid(row=0, column=1, padx=(0, 10), sticky="w")
        self._schema_combo.bind("<<ComboboxSelected>>", self._on_schema_selected)

        # "Go to Schema Editor" link shown when no schemas exist
        self._no_schema_btn = tk.Button(
            schema_bar,
            text="No schema registered — click to open Schema Editor",
            bg=c["panel_alt_bg"], fg=c["secondary"],
            relief="flat", cursor="hand2", font=("Arial", 9),
            command=lambda: self._notebook.select(1))
        # Initially hidden; shown by _refresh_schema_list when needed.

        # Scrollable form area
        form_outer = tk.Frame(t, bg=c["panel_bg"])
        form_outer.grid(row=1, column=0, sticky="nsew")
        form_outer.columnconfigure(0, weight=1)
        form_outer.rowconfigure(0, weight=1)

        self._form_canvas = tk.Canvas(form_outer, bg=c["panel_bg"], highlightthickness=0)
        form_scroll = ttk.Scrollbar(form_outer, command=self._form_canvas.yview)
        self._form_canvas.configure(yscrollcommand=form_scroll.set)
        self._form_canvas.grid(row=0, column=0, sticky="nsew")
        form_scroll.grid(row=0, column=1, sticky="ns")

        self._form_inner = tk.Frame(self._form_canvas, bg=c["panel_bg"])
        self._form_window = self._form_canvas.create_window(
            (0, 0), window=self._form_inner, anchor="nw")
        self._form_inner.bind("<Configure>",
            lambda e: self._form_canvas.configure(
                scrollregion=self._form_canvas.bbox("all")))
        self._form_canvas.bind("<Configure>",
            lambda e: self._form_canvas.itemconfigure(
                self._form_window, width=e.width))

        # Fixed preview + action area at bottom of Entry Form
        preview_frame = tk.Frame(t, bg=c["panel_alt_bg"])
        preview_frame.grid(row=2, column=0, sticky="ew")
        preview_frame.columnconfigure(0, weight=1)

        preview_header = tk.Frame(preview_frame, bg=c["panel_alt_bg"])
        preview_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
        preview_header.columnconfigure(0, weight=1)
        tk.Label(preview_header, text="ENTRY JSON PREVIEW",
                 bg=c["panel_alt_bg"], fg=c["muted_text"],
                 font=("Arial", 7, "bold")).grid(row=0, column=0, sticky="w")
        self.button(preview_header, "Copy Entry JSON", self._copy_entry_json,
                    "secondary", tooltip_id="st.copy_entry_json").grid(
            row=0, column=1, padx=(8, 0))

        self._preview = tk.Text(preview_frame, height=4, wrap="none",
                                font=("Consolas", 8),
                                bg=c["log_bg"], fg=c["text"],
                                relief="flat", padx=8, pady=4, state="disabled")
        self._preview.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 0))

        action_row = tk.Frame(preview_frame, bg=c["panel_alt_bg"])
        action_row.grid(row=2, column=0, sticky="e", padx=10, pady=(4, 8))
        self._append_btn = self.button(action_row, "Append Entry", self._append_entry,
                                       "success", tooltip_id="st.append")
        self._append_btn.pack(side="right")

    # ── Schema Editor tab ─────────────────────────────────────────────────

    def _build_schema_tab(self):
        c = self.colors
        t = self._schema_tab
        t.columnconfigure(0, weight=1)
        t.rowconfigure(2, weight=1)

        # Top button bar
        top_bar = tk.Frame(t, bg=c["panel_alt_bg"], pady=5)
        top_bar.grid(row=0, column=0, sticky="ew")
        self.button(top_bar, "Load Schema JSON…", self._load_schema_file,
                    "secondary", tooltip_id="st.load_schema").pack(side="left", padx=(10, 4))
        self.button(top_bar, "Copy Schema JSON", self._copy_schema_json,
                    "secondary", tooltip_id="st.copy_schema_json").pack(side="left", padx=4)

        # Registered schemas selector
        reg_bar = tk.Frame(t, bg=c["panel_bg"], pady=5)
        reg_bar.grid(row=1, column=0, sticky="ew")
        self.label(reg_bar, text="Registered:", panel=True).pack(side="left", padx=(12, 6))
        self._reg_var = tk.StringVar()
        self._reg_combo = ttk.Combobox(reg_bar, textvariable=self._reg_var,
                                       state="readonly", width=26)
        self._reg_combo.pack(side="left")
        self._reg_combo.bind("<<ComboboxSelected>>", self._on_reg_schema_selected)
        self.label(reg_bar, text="← select to inspect; edit and Register to create a new version",
                   panel=True, font=("Arial", 8),
                   fg=c["muted_text"]).pack(side="left", padx=(8, 0))

        # Schema editor
        editor_outer = tk.Frame(t, bg=c["panel_bg"])
        editor_outer.grid(row=2, column=0, sticky="nsew")
        editor_outer.columnconfigure(0, weight=1)
        editor_outer.rowconfigure(0, weight=1)

        self._schema_editor = tk.Text(editor_outer, wrap="none", font=("Consolas", 9),
                                      bg=c["log_bg"], fg=c["text"],
                                      insertbackground=c["text"],
                                      selectbackground=c["selection"],
                                      relief="flat", padx=10, pady=8)
        self._schema_editor.grid(row=0, column=0, sticky="nsew")
        se_scroll = ttk.Scrollbar(editor_outer, command=self._schema_editor.yview)
        se_scroll.grid(row=0, column=1, sticky="ns")
        self._schema_editor.configure(yscrollcommand=se_scroll.set)
        self._schema_editor.bind("<<Modified>>", self._on_schema_editor_changed)

        # Bottom: editor status + Register button
        bot_bar = tk.Frame(t, bg=c["panel_alt_bg"], pady=5)
        bot_bar.grid(row=3, column=0, sticky="ew")
        bot_bar.columnconfigure(0, weight=1)
        self._schema_editor_status = tk.StringVar()
        tk.Label(bot_bar, textvariable=self._schema_editor_status,
                 bg=c["panel_alt_bg"], fg=c["muted_text"],
                 font=("Arial", 8), anchor="w").grid(row=0, column=0, padx=10, sticky="w")
        self.button(bot_bar, "Register Schema", self._register_schema,
                    "accent", tooltip_id="st.register").grid(row=0, column=1, padx=(0, 10))

    # ── Browse Entries tab ────────────────────────────────────────────────

    def _build_browse_tab(self):
        c = self.colors
        t = self._browse_tab
        t.columnconfigure(0, weight=1)
        t.rowconfigure(1, weight=1)

        # Mode selector row
        mode_bar = tk.Frame(t, bg=c["panel_alt_bg"], pady=5)
        mode_bar.grid(row=0, column=0, sticky="ew")
        tk.Label(mode_bar, text="VIEW:", bg=c["panel_alt_bg"],
                 fg=c["muted_text"], font=("Arial", 8, "bold")).pack(side="left", padx=(12, 8))
        for label, value in [
            ("Entire Spine", _BROWSE_ALL),
            ("Single Entry", _BROWSE_SINGLE),
            ("Raw JSONL", _BROWSE_RAW),
        ]:
            tk.Radiobutton(mode_bar, text=label, variable=self._browse_mode,
                           value=value, command=self._on_browse_mode_changed,
                           bg=c["panel_alt_bg"], fg=c["text"],
                           selectcolor=c["panel_alt_bg"],
                           activebackground=c["panel_alt_bg"],
                           activeforeground=c["text"],
                           font=("Arial", 9)).pack(side="left", padx=(0, 12))

        # Content area (text + scrollbar)
        content = tk.Frame(t, bg=c["panel_bg"])
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        self._browse_text = tk.Text(content, wrap="word", font=("Consolas", 10),
                                    bg=c["log_bg"], fg=c["text"],
                                    insertbackground=c["text"],
                                    selectbackground=c["selection"],
                                    relief="flat", padx=12, pady=10, state="disabled")
        self._browse_text.grid(row=0, column=0, sticky="nsew")
        bs = ttk.Scrollbar(content, command=self._browse_text.yview)
        bs.grid(row=0, column=1, sticky="ns")
        self._browse_text.configure(yscrollcommand=bs.set)

        # Text tags for raw view
        self._browse_text.tag_configure(
            "schema_hdr", foreground=c["accent"], font=("Consolas", 9, "bold"))
        self._browse_text.tag_configure(
            "entry_hdr", foreground=c["success"], font=("Consolas", 9, "bold"))
        self._browse_text.tag_configure(
            "entry_sep", foreground=c["muted_text"])
        self._browse_text.tag_configure(
            "dim", foreground=c["muted_text"], font=("Consolas", 9))

        # Single-entry nav (row 2; shown/hidden based on mode)
        self._nav_frame = tk.Frame(t, bg=c["panel_alt_bg"], pady=5)
        self._nav_frame.columnconfigure(1, weight=1)
        self.button(self._nav_frame, "◄ Prev", self._browse_prev,
                    "secondary", tooltip_id="st.prev").grid(row=0, column=0, padx=(10, 4))
        self._record_label = tk.Label(self._nav_frame, text="",
                                      bg=c["panel_alt_bg"], fg=c["text"],
                                      font=("Arial", 9))
        self._record_label.grid(row=0, column=1)
        self.button(self._nav_frame, "Next ►", self._browse_next,
                    "secondary", tooltip_id="st.next").grid(row=0, column=2, padx=(4, 10))

        self._update_nav_visibility()

    def _on_browse_mode_changed(self):
        self._update_nav_visibility()
        self._refresh_browse()

    def _update_nav_visibility(self):
        if self._browse_mode.get() == _BROWSE_SINGLE:
            self._nav_frame.grid(row=2, column=0, sticky="ew")
        else:
            self._nav_frame.grid_remove()

    # ── Spine management ──────────────────────────────────────────────────

    def _new_spine(self):
        name = simpledialog.askstring(
            "New Spine", "Spine name:", parent=self.top)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        stem = name[:-6] if name.endswith(".jsonl") else name
        filename = stem + ".jsonl"

        suggested = self._default_folder / "_spine"
        suggested.mkdir(parents=True, exist_ok=True)

        folder = filedialog.askdirectory(
            parent=self.top,
            initialdir=str(suggested),
            title=f"Choose location for {filename}")
        if not folder:
            return

        spine_path = Path(folder) / filename
        try:
            spine = Spine.create(spine_path)
        except SpineError as exc:
            messagebox.showerror("Cannot create spine", str(exc), parent=self.top)
            return
        self._switch_spine(spine)

    def _open_spine(self):
        suggested = str(self._default_folder / "_spine")
        path = filedialog.askopenfilename(
            parent=self.top,
            initialdir=suggested,
            filetypes=[("Spine files", "*.jsonl"), ("All files", "*.*")],
            title="Open Spine")
        if not path:
            return
        try:
            spine = Spine.open(Path(path))
        except SpineError as exc:
            messagebox.showerror("Cannot open spine", str(exc), parent=self.top)
            return
        self._switch_spine(spine)

    def _rename_spine(self):
        if not self._spine:
            return
        new_name = simpledialog.askstring(
            "Rename Spine", "New name (without .jsonl):",
            initialvalue=self._spine.name, parent=self.top)
        if not new_name:
            return
        try:
            self._spine.rename(new_name.strip())
        except SpineError as exc:
            messagebox.showerror("Rename failed", str(exc), parent=self.top)
            return
        self._update_spine_header()
        self._status.set(f"Renamed to: {self._spine.name}.jsonl")

    def _switch_spine(self, spine: Spine):
        self._spine = spine
        self._schema_key = None
        self._schema_def = None
        self._schema_var.set("")   # force re-selection for new spine's schemas
        self._field_widgets.clear()
        self._entry_count = len(spine.read_entries())
        self._browse_index = self._entry_count - 1

        self._update_spine_header()
        self._refresh_schema_list()
        self._rebuild_form()
        self._refresh_browse()
        self._status.set(
            f"{spine.name}.jsonl  |  "
            f"{len(spine.list_schemas())} schema(s)  |  {self._entry_count} entries")

    def _update_spine_header(self):
        if self._spine:
            self._spine_name_var.set(f"{self._spine.name}.jsonl")
            self._spine_path_var.set(str(self._spine.path))
            self.top.title(f"{self._spine.name} — SchemaTOUCHER")
            self._spine_menu.entryconfigure(self._rename_cmd_index, state="normal")
        else:
            self._spine_name_var.set("(no spine loaded)")
            self._spine_path_var.set("")
            self.top.title("SchemaTOUCHER")
            self._spine_menu.entryconfigure(self._rename_cmd_index, state="disabled")

    # ── Schema management ─────────────────────────────────────────────────

    def _refresh_schema_list(self):
        keys = self._spine.list_schemas() if self._spine else []
        self._schema_combo["values"] = keys
        self._reg_combo["values"] = keys

        if keys:
            self._no_schema_btn.grid_remove()
            self._schema_combo.grid()
            if not self._schema_var.get() or self._schema_var.get() not in keys:
                # Default to most recently registered schema.
                self._schema_var.set(keys[-1])
                self._on_schema_selected()
        else:
            self._schema_combo.grid_remove()
            self._no_schema_btn.grid(row=0, column=1, padx=(0, 10), sticky="w")

    def _on_schema_selected(self, _=None):
        if not self._spine:
            return
        key = self._schema_var.get()
        if not key:
            return
        try:
            schema_def = self._spine.load_schema(key)
        except SpineError as exc:
            self._status.set(f"Cannot load schema: {exc}")
            return
        self._schema_key = key
        self._schema_def = schema_def
        self._rebuild_form()

    def _on_reg_schema_selected(self, _=None):
        """Load a registered definition into the editor for inspection."""
        if not self._spine:
            return
        key = self._reg_var.get()
        if not key:
            return
        try:
            schema_def = self._spine.load_schema(key)
        except SpineError as exc:
            self._status.set(f"Cannot load schema: {exc}")
            return
        self._syncing = True
        try:
            self._schema_editor.delete("1.0", "end")
            self._schema_editor.insert("1.0", json.dumps(schema_def, indent=2))
            self._schema_editor.edit_modified(False)
        finally:
            self._syncing = False
        self._schema_editor_status.set(
            f"Inspecting: {key}  —  edit and Register to create a new version")

    def _on_schema_editor_changed(self, _=None):
        if not self._syncing and self._schema_editor.edit_modified():
            self._schema_editor.edit_modified(False)
            self._schema_editor_status.set("Modified — not yet registered")

    def _load_schema_file(self):
        selected = filedialog.askopenfilename(
            parent=self.top,
            initialdir=str(self._default_folder),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Load Schema File")
        if not selected:
            return
        try:
            text = Path(selected).read_text("utf-8")
            schema_def = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            self._status.set(f"Load failed: {exc}")
            return
        self._syncing = True
        try:
            self._schema_editor.delete("1.0", "end")
            self._schema_editor.insert("1.0", json.dumps(schema_def, indent=2))
            self._schema_editor.edit_modified(False)
        finally:
            self._syncing = False
        self._schema_editor_status.set("Loaded from file — not yet registered")

    def _register_schema(self):
        if not self._spine:
            self._status.set("No spine loaded — create or open one first.")
            return
        raw = self._schema_editor.get("1.0", "end-1c").strip()
        if not raw:
            self._status.set("Schema editor is empty.")
            return
        try:
            schema_def = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._status.set(f"Invalid JSON: {exc}")
            return
        try:
            key = self._spine.register_schema(schema_def)
        except SpineError as exc:
            self._status.set(f"Register failed: {exc}")
            return
        self._schema_editor.edit_modified(False)
        self._schema_editor_status.set(f"Registered: {key}")
        self._refresh_schema_list()
        self._schema_var.set(key)
        self._on_schema_selected()
        self._status.set(f"Schema registered: {key}")

    def _copy_schema_json(self):
        text = self._schema_editor.get("1.0", "end-1c").strip()
        if text:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            self._status.set("Schema JSON copied.")

    # ── Form ──────────────────────────────────────────────────────────────

    def _rebuild_form(self):
        for w in self._form_inner.winfo_children():
            w.destroy()
        self._field_widgets.clear()
        if not self._schema_def:
            tk.Label(self._form_inner,
                     text="No schema loaded.",
                     bg=self.colors["panel_bg"], fg=self.colors["muted_text"],
                     font=("Arial", 9)).grid(row=0, column=0, padx=20, pady=16)
            self._update_preview()
            return
        c = self.colors
        props = self._schema_def.get("properties", {})
        self._form_inner.columnconfigure(1, weight=1)
        for row, (key, prop) in enumerate(props.items()):
            label = prop.get("label", key)
            self.label(self._form_inner, text=label, panel=True).grid(
                row=row, column=0, sticky="nw", padx=(20, 6), pady=8)
            w = _field_widget(self._form_inner, prop, c)
            w.grid(row=row, column=1, sticky="ew", padx=(0, 20), pady=8)
            self._field_widgets[key] = w
            if hasattr(w, "_var") and w._var is not None:
                w._var.trace_add("write", self._on_field_changed)
            elif isinstance(w, tk.Text):
                w.bind("<<Modified>>", self._on_text_field_changed)
        self._update_preview()

    def _on_field_changed(self, *_):
        if not self._syncing:
            self._update_preview()

    def _on_text_field_changed(self, event):
        if not self._syncing and event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._update_preview()

    def _read_form_data(self) -> dict:
        if not self._schema_def:
            return {}
        props = self._schema_def.get("properties", {})
        return {k: _get_value(self._field_widgets[k], prop)
                for k, prop in props.items()
                if k in self._field_widgets}

    def _update_preview(self):
        data = self._read_form_data()
        text = json.dumps(data, indent=2)
        self._preview.configure(state="normal")
        self._preview.delete("1.0", "end")
        self._preview.insert("1.0", text)
        self._preview.configure(state="disabled")

    # ── Append entry ──────────────────────────────────────────────────────

    def _append_entry(self):
        if not self._spine:
            self._status.set("No spine loaded.")
            return
        if not self._schema_key:
            self._status.set("No schema selected.")
            return
        data = self._read_form_data()
        try:
            self._spine.append_entry(self._schema_key, data)
        except SpineError as exc:
            self._status.set(f"Append failed: {exc}")
            return
        self._entry_count += 1
        self._browse_index = self._entry_count - 1  # advance to the new entry
        self._status.set(
            f"{self._spine.name}.jsonl  |  {self._schema_key}  |  {self._entry_count} entries")
        self._refresh_browse()

    def _copy_entry_json(self):
        text = self._preview.get("1.0", "end-1c").strip()
        if text:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            self._status.set("Entry JSON copied.")

    # ── Browse ────────────────────────────────────────────────────────────

    def _refresh_browse(self):
        self._browse_text.configure(state="normal")
        self._browse_text.delete("1.0", "end")

        if not self._spine:
            self._browse_text.configure(state="disabled")
            return

        mode = self._browse_mode.get()
        if mode == _BROWSE_ALL:
            self._render_entire_spine()
        elif mode == _BROWSE_SINGLE:
            self._render_single_entry()
        elif mode == _BROWSE_RAW:
            self._render_raw()

        self._browse_text.configure(state="disabled")

    def _render_entire_spine(self):
        entries = self._spine.read_entries()
        if not entries:
            self._browse_text.insert("1.0", "(no entries)\n")
            return
        for i, entry in enumerate(entries, 1):
            self._browse_text.insert("end",
                f"── ENTRY {i}  {entry.get('schema', '')}  "
                f"{entry.get('ts', '')[:19]}\n", "entry_sep")
            self._browse_text.insert("end",
                self._format_entry_readable(entry) + "\n\n")

    def _render_single_entry(self):
        entries = self._spine.read_entries()
        total = len(entries)
        if total == 0:
            self._record_label.configure(text="No entries")
            return
        idx = max(0, min(self._browse_index, total - 1))
        self._browse_index = idx
        self._record_label.configure(text=f"Entry {idx + 1} of {total}")
        self._browse_text.insert("1.0", self._format_entry_readable(entries[idx]))

    def _render_raw(self):
        records = self._spine.read_all()
        if not records:
            self._browse_text.insert("1.0", "(empty spine)\n")
            return
        for record in records:
            kind = record.get("kind", "unknown")
            if kind == "schema":
                hdr = (f"[schema]  {record.get('id', '')}@{record.get('version', '')} "
                       f" {record.get('ts', '')[:19]}\n")
                self._browse_text.insert("end", hdr, "schema_hdr")
            else:
                hdr = (f"[entry]   {record.get('schema', '')} "
                       f" {record.get('ts', '')[:19]}\n")
                self._browse_text.insert("end", hdr, "entry_hdr")
            self._browse_text.insert("end",
                json.dumps(record, indent=2, ensure_ascii=False) + "\n\n")

    def _format_entry_readable(self, entry: dict) -> str:
        schema_key = entry.get("schema", "")
        data = entry.get("data", {})
        labels = {}
        if self._spine and schema_key:
            try:
                schema_def = self._spine.load_schema(schema_key)
                labels = {k: v.get("label", k)
                          for k, v in schema_def.get("properties", {}).items()}
            except SpineError:
                pass
        lines = []
        for field, value in data.items():
            label = labels.get(field, field)
            lines.append(label)
            if isinstance(value, str) and "\n" in value:
                for ln in value.splitlines():
                    lines.append(f"  {ln}")
            elif isinstance(value, (dict, list)):
                lines.append(f"  {json.dumps(value)}")
            else:
                lines.append(f"  {value}")
            lines.append("")
        lines.append("─" * 50)
        lines.append(f"schema:  {schema_key}")
        ts = entry.get("ts", "")
        if ts:
            lines.append(f"ts:      {ts}")
        return "\n".join(lines)

    def _browse_prev(self):
        if self._browse_index > 0:
            self._browse_index -= 1
            self._refresh_browse()

    def _browse_next(self):
        entries = self._spine.read_entries() if self._spine else []
        if self._browse_index < len(entries) - 1:
            self._browse_index += 1
            self._refresh_browse()
