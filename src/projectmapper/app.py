# ==============================================================================
# ProjectMapper
# Tk application shell; shared state, scanning, writes, and tools live in packages.
# ==============================================================================

import sys
import os
import platform
import subprocess
import threading
import queue
import traceback
import copy
import hashlib
import time
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk, messagebox
import tkinter.font as tkFont
if __package__:
    from .tree_view import TreeProjection
    from .tools import (PatchError, validate_target, PatcherWindow,
                        TextToucherWindow, TextEditorWindow, ProjectPatcherWindow, BackupsWindow,
                        HistoryWindow, SettingsWindow, SchemaToucherWindow)
    from .tools.ui_base import attach_tooltip
    from .core import format_diagnostics, scan_project_tree
else:
    from tree_view import TreeProjection
    from tools import (PatchError, validate_target, PatcherWindow,
                       TextToucherWindow, TextEditorWindow, ProjectPatcherWindow, BackupsWindow,
                       HistoryWindow, SettingsWindow, SchemaToucherWindow)
    from tools.ui_base import attach_tooltip
    from core import format_diagnostics, scan_project_tree


if __package__:
    from .core.config import (
        APP_NAME, APP_VERSION, SOURCE_ROOT,
        S_CHECKED, S_UNCHECKED,
        TREE_MD_SUFFIX, FILEDUMP_MD_SUFFIX, COMBINED_MD_SUFFIX,
        _detect_sidecar_project_root,
    )
else:
    from core.config import (
        APP_NAME, APP_VERSION, SOURCE_ROOT,
        S_CHECKED, S_UNCHECKED,
        TREE_MD_SUFFIX, FILEDUMP_MD_SUFFIX, COMBINED_MD_SUFFIX,
        _detect_sidecar_project_root,
    )





MAX_LOG_LINES = 2000  # the main log keeps only recent lines; History keeps operations


THEME = {
    "app_bg": "#161A1F",
    "panel_bg": "#1E252D",
    "panel_alt_bg": "#273241",
    "field_bg": "#263140",
    "field_bg_alt": "#2D3948",
    "tree_bg": "#1A212B",
    "log_bg": "#10161E",
    "status_bg": "#0D1117",
    "status_text": "#89D6A0",
    "text": "#E7EDF4",
    "muted_text": "#97A4B3",
    "field_text": "#D6E2EE",
    "heading_bg": "#2A3441",
    "heading_text": "#F3F6F9",
    "selection": "#3B7E8D",
    "accent": "#C56F3D",
    "accent_hover": "#D78251",
    "secondary": "#2E7081",
    "secondary_hover": "#3A8EA2",
    "success": "#2F8E6A",
    "success_hover": "#3AA27C",
    "linked": "#7652A3",
    "linked_hover": "#8C65BA",
    "danger": "#B75A4D",
    "danger_hover": "#CB6B5E",
    "checkbox_checked": "#C56F3D",
    "checkbox_border": "#728195",
    "log_text": "#E1E7EE",
    "log_accent": "#89D6A0",
    # Diff highlighting on tree_bg; each is at least 7:1 contrast (WCAG AAA).
    "diff_header": "#9DB7D5",
    "diff_hunk": "#C4A5EA",
    "diff_add": "#8FD6A8",
    "diff_remove": "#F0A096",
}


if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")


if __package__:
    from .core.helpers import *
else:
    from core.helpers import *


if __package__:
    from .core.exports import *
else:
    from core.exports import *


if __package__:
    from .core.snapshots import *
else:
    from core.snapshots import *


if __package__:
    from .core.exclusions import *
else:
    from core.exclusions import *

















# Enough height for the path row, several tree rows, all action rows and a few log lines.
MIN_WINDOW_HEIGHT = 560


class ProgressPopup:
    def __init__(self, parent, title="Processing", on_cancel=None):
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.geometry("560x320")
        self.top.configure(bg=THEME["panel_bg"])
        self.top.transient(parent)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", self._on_close_attempt)

        self.on_cancel = on_cancel
        self.is_cancelled = False

        tk.Label(
            self.top,
            text=f"{title}...",
            fg=THEME["text"],
            bg=THEME["panel_bg"],
            font=("Arial", 12, "bold"),
        ).pack(pady=10)

        self.log_display = scrolledtext.ScrolledText(
            self.top,
            height=10,
            bg=THEME["log_bg"],
            fg=THEME["log_accent"],
            insertbackground=THEME["log_text"],
            font=("Consolas", 9),
        )
        self.log_display.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.cancel_btn = tk.Button(
            self.top,
            text="CANCEL OPERATION",
            bg=THEME["danger"],
            fg=THEME["text"],
            activebackground=THEME["danger_hover"],
            activeforeground=THEME["text"],
            font=("Arial", 10, "bold"),
            command=self.trigger_cancel,
        )
        attach_tooltip(self.cancel_btn, "progress.cancel", THEME)
        self.cancel_btn.pack(pady=10)

    def update_text(self, text):
        self.log_display.insert(tk.END, text + "\n")
        self.log_display.see(tk.END)

    def trigger_cancel(self):
        self.is_cancelled = True
        self.update_text("!!! CANCELLATION REQUESTED - STOPPING !!!")
        self.cancel_btn.config(state=tk.DISABLED, text="Stopping...")
        if self.on_cancel:
            self.on_cancel()

    def _on_close_attempt(self):
        if not self.is_cancelled:
            self.trigger_cancel()

    def close(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class ExclusionsPopup:
    SOURCE_LABELS = {
        "hardcoded_folder": "Built-in · folder",
        "predefined_filename": "Built-in · filename",
        "dynamic_user_pattern": "Custom · filename",
        "gitignore_dirname": ".gitignore · folder",
        "gitignore_file_pattern": ".gitignore · filename",
        "gitignore_path_pattern": ".gitignore · path",
    }

    def __init__(self, app):
        self.app = app
        self.selected = set()
        self.project_root = None
        self.top = tk.Toplevel(app.root)
        self.top.title("Manage Exclusions")
        self.top.configure(bg=THEME["panel_bg"])
        self.top.geometry("800x560")
        self.top.minsize(690, 420)
        self.top.transient(app.root)
        self.top.bind("<Escape>", lambda _event: self.top.destroy())

        tk.Label(self.top, text="Manage exclusions", bg=THEME["panel_bg"],
                 fg=THEME["text"], font=("Arial", 14, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(self.top, text="Exclude checked: hide matching files/folders from the mapper.\n"
                 "Exclude unchecked: allow matches, unless another rule excludes them.\n"
                 "Select: choose rows for batch actions only. Patterns use wildcards (*.log), not regex.\n"
                 "Changes apply immediately for this session; .gitignore stays unchanged.",
                 bg=THEME["panel_bg"], fg=THEME["muted_text"], justify=tk.LEFT).pack(anchor="w", padx=16)
        self.project_label = tk.Label(self.top, bg=THEME["panel_bg"], fg=THEME["muted_text"], anchor="w")
        self.project_label.pack(fill=tk.X, padx=16, pady=(6, 0))
        self._checkbutton(self.top, app.widgets["respect_exclusions"], app.apply_exclusion_settings,
                          "Apply exclusion rules (checked = hide matches)", tooltip_id="exclusions.apply").pack(anchor="w", padx=12, pady=6)

        add_row = tk.Frame(self.top, bg=THEME["panel_bg"])
        add_row.pack(fill=tk.X, padx=16, pady=(0, 10))
        tk.Label(add_row, text="Hide filenames matching:", bg=THEME["panel_bg"], fg=THEME["text"]).pack(side=tk.LEFT)
        self.entry = tk.Entry(add_row, bg=THEME["field_bg"], fg=THEME["text"], insertbackground=THEME["text"])
        attach_tooltip(self.entry, "exclusions.pattern", THEME)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.entry.bind("<Return>", lambda _event: self.add_pattern())
        app._make_button(add_row, "Add", self.add_pattern, THEME["accent"], THEME["accent_hover"],
                         tooltip_id="exclusions.add").pack(side=tk.RIGHT)

        list_frame = tk.Frame(self.top, bg=THEME["tree_bg"])
        self.canvas = tk.Canvas(list_frame, bg=THEME["tree_bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.rows_frame = tk.Frame(self.canvas, bg=THEME["tree_bg"])
        self.rows_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.columnconfigure(2, weight=1)
        self.rows_frame.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.rows_window, width=event.width))
        self.top.bind("<MouseWheel>", self._scroll)
        self.top.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-1, "units"))
        self.top.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(1, "units"))

        self.status = tk.Label(self.top, bg=THEME["panel_bg"], fg=THEME["muted_text"], anchor="w")
        actions = tk.Frame(self.top, bg=THEME["panel_bg"])
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=(0, 12))
        self.batch_buttons = []
        for label, command, color, tooltip_id in (
            ("Select All", lambda: self.select_all(True), "panel_alt_bg", "exclusions.select_all"),
            ("Deselect All", lambda: self.select_all(False), "panel_alt_bg", "exclusions.deselect_all"),
            ("Exclude Selected", lambda: self.set_selected_enabled(True), "secondary", "exclusions.exclude_selected"),
            ("Allow Selected", lambda: self.set_selected_enabled(False), "secondary", "exclusions.allow_selected"),
            ("Delete Selected", self.delete_selected, "danger", "exclusions.delete_selected"),
        ):
            button = app._make_button(actions, label, command, THEME[color], THEME["field_bg_alt"],
                                      tooltip_id=tooltip_id)
            button.pack(side=tk.LEFT, padx=3)
            if label.endswith("Selected"):
                self.batch_buttons.append(button)
        self.status.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=8)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=16)
        self.refresh()

    def _checkbutton(self, parent, variable, command, text="", background=None, *, tooltip_id):
        bg = background or THEME["panel_bg"]
        widget = tk.Checkbutton(parent, text=text, variable=variable, command=command,
                                bg=bg, fg=THEME["text"], selectcolor=THEME["tree_bg"],
                                activebackground=bg, activeforeground=THEME["text"])
        return attach_tooltip(widget, tooltip_id, THEME)

    def _scroll(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def refresh(self):
        policy = self.app.exclusion_policy
        if self.project_root != policy.gitignore_root:
            self.selected.clear()
            self.project_root = policy.gitignore_root
        self.project_label.config(text=f"Project: {self.app.selected_root}")
        self.rules = [rule for rule in policy.collect_rules() if rule["rule_type"] != "toggle"]
        keys = {policy.rule_key(rule["source"], rule["pattern"]) for rule in self.rules}
        self.selected.intersection_update(keys)
        y_position = self.canvas.yview()[0]
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self.selection_vars = {}
        for column, title in enumerate(("Select", "Exclude (hide)", "Matching pattern", "Source / match type")):
            tk.Label(self.rows_frame, text=title, bg=THEME["heading_bg"], fg=THEME["heading_text"],
                     anchor="w" if column > 1 else "center", padx=10, pady=7).grid(row=0, column=column, sticky="nsew")
        for index, rule in enumerate(self.rules, 1):
            key = policy.rule_key(rule["source"], rule["pattern"])
            bg = THEME["tree_bg"] if index % 2 else THEME["panel_bg"]
            selected = tk.BooleanVar(value=key in self.selected)
            enabled = tk.BooleanVar(value=bool(rule["active"]))
            self.selection_vars[key] = selected
            self._checkbutton(self.rows_frame, selected,
                              lambda k=key, v=selected: self.select_row(k, v.get()), background=bg,
                              tooltip_id="exclusions.row_select").grid(row=index, column=0, sticky="nsew")
            self._checkbutton(self.rows_frame, enabled,
                              lambda r=rule, v=enabled: self.toggle_rule(r, v.get()), background=bg,
                              tooltip_id="exclusions.rule").grid(row=index, column=1, sticky="nsew")
            tk.Label(self.rows_frame, text=rule["pattern"], bg=bg, fg=THEME["text"], anchor="w",
                     padx=10, pady=5, wraplength=330, justify=tk.LEFT).grid(row=index, column=2, sticky="nsew")
            tk.Label(self.rows_frame, text=self.SOURCE_LABELS[rule["source"]], bg=bg,
                     fg=THEME["muted_text"], anchor="w", padx=10).grid(row=index, column=3, sticky="nsew")
        if not self.rules:
            tk.Label(self.rows_frame, text="No exclusion rules. Add a filename pattern above.",
                     bg=THEME["tree_bg"], fg=THEME["muted_text"], pady=24).grid(row=1, column=0, columnspan=4)
        self.rows_frame.update_idletasks()
        self.canvas.yview_moveto(y_position)
        self.update_status()

    def update_status(self):
        enabled = sum(rule["active"] for rule in self.rules)
        paused = " · Exclusions OFF: matches are allowed" if not self.app.exclusion_policy.respect_exclusions else ""
        self.status.config(text=f"{len(self.rules)} rules · {enabled} checked to exclude · {len(self.selected)} selected{paused}")
        for button in self.batch_buttons:
            button.config(state=tk.NORMAL if self.selected else tk.DISABLED)

    def select_row(self, key, selected):
        if selected:
            self.selected.add(key)
        else:
            self.selected.discard(key)
        self.update_status()

    def select_all(self, selected):
        self.selected = set(self.selection_vars) if selected else set()
        for variable in self.selection_vars.values():
            variable.set(selected)
        self.update_status()

    def toggle_rule(self, rule, enabled):
        self.app.action("exclusions.update", {"operation": "enable", "source": rule["source"], "pattern": rule["pattern"], "enabled": enabled})
        self.app.exclusions_changed()

    def set_selected_enabled(self, enabled):
        policy = self.app.exclusion_policy
        for rule in self.rules:
            if policy.rule_key(rule["source"], rule["pattern"]) in self.selected:
                self.app.action("exclusions.update", {"operation": "enable", "source": rule["source"], "pattern": rule["pattern"], "enabled": enabled})
        self.app.exclusions_changed()

    def delete_selected(self):
        policy = self.app.exclusion_policy
        count = len(self.selected)
        for rule in self.rules:
            if policy.rule_key(rule["source"], rule["pattern"]) in self.selected:
                self.app.action("exclusions.update", {"operation": "delete", "source": rule["source"], "pattern": rule["pattern"]})
        self.selected.clear()
        self.app.log_message(f"Deleted {count} exclusion rules for this session.")
        self.app.exclusions_changed()

    def add_pattern(self):
        self.app.add_exclusion_from_entry(self.entry)


if __package__:
    from .application.controller import create_application
    from .application.desktop import perform
    from .application.errors import describe
else:
    from application.controller import create_application
    from application.desktop import perform
    from application.errors import describe


class ProjectMapperApp:
    @property
    def selected_root(self):
        return self.project_state.root if hasattr(self, "project_state") else getattr(self, "_selected_root", None)
    @selected_root.setter
    def selected_root(self, value):
        if hasattr(self, "project_state"):
            self.project_state.root = Path(value) if value else None
        else:
            self._selected_root = Path(value) if value else None
    def _state_property(name):
        def get(self):
            state = getattr(self, "project_state", None)
            return getattr(state, name) if state is not None else getattr(self, "_" + name, None)
        def set_(self, value):
            state = getattr(self, "project_state", None)
            if state is not None: setattr(state, name, value)
            else: setattr(self, "_" + name, value)
        return property(get, set_)
    scan_revision = _state_property("scan_revision")
    applied_scan_revision = _state_property("applied_scan_revision")
    latest_source_mtime = _state_property("latest_source_mtime")
    latest_snapshot_path = _state_property("snapshot_path")
    transformed_paths = _state_property("transformed_paths")
    running_tasks = _state_property("active_operations")
    def _model_property(name, owner="controller"):
        def get(self):
            model = getattr(self, owner, None)
            return getattr(model, name) if model is not None else getattr(self, "_" + name, {})
        def set_(self, value):
            model = getattr(self, owner, None)
            if model is not None: setattr(model, name, value)
            else: setattr(self, "_" + name, value)
        return property(get, set_)
    folder_item_states = _model_property("selection")
    tree_rows = _model_property("rows")
    scan_skipped_paths = _model_property("skipped")

    def __init__(self, root: tk.Tk, initial_root=None):
        self.root = root
        self.initial_root = Path(initial_root or Path.cwd()).resolve()
        self.theme = THEME
        self.gui_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.widgets = {}
        self.current_progress_popup = None
        self.controller, self.approve_action = create_application(self.initial_root)
        self.controller.scan_fn = lambda root, policy, stop: scan_project_tree(root, policy, stop)
        self.project_state = self.controller.state
        self.action_operations = set()
        self.desktop_operations = set()
        self.editor_windows = set()
        self.syncing_controls = False
        self.controller.dispatcher.subscribe(lambda event: self.gui_queue.put(lambda: self.on_action_event(event)))
        # Hidden failures (decision D): one concise log line; the traceback stays in History.
        self.controller.history.subscribe(
            lambda record: self.gui_queue.put(lambda: self._announce_problem(record))
            if record["category"] == "internal" else None)
        self.root.report_callback_exception = self._report_callback_exception
        self.root.bind("<Destroy>", self._destroy_actions, add="+")
        self.latest_snapshot_path = None
        self.latest_source_mtime = 0.0
        self.state_lock = self.controller.lock
        self.folder_item_states = {}
        self.tree_rows = []
        self.scan_skipped_paths = []
        self.exclusion_policy = self.controller.policy
        self.exclusions_popup = None
        self.scan_revision = 0
        self.applied_scan_revision = -1
        self.scan_pending = False
        self.scan_after_id = None
        self.scan_use_popup = False
        self.exclusions_dirty = False
        self.transformed_paths = self.project_state.transformed_paths
        self.icon_imgs = {}
        self.tree_projection = None
        self._grid_resize_timer = None
        self._git_busy = False
        self._editor_stale_check_time = 0.0
        self._create_tree_icons()

        self._setup_styles()
        self._setup_ui()
        self.process_gui_queue()
        self._activity_blinker()
        self.log_message("ProjectMapper loaded. Curate the tree, then compile a SQLite snapshot.")
        self.root.after(250, self.request_rescan_tree_silent)

    def action(self, name, payload=None, **options):
        return perform(self, name, payload, **options)

    def _destroy_actions(self, event):
        if event.widget is self.root:
            self.controller.close()

    def on_action_event(self, event):
        if event.type == "progress":
            # Progress is counted in History; the log is not flooded with one line per update.
            message = event.payload.get("message", "Working")
            self.widgets["status_var"].set(message)
            if self.current_progress_popup:
                self.current_progress_popup.update_text(message)
        if event.type == "succeeded" and event.payload.get("paths"):
            self._editor_check_stale()
            changed = {Path(p).resolve() for p in event.payload.get("paths", [])}
            for w in list(self.editor_windows):
                if not w.top.winfo_exists():
                    self.editor_windows.discard(w)
                elif w.session.path.resolve() in changed:
                    w.stale = True
                    w.status.set("Source changed or is unavailable. Your edits are preserved; reopen before saving.")
            self.request_rescan_tree_silent()
        if event.type == "succeeded" and event.operation_id not in self.desktop_operations:
            if event.action == "project.set_root":
                self.widgets["selected_root_var"].set(str(self.selected_root))
                self.populate_tree([])
                self.request_rescan_tree_silent()
            elif event.action == "project.scan":
                if event.payload.get("revision") == self.scan_revision:
                    self.populate_tree(self.tree_rows)
                    self.scan_pending = False
            elif event.action == "selection.set":
                self.refresh_tree_visuals()
            elif event.action == "exclusions.update":
                self.widgets["respect_exclusions"].set(self.exclusion_policy.respect_exclusions)
                self.refresh_exclusions_popup()
                self.request_rescan_tree_silent()
            elif event.action == "capture.configure":
                self.syncing_controls = True
                try:
                    self.widgets["include_binary_blobs"].set(self.controller.include_binary)
                finally:
                    self.syncing_controls = False
        if event.type in ("failed", "recovery_required"):
            self.log_message(describe(event.payload.get("error") or {"code": event.type}), "ERROR")
        if event.type == "recovery_required":
            self.log_message("Open Backups… to review recovery and pre-restore generations.", "WARNING")

    def _report_callback_exception(self, exc_type, exc, tb):
        """Tk-wide: button and event callbacks in every window of this interpreter."""
        trace = "".join(traceback.format_exception(exc_type, exc, tb))
        self.controller.history.record_problem("ui_callback", f"{exc_type.__name__}: {exc}", trace)

    def _announce_problem(self, record):
        try:
            source = record["action"].split(".", 1)[1]
            self.log_message(f"Internal problem ({source}): {record['error']['message']} — see History", "ERROR")
        except Exception:
            pass  # announcing must never raise back into the queue that reports failures

    def run_diagnostics(self):
        report = self.action("application.diagnostics")
        text = format_diagnostics(report)
        self.log_message(text, "INFO" if report["ok"] else "ERROR")
        messagebox.showinfo("Diagnostics", text, parent=self.root) if report["ok"] else messagebox.showwarning("Diagnostics", text, parent=self.root)
        return report

    def mark_project_dirty(self, reason, paths=()):
        self.action("project.dirty", {"reason": reason, "paths": [str(p) for p in paths]})
        self.transformed_paths = self.project_state.transformed_paths
        self.latest_snapshot_path = None

    def report_error(self, title, exc):
        self.log_message(f"{title}: {exc}", "ERROR")
        messagebox.showerror(title, str(exc), parent=self.root)

    def _create_tree_icons(self):
        sz = 16
        border = THEME["checkbox_border"]
        fill = THEME["checkbox_checked"]
        bg = THEME["tree_bg"]

        img_unchecked = tk.PhotoImage(master=self.root, width=sz, height=sz)
        img_unchecked.put((bg,), to=(0, 0, sz, sz))
        for i in range(sz):
            img_unchecked.put((border,), to=(i, 0))
            img_unchecked.put((border,), to=(i, sz - 1))
            img_unchecked.put((border,), to=(0, i))
            img_unchecked.put((border,), to=(sz - 1, i))
        for i in range(sz):
            img_unchecked.put((border,), to=(i, 1))
            img_unchecked.put((border,), to=(i, sz - 2))
            img_unchecked.put((border,), to=(1, i))
            img_unchecked.put((border,), to=(sz - 2, i))
        self.icon_imgs[S_UNCHECKED] = img_unchecked

        img_checked = tk.PhotoImage(master=self.root, width=sz, height=sz)
        img_checked.put((fill,), to=(0, 0, sz, sz))
        wh = "#FFFFFF"
        # Draw a checkmark: short leg then long leg
        for dx, dy in [(4, 8), (4, 9), (5, 9), (5, 10), (6, 10), (6, 11),
                        (7, 9), (7, 10), (8, 8), (8, 9), (9, 7), (9, 8),
                        (10, 6), (10, 7), (11, 5), (11, 6)]:
            img_checked.put((wh,), to=(dx, dy))
        self.icon_imgs[S_CHECKED] = img_checked
        # === [SECTION: TK_APP_INIT] END ===


    def _setup_styles(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.default_ui_font = "Arial"
        try:
            if "DejaVu Sans" in tkFont.families():
                self.default_ui_font = "DejaVu Sans"
        except tk.TclError:
            pass

        style.configure(
            "Treeview",
            background=THEME["tree_bg"],
            foreground=THEME["text"],
            fieldbackground=THEME["tree_bg"],
            borderwidth=0,
            font=(self.default_ui_font, 10),
            rowheight=24,
        )
        style.configure(
            "Treeview.Heading",
            background=THEME["heading_bg"],
            foreground=THEME["heading_text"],
            relief=tk.FLAT,
        )
        # Paned-window sash: give it a visible colour and a grabbable width.
        # In the clam theme, TPanedwindow.background IS the sash fill colour.
        style.configure("TPanedwindow", background=THEME["secondary"])
        try:
            style.configure("Sash", sashthickness=6, gripcount=0)
        except tk.TclError:
            style.configure("Sash", sashthickness=6)


    def _setup_ui(self):
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.configure(bg=THEME["app_bg"])
        self.root.geometry("1200x850")

        self.widgets["selected_root_var"] = tk.StringVar(value=str(self.initial_root))

        # -- Header: project identity + settings gear --
        header = tk.Frame(self.root, bg=THEME["panel_bg"])
        header.pack(fill=tk.X, padx=10, pady=(6, 2))

        gear_btn = tk.Button(
            header,
            text="⚙",
            command=self.open_settings,
            bg=THEME["panel_bg"],
            fg=THEME["muted_text"],
            activebackground=THEME["field_bg_alt"],
            activeforeground=THEME["text"],
            relief=tk.FLAT,
            bd=0,
            font=("", 13),
            cursor="hand2",
        )
        gear_btn.pack(side=tk.RIGHT, padx=(4, 2), pady=2)
        attach_tooltip(gear_btn, "main.settings", THEME)

        self.widgets["project_name_label"] = tk.Label(
            header,
            text=self.initial_root.name,
            bg=THEME["panel_bg"],
            fg=THEME["text"],
            font=("", 11, "bold"),
            anchor="w",
        )
        self.widgets["project_name_label"].pack(side=tk.LEFT, padx=(6, 4), pady=4)

        self.widgets["project_path_label"] = tk.Label(
            header,
            text=str(self.initial_root),
            bg=THEME["panel_bg"],
            fg=THEME["muted_text"],
            font=("", 9),
            anchor="w",
        )
        self.widgets["project_path_label"].pack(side=tk.LEFT, padx=(0, 4), pady=4, fill=tk.X, expand=True)

        def _sync_project_display(*_):
            root_str = self.widgets["selected_root_var"].get()
            try:
                p = Path(root_str)
                self.widgets["project_name_label"].config(text=p.name or root_str)
                self.widgets["project_path_label"].config(text=root_str)
            except Exception:
                pass
        self.widgets["selected_root_var"].trace_add("write", _sync_project_display)

        outer_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        outer_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        top_paned = ttk.PanedWindow(outer_paned, orient=tk.HORIZONTAL)
        outer_paned.add(top_paned, weight=3)

        tree_frame = tk.Frame(top_paned, bg=THEME["panel_bg"])
        self.widgets["folder_tree"] = ttk.Treeview(
            tree_frame,
            show="tree headings",
            columns=("nav_up", "nav_down", "size"),
            selectmode="browse",
        )
        attach_tooltip(self.widgets["folder_tree"], "main.tree", THEME)
        self.widgets["folder_tree"].heading("#0", text="Explorer")
        self.widgets["folder_tree"].heading("nav_up", text="↑")
        self.widgets["folder_tree"].heading("nav_down", text="↓")
        self.widgets["folder_tree"].heading("size", text="Size")
        self.widgets["folder_tree"].column("#0", width=420, minwidth=200)  # stretches with the window
        self.widgets["folder_tree"].column("nav_up", width=34, anchor="center", stretch=False)
        self.widgets["folder_tree"].column("nav_down", width=34, anchor="center", stretch=False)
        self.widgets["folder_tree"].column("size", width=120, anchor="e", stretch=False)
        self.widgets["folder_tree"].insert("", "end", text="Tree scanner pending", values=("", "", ""))

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.widgets["folder_tree"].yview)
        vsb.bind("<ButtonPress-1>", lambda event: self.tree_projection._user_navigation(event)
                 if self.tree_projection is not None else None)
        self.widgets["folder_tree"].configure(yscrollcommand=vsb.set)
        self.widgets["folder_tree"].pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.widgets["folder_tree"].bind("<ButtonRelease-1>", self.on_tree_item_click)
        self.widgets["folder_tree"].bind("<ButtonRelease-3>", self.on_file_context_menu)
        self.widgets["folder_tree"].bind("<Shift-F10>", self.on_file_context_menu)
        self.widgets["folder_tree"].bind("<<TreeviewOpen>>", self.on_tree_open)
        self.widgets["folder_tree"].bind("<<TreeviewSelect>>", self.on_tree_selection_change)
        top_paned.add(tree_frame, weight=1)

        content_frame = tk.Frame(top_paned, bg=THEME["panel_bg"])
        self._setup_content_panel(content_frame)
        top_paned.add(content_frame, weight=2)

        action_frame = tk.Frame(outer_paned, bg=THEME["panel_bg"])

        # --- Tabbed toolbar ---
        tab_bar = tk.Frame(action_frame, bg=THEME["heading_bg"])
        tab_bar.pack(fill=tk.X)
        self._toolbar_tab_buttons = {}
        self._toolbar_tab_frames = {}
        self._active_toolbar_tab = None

        toolbar_body = tk.Frame(action_frame, bg=THEME["panel_bg"])
        toolbar_body.pack(fill=tk.X)

        # -- Snapshot tab --
        snap_frame = tk.Frame(toolbar_body, bg=THEME["panel_bg"])
        self._toolbar_tab_frames["Snapshot"] = snap_frame

        btn_row = tk.Frame(snap_frame, bg=THEME["panel_bg"])
        btn_row.pack(fill=tk.X, padx=5, pady=(6, 2))
        tool_row = tk.Frame(snap_frame, bg=THEME["panel_bg"])
        tool_row.pack(fill=tk.X, padx=5, pady=2)

        self._make_button(btn_row, "Compile Snapshot", self.compile_snapshot_placeholder, THEME["accent"], THEME["accent_hover"], bold=True, tooltip_id="main.compile").pack(side=tk.LEFT, padx=4)
        self._make_button(btn_row, "Export Tree MD", self.export_tree_markdown, THEME["panel_alt_bg"], THEME["field_bg_alt"], tooltip_id="main.export_tree").pack(side=tk.LEFT, padx=4)
        self._make_button(btn_row, "Export Filedump MD", self.export_filedump_markdown, THEME["panel_alt_bg"], THEME["field_bg_alt"], tooltip_id="main.export_filedump").pack(side=tk.LEFT, padx=4)
        self._make_button(btn_row, "Export Tree+Dump MD", self.export_combined_markdown, THEME["panel_alt_bg"], THEME["field_bg_alt"], tooltip_id="main.export_combined").pack(side=tk.LEFT, padx=4)
        self._make_button(tool_row, "Open Output Folder", self.open_output_folder, THEME["success"], THEME["success_hover"], tooltip_id="main.output_folder").pack(side=tk.LEFT, padx=4)
        vendor_button = self._make_button(tool_row, "Export Vendor App", self.export_vendor_app, THEME["secondary"], THEME["secondary_hover"], tooltip_id="main.vendor_export")
        if SOURCE_ROOT is None:  # Installed package: there is no source checkout to vendor.
            vendor_button.configure(state=tk.DISABLED)
        vendor_button.pack(side=tk.LEFT, padx=4)
        self.widgets["vendor_export_button"] = vendor_button
        self._make_button(tool_row, "Diagnostics", self.run_diagnostics, THEME["panel_alt_bg"], THEME["field_bg_alt"], tooltip_id="main.diagnostics").pack(side=tk.RIGHT, padx=4)
        self.widgets["backups_button"] = self._make_button(tool_row, "Backups…", self.open_backups,
                                                           THEME["panel_alt_bg"], THEME["field_bg_alt"], tooltip_id="main.backups")
        self.widgets["backups_button"].pack(side=tk.RIGHT, padx=4)

        option_row = tk.Frame(snap_frame, bg=THEME["panel_bg"])
        option_row.pack(fill=tk.X, padx=5, pady=2)
        control_row = tk.Frame(snap_frame, bg=THEME["panel_bg"])
        control_row.pack(fill=tk.X, padx=5, pady=2)
        self.widgets["respect_exclusions"] = tk.BooleanVar(value=True)
        exclusion_toggle = tk.Checkbutton(
            option_row,
            text="Apply exclusions (hide matches)",
            variable=self.widgets["respect_exclusions"],
            command=self.apply_exclusion_settings,
            bg=THEME["panel_bg"],
            fg=THEME["text"],
            selectcolor=THEME["tree_bg"],
            activebackground=THEME["panel_bg"],
            activeforeground=THEME["text"],
        )
        attach_tooltip(exclusion_toggle, "main.apply_exclusions", THEME).pack(side=tk.LEFT, padx=6)
        self.widgets["include_tree_in_filedump"] = tk.BooleanVar(value=False)
        tree_export_toggle = tk.Checkbutton(
            option_row,
            text="Tree in filedump export",
            variable=self.widgets["include_tree_in_filedump"],
            bg=THEME["panel_bg"],
            fg=THEME["text"],
            selectcolor=THEME["tree_bg"],
            activebackground=THEME["panel_bg"],
            activeforeground=THEME["text"],
        )
        attach_tooltip(tree_export_toggle, "main.tree_export", THEME).pack(side=tk.LEFT, padx=6)
        self.widgets["include_binary_blobs"] = tk.BooleanVar(value=False)
        self.widgets["include_binary_blobs"].trace_add("write", lambda *_: None if self.syncing_controls else self.action("capture.configure", {"include_binary": bool(self.widgets["include_binary_blobs"].get())}))
        binary_toggle = tk.Checkbutton(
            option_row,
            text="Preserve binary blobs in DB",
            variable=self.widgets["include_binary_blobs"],
            bg=THEME["panel_bg"],
            fg=THEME["text"],
            selectcolor=THEME["tree_bg"],
            activebackground=THEME["panel_bg"],
            activeforeground=THEME["text"],
        )
        attach_tooltip(binary_toggle, "main.binary_blobs", THEME).pack(side=tk.LEFT, padx=6)
        tk.Label(control_row, text="Capture:", bg=THEME["panel_bg"],
                 fg=THEME["muted_text"], font=("Arial", 9)).pack(side=tk.LEFT, padx=(4, 0))
        self._make_button(control_row, "All", lambda: self.set_global_selection(S_CHECKED), THEME["panel_alt_bg"], THEME["field_bg_alt"], tooltip_id="main.select_all").pack(side=tk.LEFT, padx=2)
        self._make_button(control_row, "None", lambda: self.set_global_selection(S_UNCHECKED), THEME["panel_alt_bg"], THEME["field_bg_alt"], tooltip_id="main.select_none").pack(side=tk.LEFT, padx=2)
        self._make_button(control_row, "Add", self.add_exclusion_from_entry, THEME["accent"], THEME["accent_hover"], tooltip_id="main.add_exclusion").pack(side=tk.RIGHT, padx=2)
        self.widgets["exclusion_entry"] = tk.Entry(
            control_row,
            bg=THEME["field_bg_alt"],
            fg=THEME["field_text"],
            insertbackground=THEME["field_text"],
            width=22,
            relief=tk.FLAT,
        )
        attach_tooltip(self.widgets["exclusion_entry"], "main.exclusion_pattern", THEME)
        self.widgets["exclusion_entry"].pack(side=tk.RIGHT, padx=4)
        # Packed right to left: reads "Hide pattern: [entry] Add".
        tk.Label(control_row, text="Hide pattern:", bg=THEME["panel_bg"], fg=THEME["muted_text"]).pack(side=tk.RIGHT, padx=4)
        self._make_button(control_row, "Exclusions", self.manage_exclusions_popup, THEME["success"], THEME["success_hover"], tooltip_id="main.manage_exclusions").pack(side=tk.RIGHT, padx=2)
        self._make_button(control_row, "Rescan", self.request_rescan_tree, THEME["secondary"], THEME["secondary_hover"], tooltip_id="main.rescan").pack(side=tk.RIGHT, padx=2)

        # -- Git tab --
        git_frame = tk.Frame(toolbar_body, bg=THEME["panel_bg"])
        self._toolbar_tab_frames["Git"] = git_frame
        self._setup_git_toolbar(git_frame)

        # -- Tab buttons (created after both frames exist) --
        for tab_name in ("Snapshot", "Git"):
            btn = tk.Label(
                tab_bar, text=f"  {tab_name}  ", cursor="hand2",
                bg=THEME["heading_bg"], fg=THEME["muted_text"],
                font=("Arial", 9), padx=10, pady=4,
            )
            btn.pack(side=tk.LEFT, padx=(2, 0), pady=(2, 0))
            btn.bind("<Button-1>", lambda _e, n=tab_name: self._switch_toolbar_tab(n))
            self._toolbar_tab_buttons[tab_name] = btn
        self._switch_toolbar_tab("Snapshot")

        log_header = tk.Frame(action_frame, bg=THEME["panel_bg"])
        log_header.pack(fill=tk.X, padx=5, pady=(4, 0))
        tk.Label(log_header, text="LOG (recent lines; operations are kept in History)", bg=THEME["panel_bg"],
                 fg=THEME["muted_text"], font=("Arial", 9)).pack(side=tk.LEFT)
        self.widgets["history_button"] = self._make_button(log_header, "History…", self.open_history,
                                                           THEME["panel_alt_bg"], THEME["field_bg_alt"], tooltip_id="main.history")
        self.widgets["history_button"].pack(side=tk.RIGHT)

        self.widgets["log_box"] = scrolledtext.ScrolledText(
            action_frame,
            bg=THEME["log_bg"],
            fg=THEME["log_text"],
            insertbackground=THEME["log_text"],
            font=("Consolas", 9),
            state=tk.DISABLED,
            height=5,  # a starting height; the tree gets the room and the pane divider can be dragged
            width=60,  # a base width only; the log fills the window
        )
        self.widgets["log_box"].pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        outer_paned.add(action_frame, weight=1)

        self.widgets["status_var"] = tk.StringVar(value="Ready.")
        self.widgets["status_bar"] = tk.Label(
            self.root,
            textvariable=self.widgets["status_var"],
            bg=THEME["status_bg"],
            fg=THEME["status_text"],
            anchor="w",
        )
        # Packed before the pane so a short window shrinks the pane, never the status bar.
        self.widgets["status_bar"].pack(fill=tk.X, side=tk.BOTTOM, before=outer_paned)
        self.root.update_idletasks()
        self.root.minsize(self.root.winfo_reqwidth(), MIN_WINDOW_HEIGHT)

    def _make_button(self, parent, text, command, bg, active_bg, bold=False, *, tooltip_id):
        widget = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=THEME["text"],
            activebackground=active_bg,
            activeforeground=THEME["text"],
            font=("Arial", 10, "bold" if bold else "normal"),
            relief=tk.FLAT,
            cursor="hand2",
            padx=10,
            pady=6,
        )
        return attach_tooltip(widget, tooltip_id, THEME)


    def on_file_context_menu(self, event):
        tree = self.widgets["folder_tree"]
        keyboard = getattr(event, "keysym", "") == "F10"
        iid = tree.focus() if keyboard else tree.identify_row(event.y)
        if iid.startswith(TreeProjection.PLACEHOLDER):
            return "break"
        # Context selection must not toggle capture checkboxes.
        if iid:
            tree.selection_set(iid)
            tree.focus(iid)
        else:
            tree.selection_remove(*tree.selection())
            tree.focus("")
        # Empty space targets the current project root, never a previously selected row.
        path = Path(iid) if iid else self.selected_root
        x = tree.winfo_rootx() + 40 if keyboard else event.x_root
        y = tree.winfo_rooty() + 40 if keyboard else event.y_root
        self._show_path_context_menu(path, x, y)
        return "break"

    def _show_path_context_menu(self, path, x_root: int, y_root: int):
        """Build and display the right-click context menu for *path*.

        *path* may be a file, a directory, or None (falls back to the project
        root).  File-specific items are enabled only when path.is_file().
        """
        if path is None:
            path = self.selected_root
        if path is None:
            return
        previous_menu = getattr(self, "file_context_menu", None)
        if previous_menu is not None:
            previous_menu.destroy()
        menu = self.file_context_menu = tk.Menu(self.root, tearoff=False)
        allowed = path.is_file()
        label = "Tokenizing Patcher…" if allowed else "Tokenizing Patcher… (select a file)"
        try:
            validate_target(path)
        except PatchError:
            allowed = False
        menu.add_command(label=label, command=lambda: self.open_tokenizing_patcher(path),
                         state="normal" if allowed else "disabled")
        menu.add_command(label="Open Text Editor…", command=lambda: self.open_text_editor(path),
                         state="normal" if allowed else "disabled")
        folder = path.parent if path.is_file() else path
        can_create = folder.is_dir()
        try:
            validate_target(folder)
        except PatchError:
            can_create = False
        menu.add_command(label="New Text File…", command=lambda: self.open_text_toucher(folder),
                         state="normal" if can_create else "disabled")
        menu.add_command(label="Schema Toucher…", command=lambda: self.open_schema_toucher(folder),
                         state="normal")
        menu.add_separator()
        menu.add_command(label="Delete File…", command=lambda: self.delete_file(path),
                         state="normal" if allowed else "disabled")
        menu.add_separator()
        project_root = self.selected_root
        can_project_patch = bool(project_root and project_root.is_dir())
        if can_project_patch:
            try:
                validate_target(project_root)
            except PatchError:
                can_project_patch = False
        menu.add_command(label="Project Patcher…", command=lambda: self.open_project_patcher(project_root),
                         state="normal" if can_project_patch else "disabled")
        menu.add_separator()
        menu.add_command(label="Open Folder in Terminal",
                         command=lambda: self._open_folder_in_terminal(folder))
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def open_tokenizing_patcher(self, path):
        try:
            return PatcherWindow(self, path)
        except (OSError, PatchError) as exc:
            self.report_error("Cannot open patcher", exc)
            return None

    def open_text_toucher(self, folder):
        try:
            return TextToucherWindow(self, folder)
        except (OSError, PatchError) as exc:
            self.report_error("Cannot create file", exc)
            return None

    def open_schema_toucher(self, folder, path=None):
        try:
            return SchemaToucherWindow(self, folder=folder, path=path)
        except (OSError, PatchError) as exc:
            self.report_error("Cannot open schema toucher", exc)
            return None

    def open_text_editor(self, path):
        try:
            return TextEditorWindow(self, path)
        except (OSError, PatchError) as exc:
            self.report_error("Cannot open text editor", exc)
            return None

    def open_backups(self):
        """One Backups window at a time; raise the existing one."""
        existing = getattr(self, "backups_window", None)
        if existing is not None and existing.top.winfo_exists():
            existing.top.deiconify()
            existing.top.lift()
            existing.refresh()
            return existing
        self.backups_window = BackupsWindow(self)
        return self.backups_window

    def open_history(self):
        """One History window at a time; raise the existing one."""
        existing = getattr(self, "history_window", None)
        if existing is not None and existing.top.winfo_exists():
            existing.top.deiconify()
            existing.top.lift()
            existing.refresh()
            return existing
        self.history_window = HistoryWindow(self)
        return self.history_window

    def open_settings(self):
        """Open or raise the per-user settings window."""
        existing = getattr(self, "settings_window", None)
        if existing is not None and existing.top.winfo_exists():
            existing.top.deiconify()
            existing.top.lift()
            return existing
        self.settings_window = SettingsWindow(self)
        return self.settings_window

    def _open_folder_in_terminal(self, folder: Path):
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    ["powershell.exe", "-NoExit", "-Command",
                     f'Set-Location -LiteralPath "{folder}"'],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-a", "Terminal", str(folder)])
            else:
                subprocess.Popen(["x-terminal-emulator", "--working-directory", str(folder)])
        except OSError as exc:
            self.report_error("Cannot open terminal", exc)

    def open_project_patcher(self, folder):
        try:
            return ProjectPatcherWindow(self, folder)
        except (OSError, PatchError) as exc:
            self.report_error("Cannot open project patcher", exc)
            return None

    def delete_file(self, path):
        if self.running_tasks or self.scan_pending:
            self.log_message("Wait for the current scan or compile to finish before deleting a file.", "WARNING")
            return

        def do_delete():
            try:
                perform(self, "file.delete", {"path": str(path)},
                        approval_guard=lambda: not self.scan_pending,
                        approval_fn=self._show_approval_and_wait)
                self.gui_queue.put(lambda: self.file_transformed(path, action="Deleted"))
            except PatchError as exc:
                msg = str(exc)
                self.gui_queue.put(lambda: self.report_error("Could not delete file", msg))

        self.run_threaded_action(do_delete, "file.delete")

    def file_transformed(self, path, action="Saved"):
        self.log_message(f"{action} file: {path}. Compile a new snapshot before exporting.")
        self.request_rescan_tree_silent()

    def request_rescan_tree(self):
        self._queue_tree_scan(use_popup=True)

    def request_rescan_tree_silent(self):
        self._queue_tree_scan(use_popup=False)

    def _queue_tree_scan(self, use_popup=False):
        self.controller.reserve_scan()
        self.scan_pending = True
        self.scan_use_popup = self.scan_use_popup or use_popup
        self.refresh_exclusions_popup()
        if self.scan_after_id is not None:
            self.root.after_cancel(self.scan_after_id)
        self.scan_after_id = self.root.after(150, self._start_pending_scan)

    def _start_pending_scan(self):
        self.scan_after_id = None
        if self.running_tasks:
            self.scan_after_id = self.root.after(150, self._start_pending_scan)
            return
        root = self.selected_root
        revision = self.scan_revision
        policy = copy.deepcopy(self.exclusion_policy)
        use_popup = self.scan_use_popup
        self.scan_use_popup = False
        self.run_threaded_action(lambda: self._scan_tree_impl(root, policy, revision), "scan_tree", use_popup=use_popup)

    def _scan_tree_impl(self, root, policy, revision):
        root = root if root and root.is_dir() else None
        if root is None:
            self.schedule_log_message("Cannot scan: no valid project root.", "ERROR")
            self.gui_queue.put(lambda: self._finish_tree_scan(revision))
            return
        self.schedule_log_message(f"Scanning project tree: {root}")
        try:
            result = self.action("project.scan", {"revision": revision})
            rows = [dict(r, path=Path(r["path"]), parent=Path(r["parent"]) if r["parent"] else None) for r in result["rows"]]
            skipped = result["skipped"]
        except Exception:
            self.gui_queue.put(lambda: self._finish_tree_scan(revision))
            raise
        if self.stop_event.is_set():
            self.schedule_log_message("Tree scan cancelled.", "WARNING")
            self.gui_queue.put(lambda: self._finish_tree_scan(revision))
            return
        self.gui_queue.put(lambda: self._apply_tree_scan(root, revision, rows, skipped))

    def _finish_tree_scan(self, revision):
        if revision == self.scan_revision:
            self.scan_pending = False

    def _apply_tree_scan(self, root, revision, rows, skipped):
        # Rapid edits may finish while an older scan is still running.
        if revision != self.scan_revision or root != self.selected_root:
            return
        self.populate_tree(rows)
        self.scan_pending = False
        self.log_message(f"Scan complete: {len(rows)} visible entries, {len(skipped)} skipped entries.")

    def populate_tree(self, rows: list[dict]):
        tree = self.widgets["folder_tree"]
        if self.tree_projection is None:
            self.tree_projection = TreeProjection(tree, self._insert_tree_row)
        self.tree_projection.replace(rows)

    def _insert_tree_row(self, row):
        tree = self.widgets["folder_tree"]
        iid = str(row["path"])
        parent = "" if row["parent"] is None else str(row["parent"])
        state = self.folder_item_states.get(iid, S_UNCHECKED)
        prefix = "📁" if row["entry_type"] == "dir" else "📄"
        size_text = "" if row["size_bytes"] is None else format_display_size(row["size_bytes"])
        is_dir = row["entry_type"] == "dir"
        tree.insert(parent, "end", iid=iid, text=f"  {prefix} {row['name']}",
                    image=self.icon_imgs.get(state, self.icon_imgs[S_UNCHECKED]),
                    values=("↑" if is_dir else "", "↓" if is_dir else "", size_text),
                    open=False)

    def on_tree_open(self, event):
        if self.tree_projection is not None:
            iid = event.widget.focus()
            if iid:
                self.tree_projection.expand(iid)

    def refresh_tree_visuals(self, start_iid: str | None = None):
        tree = self.widgets["folder_tree"]
        pending = [start_iid] if start_iid else list(tree.get_children())
        while pending:
            iid = pending.pop()
            if iid.startswith("__lazy__:") or not tree.exists(iid):
                continue
            state = self.folder_item_states.get(iid, S_UNCHECKED)
            tree.item(iid, image=self.icon_imgs.get(state, self.icon_imgs[S_UNCHECKED]))
            pending.extend(tree.get_children(iid))

    def on_tree_item_click(self, event):
        tree = event.widget
        iid = tree.identify_row(event.y)
        if not iid:
            return

        column = tree.identify_column(event.x)
        element = tree.identify("element", event.x, event.y) or ""
        if "indicator" in element:
            return
        path = Path(iid)
        if iid.startswith("__lazy__:"):
            return

        if column == "#1" and path.is_dir():
            self.navigate_tree_to_path(path.parent)
            return

        if column == "#2" and path.is_dir():
            self.navigate_tree_to_path(path)
            return

        if "image" in element:
            self.toggle_tree_item(iid)

    def toggle_tree_item(self, iid: str):
        current = self.folder_item_states.get(iid, S_UNCHECKED)
        new_state = S_CHECKED if current != S_CHECKED else S_UNCHECKED
        self._set_tree_state_recursive(iid, new_state)
        self.refresh_tree_visuals(iid)

    def _set_tree_state_recursive(self, iid: str, state: str):
        self.action("selection.set", {"path": iid, "state": state})

    def set_global_selection(self, state: str):
        tree = self.widgets.get("folder_tree")
        if tree is None:
            return
        self.action("selection.set", {"state": state})
        self.refresh_tree_visuals()
        self.log_message(f"Set visible tree selection to: {state}")

    def is_selected(self, path: Path) -> bool:
        try:
            key = str(path.resolve())
        except Exception:
            return False
        with self.state_lock:
            return self.folder_item_states.get(key, S_UNCHECKED) == S_CHECKED

    def _setup_content_panel(self, parent):
        # Row 1: filename label + lock toggle (lock shown only when text file is open)
        _header_row = tk.Frame(parent, bg=THEME["panel_bg"])
        _header_row.pack(fill=tk.X, pady=(4, 0))
        self._content_header_row = _header_row
        self.widgets["content_header"] = tk.Label(
            _header_row, text="Select a file or folder to preview",
            bg=THEME["panel_bg"], fg=THEME["muted_text"],
            anchor="w", padx=10,
        )
        self.widgets["content_header"].pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._editor_modified_label = tk.Label(
            _header_row, text="", bg=THEME["panel_bg"], fg=THEME["accent"],
            font=("Arial", 9, "bold"), padx=8,
        )
        self._editor_modified_label.pack(side=tk.RIGHT)
        # Lock/Edit toggle lives here in the header row, not in the menu bar.
        # Packed after modified so it sits just left of it: [filename …] [🔒] [●]
        self._editor_lock_btn = tk.Button(
            _header_row, text="🔒 Locked", command=self._editor_toggle_lock,
            bg=THEME["secondary"], fg=THEME["text"],
            activebackground=THEME["secondary_hover"], activeforeground=THEME["text"],
            relief=tk.FLAT, padx=10, pady=2, font=("Arial", 9, "bold"),
        )
        attach_tooltip(self._editor_lock_btn, "editor.read_only", THEME)
        self._editor_lock_btn.pack_forget()  # hidden until a text file is opened

        # Row 2: menu bar (File | Edit … lock toggle) — hidden until text file selected
        _menubar = tk.Frame(parent, bg=THEME["heading_bg"])
        _menubar.pack(fill=tk.X)
        self._editor_toolbar = _menubar

        _menu_kw = dict(
            bg=THEME["panel_bg"], fg=THEME["text"],
            activebackground=THEME["secondary"], activeforeground=THEME["text"],
            relief=tk.FLAT, bd=1, tearoff=0,
        )
        _mb_kw = dict(
            bg=THEME["heading_bg"], fg=THEME["heading_text"],
            activebackground=THEME["panel_alt_bg"], activeforeground=THEME["text"],
            relief=tk.FLAT, padx=12, pady=4, font=("Arial", 9),
            indicatoron=False, direction="below",
        )

        # File menu
        _file_mb = tk.Menubutton(_menubar, text="File", **_mb_kw)
        _file_mb.pack(side=tk.LEFT)
        _file_menu = tk.Menu(_file_mb, **_menu_kw)
        _file_menu.add_command(label="New",       command=self._editor_new)
        _file_menu.add_command(label="Save",      command=self._editor_save,   accelerator="Ctrl+S")
        _file_menu.add_command(label="Save As…",  command=self._editor_save_as)
        _file_mb.config(menu=_file_menu)
        self._editor_file_menu = _file_menu

        # Edit menu
        _edit_mb = tk.Menubutton(_menubar, text="Edit", **_mb_kw)
        _edit_mb.pack(side=tk.LEFT)
        _edit_menu = tk.Menu(_edit_mb, **_menu_kw)
        _edit_menu.add_command(label="Find / Replace…", command=self._editor_find_replace, accelerator="Ctrl+F")
        _edit_menu.add_separator()
        _edit_menu.add_command(label="Tokenizing Patcher…", command=self._editor_open_patcher)
        _edit_mb.config(menu=_edit_menu)

        _menubar.pack_forget()
        self._editor_locked = True

        # Text / editor view — 6 px margin on all sides so text breathes
        self._content_text_frame = tk.Frame(parent, bg=THEME["panel_bg"])
        _body = tk.Frame(self._content_text_frame, bg=THEME["panel_bg"])
        _body.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        _body.rowconfigure(0, weight=1)
        _body.columnconfigure(0, weight=1)
        self._editor_widget = tk.Text(
            _body, wrap=tk.NONE, undo=True,
            font=("Consolas", 10),
            bg=THEME["log_bg"], fg=THEME["text"],
            insertbackground=THEME["text"],
            selectbackground=THEME["selection"],
            selectforeground=THEME["text"],
            relief=tk.FLAT, padx=10, pady=8,
            state=tk.DISABLED,
        )
        self._editor_widget.grid(row=0, column=0, sticky="nsew")
        _ed_ybar = tk.Scrollbar(_body, command=self._editor_widget.yview,
                                bg=THEME["panel_alt_bg"], troughcolor=THEME["log_bg"],
                                activebackground=THEME["secondary"])
        _ed_ybar.grid(row=0, column=1, sticky="ns")
        _ed_xbar = tk.Scrollbar(_body, orient=tk.HORIZONTAL,
                                command=self._editor_widget.xview,
                                bg=THEME["panel_alt_bg"], troughcolor=THEME["log_bg"],
                                activebackground=THEME["secondary"])
        _ed_xbar.grid(row=1, column=0, sticky="ew")
        self._editor_widget.configure(yscrollcommand=_ed_ybar.set,
                                      xscrollcommand=_ed_xbar.set)
        self._editor_widget.bind("<<Modified>>", self._editor_changed)
        self._editor_widget.bind("<Control-s>", lambda _e: self._editor_save())
        self._editor_widget.bind("<Control-f>", lambda _e: self._editor_find_replace())
        self._editor_widget.bind("<ButtonPress-1>", self._editor_click_locked)
        self._editor_widget.bind("<FocusIn>", self._editor_check_stale, add=True)
        self._editor_path = None
        self._editor_sha256 = None
        self._editor_dirty = False
        self._editor_stale = False

        self._content_grid_frame = tk.Frame(parent, bg=THEME["tree_bg"])
        self._grid_breadcrumb_frame = tk.Frame(
            self._content_grid_frame, bg=THEME["heading_bg"]
        )
        self._grid_breadcrumb_frame.pack(side=tk.TOP, fill=tk.X)
        self._content_grid_canvas = tk.Canvas(
            self._content_grid_frame, bg=THEME["tree_bg"], highlightthickness=0
        )
        _grid_vsb = ttk.Scrollbar(
            self._content_grid_frame, orient=tk.VERTICAL,
            command=self._content_grid_canvas.yview
        )
        self._content_grid_canvas.configure(yscrollcommand=_grid_vsb.set)
        _grid_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._content_grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._content_grid_inner = tk.Frame(self._content_grid_canvas, bg=THEME["tree_bg"])
        self._content_grid_window = self._content_grid_canvas.create_window(
            (0, 0), window=self._content_grid_inner, anchor="nw"
        )
        self._content_grid_inner.bind(
            "<Configure>",
            lambda e: self._content_grid_canvas.configure(
                scrollregion=self._content_grid_canvas.bbox("all")
            )
        )
        self._content_grid_canvas.bind("<Configure>", self._on_content_grid_resize)
        self._content_grid_canvas.bind(
            "<MouseWheel>",
            lambda e: self._content_grid_canvas.yview_scroll(
                -1 if e.delta > 0 else 1, "units"
            )
        )
        # Right-click on empty canvas space → folder-level context menu.
        self._content_grid_canvas.bind(
            "<ButtonRelease-3>",
            lambda e: self._show_path_context_menu(
                getattr(self, "_content_grid_folder", self.selected_root),
                e.x_root, e.y_root,
            )
        )
        self._content_grid_entries = []
        self._content_grid_folder: "Path | None" = None
        self._grid_cells: list = []
        self._grid_focus_idx: int = -1
        self._grid_cols: int = 1
        self._show_content_widget("readonly")
        self.root.bind("<Control-s>", lambda _e: self._editor_save() if self._editor_path else None)
        self.root.bind("<Control-f>", lambda _e: self._editor_find_replace() if self._editor_path else None)

    def _show_content_widget(self, mode: str):
        """Switch the content panel. *mode* is 'editor', 'readonly', or 'grid'."""
        if getattr(self, "_content_widget_showing", None) == mode:
            return
        self._content_widget_showing = mode
        if mode == "grid":
            self._editor_toolbar.pack_forget()
            self._editor_lock_btn.pack_forget()
            self._content_text_frame.pack_forget()
            self._content_grid_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self._content_grid_frame.pack_forget()
            if mode == "editor":
                self._content_text_frame.pack(fill=tk.BOTH, expand=True)
                self._editor_toolbar.pack(fill=tk.X, before=self._content_text_frame)
                self._editor_lock_btn.pack(side=tk.RIGHT, padx=(0, 4))
            else:
                self._editor_toolbar.pack_forget()
                self._editor_lock_btn.pack_forget()
                self._content_text_frame.pack(fill=tk.BOTH, expand=True)

    def on_tree_selection_change(self, event):
        if getattr(self, "_grid_syncing_tree", False):
            return
        tree = event.widget
        selected = tree.selection()
        iid = selected[0] if selected else ""
        if iid and not iid.startswith(TreeProjection.PLACEHOLDER):
            self.update_content_panel(iid)
        else:
            self._show_content_placeholder()

    def update_content_panel(self, iid):
        path = Path(iid)
        if path.is_dir():
            self._show_folder_grid(path)
        elif path.is_file():
            if self._is_text_file(path):
                self._show_text_content(path)
            else:
                self._show_file_metadata(path)
        else:
            self._show_content_placeholder(f"Not found: {Path(iid).name}")

    def _is_text_file(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                return b"\x00" not in f.read(8192)
        except OSError:
            return False

    def _show_text_content(self, path: Path):
        self.widgets["content_header"].config(text=str(path))
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            sha = hashlib.sha256(raw).hexdigest()
        except OSError as exc:
            text = f"Cannot read file: {exc}"
            sha = None
        self._editor_path = path
        self._editor_sha256 = sha
        self._editor_dirty = False
        self._editor_stale = False
        self._editor_modified_label.config(text="")
        ew = self._editor_widget
        ew.config(state=tk.NORMAL)
        ew.delete("1.0", tk.END)
        ew.insert("1.0", text)
        ew.edit_modified(False)
        self._editor_locked = True
        ew.config(state=tk.DISABLED)
        self._editor_lock_btn.config(text="🔒 Locked", bg=THEME["secondary"])
        self._editor_file_menu.entryconfig("Save", state=tk.DISABLED)
        self._show_content_widget("editor")

    def _show_file_metadata(self, path: Path):
        self.widgets["content_header"].config(text=str(path))
        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            text = (
                f"Name:      {path.name}\n"
                f"Type:      {path.suffix or '(no extension)'}\n"
                f"Size:      {format_display_size(stat.st_size)}\n"
                f"Modified:  {mtime}\n\n"
                f"Binary file — content not shown."
            )
        except OSError as exc:
            text = f"Cannot read metadata: {exc}"
        self._editor_path = None
        self._editor_sha256 = None
        self._editor_dirty = False
        self._editor_modified_label.config(text="")
        ew = self._editor_widget
        ew.config(state=tk.NORMAL)
        ew.delete("1.0", tk.END)
        ew.insert("1.0", text)
        ew.edit_modified(False)
        ew.config(state=tk.DISABLED)
        self._show_content_widget("readonly")

    def _show_folder_grid(self, path: Path):
        if getattr(self, "_content_grid_folder", None) == path and \
           getattr(self, "_content_widget_showing", None) == "grid":
            return
        self.widgets["content_header"].config(text=str(path))
        self._content_grid_folder = path
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError):
            entries = []
        self._content_grid_entries = entries
        self._update_grid_breadcrumb(path)
        self._layout_folder_grid()
        self._show_content_widget("grid")
        self._content_grid_canvas.focus_set()
        self._sync_tree_to_path(path)

    def _layout_folder_grid(self):
        if getattr(self, "_laying_out_grid", False):
            return
        self._laying_out_grid = True
        try:
            self._layout_folder_grid_inner()
        finally:
            self._laying_out_grid = False

    def _layout_folder_grid_inner(self):
        frame = self._content_grid_inner
        for child in frame.winfo_children():
            child.destroy()
        self._grid_cells = []
        self._grid_focus_idx = -1
        entries = getattr(self, "_content_grid_entries", [])
        if not entries:
            tk.Label(frame, text="(empty folder)", bg=THEME["tree_bg"],
                     fg=THEME["muted_text"], pady=20).grid(row=0, column=0)
            return
        canvas_w = self._content_grid_canvas.winfo_width() or 400
        item_w = 88
        cols = max(1, canvas_w // item_w)
        self._grid_cols = cols
        rows = max(1, (len(entries) + cols - 1) // cols)
        for c in range(cols):
            frame.grid_columnconfigure(c, minsize=item_w + 4, weight=0)
        for r in range(rows):
            frame.grid_rowconfigure(r, minsize=72 + 4, weight=0)
        hover_bg = THEME.get("tree_selected_bg", THEME["secondary"])
        focus_bg = THEME["selection"]
        normal_bg = THEME["tree_bg"]
        for idx, entry in enumerate(entries):
            r, c = divmod(idx, cols)
            icon = "📁" if entry.is_dir() else "📄"
            cell = tk.Frame(frame, bg=normal_bg, width=item_w, height=72,
                            highlightthickness=0, borderwidth=0)
            cell.grid_propagate(False)
            cell.grid(row=r, column=c, padx=2, pady=2, sticky="nsew")
            lbl_icon = tk.Label(cell, text=icon, font=("Arial", 22), bg=normal_bg,
                                fg=THEME["text"], width=3, anchor="center")
            lbl_icon.pack(fill="x", pady=(4, 0))
            short = entry.name if len(entry.name) <= 13 else entry.name[:11] + "…"
            lbl_name = tk.Label(cell, text=short, bg=normal_bg, fg=THEME["text"],
                                font=("Arial", 8), wraplength=item_w - 4,
                                justify=tk.CENTER)
            lbl_name.pack(fill="x")
            self._grid_cells.append((cell, lbl_icon, lbl_name))
            # Hover highlight respects focus state.
            def _enter(ev, _idx=idx, _cell=cell, _lbl_i=lbl_icon, _lbl_n=lbl_name):
                if self._grid_focus_idx != _idx:
                    for w in (_cell, _lbl_i, _lbl_n):
                        w.config(bg=hover_bg)
            def _leave(ev, _idx=idx, _cell=cell, _lbl_i=lbl_icon, _lbl_n=lbl_name):
                if self._grid_focus_idx != _idx:
                    for w in (_cell, _lbl_i, _lbl_n):
                        w.config(bg=normal_bg)
            def _cell_click(ev, _idx=idx):
                self._content_grid_canvas.focus_set()
                self._grid_set_focus(_idx)
                return "break"
            def _cell_rc(ev, _p=entry):
                self._show_path_context_menu(_p, ev.x_root, ev.y_root)
                return "break"
            def _cell_dbl(ev, _p=entry):
                if _p.is_dir():
                    self._show_folder_grid(_p)
                else:
                    self.update_content_panel(str(_p))
                return "break"
            for w in (cell, lbl_icon, lbl_name):
                w.bind("<Enter>", _enter)
                w.bind("<Leave>", _leave)
                w.bind("<ButtonRelease-1>", _cell_click)
                w.bind("<ButtonRelease-3>", _cell_rc)
                w.bind("<Double-Button-1>", _cell_dbl)
        # Key bindings on the canvas (receives focus when grid is shown).
        canvas = self._content_grid_canvas
        canvas.bind("<Left>",      lambda e: self._grid_key_nav(-1))
        canvas.bind("<Right>",     lambda e: self._grid_key_nav(1))
        canvas.bind("<Up>",        lambda e: self._grid_key_nav(-self._grid_cols))
        canvas.bind("<Down>",      lambda e: self._grid_key_nav(self._grid_cols))
        canvas.bind("<Return>",    lambda e: self._grid_key_enter())
        canvas.bind("<BackSpace>", lambda e: self._grid_key_backspace())

    def _grid_set_focus(self, idx: int):
        normal_bg = THEME["tree_bg"]
        focus_bg = THEME["selection"]
        # Clear previous focus highlight.
        prev = self._grid_focus_idx
        if 0 <= prev < len(self._grid_cells):
            cell, li, ln = self._grid_cells[prev]
            for w in (cell, li, ln):
                w.config(bg=normal_bg)
        self._grid_focus_idx = idx
        if 0 <= idx < len(self._grid_cells):
            cell, li, ln = self._grid_cells[idx]
            for w in (cell, li, ln):
                w.config(bg=focus_bg)
            # Scroll the canvas so the focused cell is visible.
            cols = self._grid_cols
            row = idx // cols
            item_h = 72 + 4
            total_rows = (len(self._grid_cells) + cols - 1) // cols
            canvas = self._content_grid_canvas
            canvas_h = canvas.winfo_height() or 1
            total_h = total_rows * item_h
            if total_h > canvas_h:
                frac_top = (row * item_h) / total_h
                frac_bot = ((row + 1) * item_h) / total_h
                canvas.yview_moveto(max(0.0, frac_top - canvas_h / total_h / 2))

    def _grid_key_nav(self, delta: int):
        n = len(self._grid_cells)
        if n == 0:
            return
        current = self._grid_focus_idx
        if current < 0:
            new_idx = 0
        else:
            new_idx = max(0, min(n - 1, current + delta))
        self._grid_set_focus(new_idx)

    def _grid_key_enter(self):
        idx = self._grid_focus_idx
        if idx < 0 or idx >= len(self._content_grid_entries):
            return
        entry = self._content_grid_entries[idx]
        if entry.is_dir():
            self._show_folder_grid(entry)
        else:
            self.update_content_panel(str(entry))

    def _grid_key_backspace(self):
        current = getattr(self, "_content_grid_folder", None)
        if current is None:
            return
        root = self.selected_root
        parent = current.parent
        if parent == current:
            return
        if root is not None and current == root:
            return
        self._show_folder_grid(parent)

    def _on_content_grid_resize(self, event):
        self._content_grid_canvas.itemconfigure(self._content_grid_window, width=event.width)
        if getattr(self, "_content_grid_entries", None):
            if self._grid_resize_timer is not None:
                self.root.after_cancel(self._grid_resize_timer)
            self._grid_resize_timer = self.root.after(80, self._layout_folder_grid)

    def _update_grid_breadcrumb(self, path: Path):
        frame = self._grid_breadcrumb_frame
        for w in frame.winfo_children():
            w.destroy()
        root = self.selected_root
        parts: list[tuple[str, Path]] = []
        try:
            if root is not None:
                rel = path.relative_to(root)
                p = root
                parts.append((root.name, root))
                for part in rel.parts:
                    p = p / part
                    parts.append((part, p))
            else:
                for i in range(len(path.parts)):
                    parts.append((path.parts[i], Path(*path.parts[: i + 1])))
        except (ValueError, TypeError):
            parts = [(path.name, path)]

        for i, (label, part_path) in enumerate(parts):
            is_last = (i == len(parts) - 1)
            if is_last:
                tk.Label(frame, text=label, bg=THEME["heading_bg"],
                         fg=THEME["text"], font=("Arial", 9, "bold"),
                         padx=6, pady=3).pack(side=tk.LEFT)
            else:
                btn = tk.Button(
                    frame, text=label, bg=THEME["heading_bg"],
                    fg=THEME["muted_text"], font=("Arial", 9),
                    relief=tk.FLAT, cursor="hand2", padx=4, pady=2, bd=0,
                    activebackground=THEME["heading_bg"],
                    activeforeground=THEME["text"],
                    command=lambda _p=part_path: self._show_folder_grid(_p),
                )
                attach_tooltip(btn, "main.breadcrumb", THEME)
                btn.pack(side=tk.LEFT)
                tk.Label(frame, text="/", bg=THEME["heading_bg"],
                         fg=THEME["muted_text"], font=("Arial", 9),
                         padx=2, pady=3).pack(side=tk.LEFT)

        parent_path = path.parent
        can_go_up = parent_path != path and (root is None or path != root)
        if can_go_up:
            up_btn = tk.Button(
                frame, text="↑ Up", bg=THEME["heading_bg"],
                fg=THEME["secondary"], font=("Arial", 9),
                relief=tk.FLAT, cursor="hand2", padx=8, pady=2, bd=0,
                activebackground=THEME["heading_bg"],
                activeforeground=THEME["secondary_hover"],
                command=lambda _p=parent_path: self._show_folder_grid(_p),
            )
            attach_tooltip(up_btn, "main.breadcrumb", THEME)
            up_btn.pack(side=tk.RIGHT, padx=(0, 4))

        entries = getattr(self, "_content_grid_entries", [])
        n_dirs  = sum(1 for e in entries if e.is_dir())
        n_files = len(entries) - n_dirs
        if n_dirs and n_files:
            count_text = f"{n_dirs} folder{'s' if n_dirs != 1 else ''}  ·  {n_files} file{'s' if n_files != 1 else ''}"
        elif n_dirs:
            count_text = f"{n_dirs} folder{'s' if n_dirs != 1 else ''}"
        elif n_files:
            count_text = f"{n_files} file{'s' if n_files != 1 else ''}"
        else:
            count_text = "empty"
        tk.Label(frame, text=count_text, bg=THEME["heading_bg"],
                 fg=THEME["muted_text"], font=("Arial", 9),
                 padx=10, pady=3).pack(side=tk.RIGHT)

    def _sync_tree_to_path(self, path: Path):
        tree = self.widgets["folder_tree"]
        iid = str(path)
        try:
            tree.item(iid)
        except tk.TclError:
            return
        self._grid_syncing_tree = True
        try:
            tree.selection_set(iid)
            tree.focus(iid)
        finally:
            self._grid_syncing_tree = False
        tree.after_idle(lambda: tree.see(iid))

    def _show_content_placeholder(self, text="Select a file or folder to preview"):
        self.widgets["content_header"].config(text=text)
        self._editor_path = None
        self._editor_sha256 = None
        self._editor_dirty = False
        self._editor_modified_label.config(text="")
        ew = self._editor_widget
        ew.config(state=tk.NORMAL)
        ew.delete("1.0", tk.END)
        ew.edit_modified(False)
        ew.config(state=tk.DISABLED)
        self._show_content_widget("readonly")

    def _editor_changed(self, _event=None):
        if self._editor_widget.edit_modified():
            self._editor_widget.edit_modified(False)
            self._editor_dirty = True
            self._editor_modified_label.config(text="● modified")

    def _editor_save(self):
        if not self._editor_path or not self._editor_sha256:
            return
        ew = self._editor_widget
        text = ew.get("1.0", "end-1c")
        path_snap = self._editor_path
        sha_snap = self._editor_sha256

        def do_save():
            result = self.action("text.save", {
                "path": str(path_snap),
                "text": text,
                "sha256": sha_snap,
                "backup": False,
            })
            saved_path = Path(result["path"])
            raw = saved_path.read_bytes()
            new_sha = hashlib.sha256(raw).hexdigest()
            def apply():
                self._editor_sha256 = new_sha
                self._editor_path = saved_path
                self._editor_dirty = False
                ew.edit_modified(False)
                self._editor_modified_label.config(text="")
                self.widgets["content_header"].config(text=str(saved_path))
                self.file_transformed(saved_path)
            self.gui_queue.put(apply)

        self.run_threaded_action(do_save, "editor.save")

    def _editor_click_locked(self, _event=None):
        if self._editor_locked and self._editor_path:
            self._editor_lock_hint()

    def _editor_lock_hint(self):
        if getattr(self, "_lock_hint_id", None):
            return
        lbl = tk.Label(
            self._editor_widget, text=" 🔒 Click the lock button to edit ",
            bg=THEME["accent"], fg="#FFFFFF", font=("Arial", 9, "bold"),
            padx=8, pady=4, relief=tk.SOLID, bd=1,
        )
        lbl.place(relx=0.5, rely=0.0, anchor="n", y=8)
        self._lock_hint_id = self.root.after(
            2000, lambda: (lbl.destroy(), setattr(self, "_lock_hint_id", None)))

    def _editor_check_stale(self, _event=None):
        if not self._editor_path or not self._editor_sha256:
            return
        now = time.monotonic()
        if now - self._editor_stale_check_time < 2.0:
            return
        self._editor_stale_check_time = now
        path = self._editor_path
        expected_sha = self._editor_sha256
        def _worker():
            try:
                raw = path.read_bytes()
                disk_sha = hashlib.sha256(raw).hexdigest()
                stale = disk_sha != expected_sha
            except OSError:
                stale = True
            self.gui_queue.put(lambda: self._editor_apply_stale(stale))
        threading.Thread(target=_worker, daemon=True).start()

    def _editor_apply_stale(self, stale):
        self._editor_stale = stale
        if stale:
            self._editor_modified_label.config(text="⚠ changed on disk")

    def _editor_toggle_lock(self):
        if self._editor_stale and not self._editor_locked:
            from tkinter import messagebox
            if not messagebox.askyesno(
                "File changed on disk",
                "This file was modified outside the editor.\n"
                "Continue editing anyway?",
                parent=self.root, default="no",
            ):
                return
        self._editor_locked = not self._editor_locked
        state = tk.DISABLED if self._editor_locked else tk.NORMAL
        self._editor_widget.config(state=state)
        self._editor_file_menu.entryconfig("Save", state=state)
        if self._editor_locked:
            self._editor_lock_btn.config(text="🔒 Locked", bg=THEME["secondary"])
        else:
            self._editor_lock_btn.config(text="✏️ Editing", bg=THEME["accent"])
            self._editor_stale = False
            self._editor_widget.focus_set()

    def _editor_new(self):
        if self._editor_dirty:
            from tkinter import messagebox
            if not messagebox.askyesno("Discard changes?",
                                       "Discard unsaved changes and start a new document?",
                                       parent=self.root, default="no"):
                return
        self._editor_path = None
        self._editor_sha256 = None
        self._editor_dirty = False
        self._editor_modified_label.config(text="")
        self.widgets["content_header"].config(text="New document (unsaved)")
        ew = self._editor_widget
        ew.config(state=tk.NORMAL)
        ew.delete("1.0", tk.END)
        ew.edit_modified(False)
        self._editor_locked = False
        self._editor_lock_btn.config(text="✏️ Editing", bg=THEME["accent"])
        self._editor_file_menu.entryconfig("Save", state=tk.DISABLED)
        ew.focus_set()

    def _editor_save_as(self):
        from tkinter import filedialog
        init_dir = str(self._editor_path.parent) if self._editor_path else str(Path.cwd())
        init_file = self._editor_path.name if self._editor_path else "untitled.txt"
        dest = filedialog.asksaveasfilename(
            parent=self.root, initialdir=init_dir, initialfile=init_file,
            filetypes=(("All files", "*.*"),),
        )
        if not dest:
            return
        target = Path(dest)
        ew = self._editor_widget
        text = ew.get("1.0", "end-1c")

        def do_save_as():
            result = self.action("text.save_as", {"path": str(target), "text": text, "backup": False})
            saved_path = Path(result["path"])
            raw = saved_path.read_bytes()
            new_sha = hashlib.sha256(raw).hexdigest()
            def apply():
                self._editor_sha256 = new_sha
                self._editor_path = saved_path
                self._editor_dirty = False
                ew.edit_modified(False)
                self._editor_modified_label.config(text="")
                self.widgets["content_header"].config(text=str(saved_path))
                self._editor_file_menu.entryconfig("Save", state=tk.NORMAL)
                self.file_transformed(saved_path)
            self.gui_queue.put(apply)

        self.run_threaded_action(do_save_as, "editor.save_as")

    def _editor_find_replace(self):
        if getattr(self, "_editor_find_window", None):
            try:
                if self._editor_find_window.winfo_exists():
                    self._editor_find_window.lift()
                    return
            except tk.TclError:
                pass
        win = self._editor_find_window = tk.Toplevel(self.root)
        win.title("Find / Replace")
        win.configure(bg=THEME["panel_bg"])
        win.transient(self.root)
        find_var = tk.StringVar(win)
        replace_var = tk.StringVar(win)
        for row, label, var in ((0, "Find:", find_var), (1, "Replace:", replace_var)):
            tk.Label(win, text=label, bg=THEME["panel_bg"], fg=THEME["text"]).grid(
                row=row, column=0, padx=8, pady=6, sticky="w")
            tk.Entry(win, textvariable=var, width=38,
                     bg=THEME["field_bg"], fg=THEME["field_text"],
                     insertbackground=THEME["text"], relief=tk.FLAT).grid(
                row=row, column=1, padx=8, pady=6)
        btn_frame = tk.Frame(win, bg=THEME["panel_bg"])
        btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
        self._make_button(btn_frame, "Find Next",
                          lambda: self._editor_find_next(find_var.get()),
                          THEME["secondary"], THEME["secondary_hover"],
                          tooltip_id="editor.find_next").pack(side=tk.LEFT, padx=4)
        self._make_button(btn_frame, "Replace All",
                          lambda: self._editor_replace_all(find_var.get(), replace_var.get()),
                          THEME["accent"], THEME["accent_hover"],
                          tooltip_id="editor.replace_all").pack(side=tk.LEFT, padx=4)

    def _editor_find_next(self, query):
        if not query:
            return
        ew = self._editor_widget
        start = ew.index("insert")
        pos = ew.search(query, start, stopindex=tk.END)
        if not pos:
            pos = ew.search(query, "1.0", stopindex=tk.END)
        if pos:
            end = f"{pos}+{len(query)}c"
            ew.tag_remove("found", "1.0", tk.END)
            ew.tag_add("found", pos, end)
            ew.tag_configure("found", background=THEME["selection"])
            ew.mark_set("insert", end)
            ew.see(pos)

    def _editor_replace_all(self, query, replacement):
        if self._editor_locked or not query:
            return
        ew = self._editor_widget
        content = ew.get("1.0", "end-1c")
        count = content.count(query)
        if not count:
            return
        new_content = content.replace(query, replacement)
        ew.delete("1.0", tk.END)
        ew.insert("1.0", new_content)
        ew.edit_modified(True)

    def _editor_open_patcher(self):
        if self._editor_path:
            self.open_tokenizing_patcher(self._editor_path)


    def apply_exclusion_settings(self):
        var = self.widgets.get("respect_exclusions")
        self.action("exclusions.update", {"operation": "respect", "respect": bool(var.get()) if var else True})
        self.log_message(f"Respect exclusions: {self.exclusion_policy.respect_exclusions}")
        self.exclusions_changed()

    def add_exclusion_from_entry(self, entry=None):
        if entry is None:
            entry = self.widgets.get("exclusion_entry")
        if entry is None:
            return
        value = entry.get().strip()
        if not value:
            return
        self.action("exclusions.update", {"operation": "add", "pattern": value})
        entry.delete(0, tk.END)
        self.log_message(f"Added exclusion pattern: {value}")
        self.exclusions_changed()

    def exclusions_changed(self):
        self.exclusions_dirty = True
        self.mark_project_dirty("exclusions_changed")
        self.request_rescan_tree_silent()

    def refresh_exclusions_popup(self):
        if self.exclusions_popup and self.exclusions_popup.top.winfo_exists():
            self.exclusions_popup.refresh()

    def manage_exclusions_popup(self):
        if self.exclusions_popup and self.exclusions_popup.top.winfo_exists():
            self.exclusions_popup.top.deiconify()
            self.exclusions_popup.top.lift()
            self.exclusions_popup.top.focus_set()
            return
        self.action("exclusions.inspect")
        self.exclusions_popup = ExclusionsPopup(self)


    def navigate_tree_to_path(self, target_path: Path):
        try:
            target = target_path.resolve()
        except Exception:
            return
        if not target.is_dir():
            return
        self.widgets["selected_root_var"].set(str(target))
        self.action("project.set_root", {"path": str(target)})
        self.populate_tree([])
        self.exclusions_dirty = False
        self.latest_source_mtime = 0.0
        self.transformed_paths = self.project_state.transformed_paths
        self.latest_snapshot_path = None
        self.log_message(f"Project root set: {self.selected_root}")
        self.request_rescan_tree()

    def navigate_to_parent(self):
        root = self.selected_root if self.selected_root and self.selected_root.is_dir() else None
        if root is None:
            return
        parent = root.parent
        if parent != root and parent.is_dir():
            self.navigate_tree_to_path(parent)

    def choose_root_dialog(self):
        selected = filedialog.askdirectory()
        if selected:
            self.widgets["selected_root_var"].set(selected)
            self.choose_root_from_entry()

    def choose_root_from_entry(self):
        candidate = Path(self.widgets["selected_root_var"].get()).expanduser()
        if not candidate.is_dir():
            self.report_error("Invalid Project Root", f"Not a directory:\n{candidate}")
            return
        self.action("project.set_root", {"path": str(candidate.resolve())})
        self.populate_tree([])
        self.exclusions_dirty = False
        self.latest_source_mtime = 0.0
        self.transformed_paths = self.project_state.transformed_paths
        self.latest_snapshot_path = None
        self.log_message(f"Project root set: {self.selected_root}")
        self.request_rescan_tree()

    def get_output_dir(self) -> Path:
        return Path(self.action("output.location")["path"])

    # ── Tabbed toolbar ──────────────────────────────────────────────

    def _switch_toolbar_tab(self, name):
        if self._active_toolbar_tab == name:
            return
        for tab_name, frame in self._toolbar_tab_frames.items():
            if tab_name == name:
                frame.pack(fill=tk.X)
            else:
                frame.pack_forget()
        for tab_name, btn in self._toolbar_tab_buttons.items():
            if tab_name == name:
                btn.config(bg=THEME["panel_bg"], fg=THEME["text"], font=("Arial", 9, "bold"))
            else:
                btn.config(bg=THEME["heading_bg"], fg=THEME["muted_text"], font=("Arial", 9))
        self._active_toolbar_tab = name
        if name == "Git":
            self._git_refresh_status()

    def _setup_git_toolbar(self, parent):
        row1 = tk.Frame(parent, bg=THEME["panel_bg"])
        row1.pack(fill=tk.X, padx=5, pady=(6, 2))

        self._git_status_label = tk.Label(
            row1, text="", bg=THEME["panel_bg"], fg=THEME["muted_text"],
            font=("Consolas", 9), anchor="w",
        )
        self._git_status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._make_button(row1, "Refresh", self._git_refresh_status,
                          THEME["panel_alt_bg"], THEME["field_bg_alt"],
                          tooltip_id="git.refresh").pack(side=tk.RIGHT, padx=4)

        row2 = tk.Frame(parent, bg=THEME["panel_bg"])
        row2.pack(fill=tk.X, padx=5, pady=2)

        self._git_add_btn = self._make_button(row2, "Add All", self._git_add_all,
                          THEME["accent"], THEME["accent_hover"],
                          tooltip_id="git.add_all")
        self._git_add_btn.pack(side=tk.LEFT, padx=4)
        self._git_pull_btn = self._make_button(row2, "Pull", self._git_pull,
                          THEME["secondary"], THEME["secondary_hover"],
                          tooltip_id="git.pull")
        self._git_pull_btn.pack(side=tk.LEFT, padx=4)
        self._git_push_btn = self._make_button(row2, "Push", self._git_push,
                          THEME["success"], THEME["success_hover"],
                          tooltip_id="git.push")
        self._git_push_btn.pack(side=tk.LEFT, padx=4)

        row3 = tk.Frame(parent, bg=THEME["panel_bg"])
        row3.pack(fill=tk.X, padx=5, pady=(2, 4))

        tk.Label(row3, text="Message:", bg=THEME["panel_bg"],
                 fg=THEME["muted_text"], font=("Arial", 9)).pack(side=tk.LEFT, padx=(4, 0))
        self._git_commit_entry = tk.Entry(
            row3, bg=THEME["field_bg_alt"], fg=THEME["field_text"],
            insertbackground=THEME["field_text"], relief=tk.FLAT, font=("Consolas", 9),
        )
        attach_tooltip(self._git_commit_entry, "git.commit_message", THEME)
        self._git_commit_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self._git_commit_entry.bind("<Return>", lambda _e: self._git_commit())
        self._git_commit_btn = self._make_button(row3, "Commit", self._git_commit,
                          THEME["accent"], THEME["accent_hover"], bold=True,
                          tooltip_id="git.commit")
        self._git_commit_btn.pack(side=tk.RIGHT, padx=4)

    # ── Git operations ────────────────────────────────────────────

    def _git_set_busy(self, busy):
        self._git_busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in (self._git_add_btn, self._git_pull_btn,
                    self._git_push_btn, self._git_commit_btn):
            btn.config(state=state)
        if busy:
            self._git_status_label.config(text="Running…")

    def _git_run(self, args, callback):
        def _worker():
            try:
                result = subprocess.run(
                    ["git"] + args, capture_output=True, text=True,
                    cwd=self.project_state.root if self.project_state.root else None,
                    timeout=30,
                )
                self.gui_queue.put(lambda: callback(result))
            except Exception as exc:
                self.gui_queue.put(lambda: self.log_message(f"Git error: {exc}", "ERROR"))
        threading.Thread(target=_worker, daemon=True).start()

    def _git_refresh_status(self):
        root = self.project_state.root
        if not root:
            self._git_status_label.config(text="No project root set")
            return
        self._git_status_label.config(text="Checking…")
        def _on_result(r):
            if r.returncode != 0:
                self._git_status_label.config(text="Not a git repository")
                return
            lines = r.stdout.strip().splitlines()
            branch = ""
            for ln in lines:
                if ln.startswith("## "):
                    branch = ln[3:].split("...")[0]
                    break
            changed = sum(1 for ln in lines if not ln.startswith("## ") and ln.strip())
            parts = []
            if branch:
                parts.append(f"Branch: {branch}")
            parts.append(f"{changed} changed" if changed else "Clean")
            self._git_status_label.config(text="  |  ".join(parts))
        self._git_run(["status", "--short", "--branch"], _on_result)

    def _git_add_all(self):
        if self._git_busy:
            self.log_message("A git operation is already in progress.", "WARNING")
            return
        root = self.project_state.root
        if not root:
            self.log_message("Set a project root first.", "WARNING")
            return
        self._git_set_busy(True)
        self.log_message("git add .")
        def _on_result(r):
            self._git_set_busy(False)
            if r.returncode == 0:
                self.log_message("Staged all changes.", "SUCCESS")
            else:
                self.log_message(f"git add failed: {r.stderr.strip()}", "ERROR")
            self._git_refresh_status()
        self._git_run(["add", "."], _on_result)

    def _git_commit(self):
        if self._git_busy:
            self.log_message("A git operation is already in progress.", "WARNING")
            return
        msg = self._git_commit_entry.get().strip()
        if not msg:
            self.log_message("Enter a commit message.", "WARNING")
            return
        root = self.project_state.root
        if not root:
            self.log_message("Set a project root first.", "WARNING")
            return
        self._git_set_busy(True)
        self.log_message(f"git commit -m \"{msg}\"")
        def _on_result(r):
            self._git_set_busy(False)
            if r.returncode == 0:
                self._git_commit_entry.delete(0, tk.END)
                self.log_message(f"Committed: {msg}", "SUCCESS")
            else:
                self.log_message(f"git commit failed: {r.stderr.strip()}", "ERROR")
            self._git_refresh_status()
        self._git_run(["commit", "-m", msg], _on_result)

    def _git_pull(self):
        if self._git_busy:
            self.log_message("A git operation is already in progress.", "WARNING")
            return
        root = self.project_state.root
        if not root:
            self.log_message("Set a project root first.", "WARNING")
            return
        self._git_set_busy(True)
        self.log_message("git pull")
        def _on_result(r):
            self._git_set_busy(False)
            if r.returncode == 0:
                self.log_message(f"Pull complete: {r.stdout.strip()}", "SUCCESS")
            else:
                self.log_message(f"git pull failed: {r.stderr.strip()}", "ERROR")
            self._git_refresh_status()
        self._git_run(["pull"], _on_result)

    def _git_push(self):
        if self._git_busy:
            self.log_message("A git operation is already in progress.", "WARNING")
            return
        root = self.project_state.root
        if not root:
            self.log_message("Set a project root first.", "WARNING")
            return
        self._git_set_busy(True)
        self.log_message("git push")
        def _on_result(r):
            self._git_set_busy(False)
            if r.returncode == 0:
                self.log_message(f"Push complete: {r.stdout.strip()}", "SUCCESS")
            else:
                self.log_message(f"git push failed: {r.stderr.strip()}", "ERROR")
            self._git_refresh_status()
        self._git_run(["push"], _on_result)

    def compile_snapshot_placeholder(self):
        if self.scan_pending or "scan_tree" in self.running_tasks or self.applied_scan_revision != self.scan_revision:
            self.log_message("A current project tree is required. Finish a rescan before compiling.", "WARNING")
            return
        policy = copy.deepcopy(self.exclusion_policy)
        root = self.selected_root
        revision = self.scan_revision
        include_binary_blobs = bool(self.widgets["include_binary_blobs"].get())
        self.mark_project_dirty("compile_required")
        capture_revision = self.project_state.capture_revision
        self.run_threaded_action(
            lambda: self._compile_snapshot_impl(policy, root, revision, include_binary_blobs, capture_revision),
            "compile_snapshot", use_popup=True)

    def _compile_snapshot_impl(self, policy, root, revision, include_binary_blobs, capture_revision=None):
        result = self.action("snapshot.compile")
        self.schedule_log_message(f"Snapshot compiled: {result['path']}")
        self.gui_queue.put(lambda: setattr(self, "exclusions_dirty", False))

    def _accept_compiled_snapshot(self, snapshot_path, root, revision, capture_revision=None):
        if root == self.selected_root and revision == self.scan_revision:
            if not self.project_state.mark_snapshot(snapshot_path, revision, capture_revision):
                return
            self.latest_snapshot_path = snapshot_path
            self.exclusions_dirty = False
            self.transformed_paths = self.project_state.transformed_paths
            self.log_message(f"Latest snapshot set: {snapshot_path}")

    def _require_latest_snapshot(self):
        try:
            return Path(self.action("snapshot.require")["path"])
        except (OSError, PatchError) as exc:
            self.log_message(str(exc), "WARNING")
            return None

    def export_snapshot_output(self, output_name, suffix, content_override=None):
        include_tree = output_name == "project_filedump_markdown" and bool(self.widgets["include_tree_in_filedump"].get())
        def export():
            result = self.action("snapshot.export", {"output": output_name, "suffix": suffix, "include_tree": include_tree})
            self.schedule_log_message(f"Exported: {result['path']}")
        self.run_threaded_action(export, "export_snapshot", use_popup=True)

    def export_tree_markdown(self):
        self.export_snapshot_output("project_tree_markdown", TREE_MD_SUFFIX)

    def export_filedump_markdown(self):
        self.export_snapshot_output("project_filedump_markdown", FILEDUMP_MD_SUFFIX)

    def export_combined_markdown(self):
        self.export_snapshot_output("project_tree_and_filedump_markdown", COMBINED_MD_SUFFIX)

    def export_vendor_app(self):
        self.run_threaded_action(self._export_vendor_app_impl, "vendor_export", use_popup=True)

    def export_sidecar_app(self):
        self.run_threaded_action(self._export_sidecar_app_impl, "sidecar_export", use_popup=True)

    def _export_sidecar_app_impl(self):
        result = create_sidecar_export(log_callback=self.schedule_log_message)
        if result.get("zip_path"):
            self.schedule_log_message(f"Sidecar ZIP ready: {result['zip_path']}")
        else:
            self.schedule_log_message(f"Sidecar export ready: {result['export_name']} ({result['included_count']} files)")

    def _export_vendor_app_impl(self):
        result = self.action("vendor.export")
        self.schedule_log_message(
            f"Vendor export ready: {result['export_dir']} ({result['included_count']} files, {result['skipped_count']} skipped)"
        )
        if result.get("zip_path"):
            self.schedule_log_message(f"Vendor zip ready: {result['zip_path']}")

    def open_output_folder(self):
        out_dir = Path(self.action("output.location")["path"])
        try:
            if platform.system() == "Windows":
                os.startfile(out_dir)
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(out_dir)], check=False)
            else:
                subprocess.run(["xdg-open", str(out_dir)], check=False)
            self.log_message(f"Opened output folder: {out_dir}")
        except Exception as exc:
            self.log_message(f"Could not open output folder: {exc}", "ERROR")


    def _activity_blinker(self):
        if self.running_tasks:
            task_names = ", ".join(sorted(self.running_tasks))
            self.widgets["status_var"].set(f"[ACTIVE] {task_names}")
            current_color = self.widgets["status_bar"].cget("bg")
            next_color = THEME["panel_alt_bg"] if current_color == THEME["status_bg"] else THEME["status_bg"]
            self.widgets["status_bar"].config(bg=next_color)
        else:
            self.widgets["status_bar"].config(bg=THEME["status_bg"])
        self.root.after(500, self._activity_blinker)

    def cancel_current_operations(self):
        self.stop_event.set()
        for operation in tuple(self.action_operations):
            self.controller.dispatcher.cancel(operation)
        self.log_message("Stop signal sent to active task.", "WARNING")

    def run_threaded_action(self, target_function, task_id: str, use_popup=False):
        if task_id in self.running_tasks:
            self.log_message(f"Task already running: {task_id}", "WARNING")
            return

        if use_popup:
            self.current_progress_popup = ProgressPopup(self.root, title=f"Working: {task_id}", on_cancel=self.cancel_current_operations)

        self.running_tasks.add(task_id)
        self.project_state.operation_started(task_id)
        self.stop_event.clear()

        def runner():
            try:
                target_function()
            except PatchError as exc:
                # A routine action failure; the operation itself is already in History.
                self.schedule_log_message(f"{task_id}: {exc}", "ERROR")
            except Exception as exc:
                self.controller.history.record_problem("worker", f"{task_id} failed: {exc}", traceback.format_exc())
            finally:
                self.running_tasks.discard(task_id)
                self.project_state.operation_finished(task_id)
                if use_popup and self.current_progress_popup:
                    popup = self.current_progress_popup
                    self.current_progress_popup = None
                    self.gui_queue.put(popup.close)
                self.schedule_log_message(f"Task finished: {task_id}")

        threading.Thread(target=runner, daemon=True).start()

    def _show_approval_and_wait(self, summary, parent=None):
        """Post an approval dialog to the Tk thread and block the caller until answered.
        Safe to call from a worker thread — Tk thread stays free."""
        event = threading.Event()
        result = [False]
        def _ask():
            from tkinter import messagebox
            result[0] = messagebox.askyesno(summary["title"], summary["message"],
                                            parent=parent or self.root, default="no")
            event.set()
        self.gui_queue.put(_ask)
        event.wait()
        return result[0]

    def schedule_log_message(self, msg: str, level: str = "INFO"):
        self.gui_queue.put(lambda: self.log_message(msg, level))
        if self.current_progress_popup:
            self.gui_queue.put(lambda: self.current_progress_popup.update_text(f"[{level}] {msg}") if self.current_progress_popup else None)

    def log_message(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("[%H:%M:%S]")
        full_msg = f"{ts} [{level}] {msg}\n"
        log_box = self.widgets.get("log_box")
        if log_box:
            log_box.config(state=tk.NORMAL)
            log_box.insert(tk.END, full_msg)
            excess = int(log_box.index("end-1c").split(".")[0]) - 1 - MAX_LOG_LINES
            if excess > 0:
                log_box.delete("1.0", f"{excess + 1}.0")
            log_box.config(state=tk.DISABLED)
            log_box.see(tk.END)
        status = self.widgets.get("status_var")
        if status:
            status.set(f"{ts} {msg}")

    def process_gui_queue(self):
        while True:
            try:
                callback = self.gui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                self.controller.history.record_problem("ui_queue", f"{type(exc).__name__}: {exc}",
                                                       traceback.format_exc())
        self.root.after(100, self.process_gui_queue)


def run_gui(initial_root=None):
    if initial_root is None:
        initial_root = _detect_sidecar_project_root()
    root = tk.Tk()
    ProjectMapperApp(root, initial_root)
    root.mainloop()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="projectmapper", description=APP_NAME)
    parser.add_argument("root", nargs="?", type=Path,
                        help="project folder to open (default: the current directory)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    args = parser.parse_args(argv)
    if args.root is not None and not args.root.is_dir():
        parser.error(f"not a folder: {args.root}")
    run_gui(args.root)


if __name__ == "__main__":
    main()
