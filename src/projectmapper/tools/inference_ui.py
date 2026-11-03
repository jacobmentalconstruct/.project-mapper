"""Floating Ollama inference window for ProjectMapper."""

import json
import time
import tkinter as tk
from tkinter import scrolledtext, ttk
from pathlib import Path

if __package__:
    from .ui_base import ToolWindowMixin, attach_tooltip
    from .inference import DEFAULT_ENDPOINT
    from .inference_spine import resolve_entries, SpineContextError
    from .inference_assembler import assemble_prompt, freeze_request, AssemblyError, InferenceRequest
    from .spine import Spine
else:
    from ui_base import ToolWindowMixin, attach_tooltip
    from inference import DEFAULT_ENDPOINT
    from inference_spine import resolve_entries, SpineContextError
    from inference_assembler import assemble_prompt, freeze_request, AssemblyError, InferenceRequest
    from spine import Spine
try:
    from ..application.desktop import Stream
    from ..application.errors import describe
except ImportError:  # run as a script: tools/ and application/ are top-level packages
    from application.desktop import Stream
    from application.errors import describe

# Every attempt is appended here (relative to the project root) when "Record runs" is on.
RUNS_SPINE = Path("_spine") / "inference-runs.jsonl"
RUN_SCHEMA_KEY = "inference-run@1"
RUN_SCHEMA = {
    "id": "inference-run", "version": 1, "title": "Inference run",
    "required": ["step", "model", "status", "prompt"],
    "properties": {
        "step": {"type": "integer", "label": "Step"},
        "model": {"type": "string", "label": "Model"},
        "status": {"type": "string", "label": "Status", "enum": ["success", "stopped", "error"]},
        "prompt": {"type": "string", "format": "textarea", "label": "Submitted prompt"},
        "options": {"type": "object", "label": "Options"},
        "response": {"type": "string", "format": "textarea", "label": "Response"},
        "error": {"type": "string", "label": "Error"},
        "elapsed_s": {"type": "number", "label": "Elapsed (s)"},
        "context": {"type": "string", "label": "Spine context"},
    },
}


def _inference_config_path() -> Path:
    try:
        if __package__:
            from ..core.settings import settings_path
        else:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from core.settings import settings_path
        return settings_path().parent / "inference.json"
    except Exception:
        return Path.home() / ".projectmapper_inference.json"


def _load_inference_config() -> dict:
    path = _inference_config_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_inference_config(data: dict) -> None:
    path = _inference_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


__all__ = ["InferenceWindow"]


class InferenceWindow(ToolWindowMixin):
    """Nonmodal, resizable floating window for Ollama inference.

    Chain semantics:
    - A chain is a linear sequence of steps.  Each step consumes the
      preceding step's current result as part of its prompt.
    - Running step N first runs any ancestor step that has no current
      result, then runs N itself.  Optionally cascades to all subsequent
      steps.
    - Every attempt is kept in _execution_records (and, with Record runs on,
      appended to the project's _spine/inference-runs.jsonl). Only a
      successful run replaces _last_response and then clears the current
      result of every subsequent step.
    - Only one execution operates on the chain at a time (root._chain_busy).
    """

    _step_counter: int = 0

    def __init__(self, app, context_path=None,
                 chain_parent: "InferenceWindow | None" = None,
                 chain_order: str = "after"):
        InferenceWindow._step_counter += 1
        self._step = InferenceWindow._step_counter

        self.app = app
        self.colors = app.theme
        self._stream = None  # the running inference.* action, if any
        self._started_at = 0.0
        self._context_path = context_path
        self._config = _load_inference_config()

        # Current committed result and execution history.
        self._last_response: str = ""      # committed after successful run
        self._execution_records: list[dict] = []

        # Spine context selection (optional).
        self._spine_path: str = ""         # path as string; empty = no spine
        self._spine_indices: list[int] = []

        # Frozen request for the latest attempt.
        self._frozen_request: "InferenceRequest | None" = None

        # Saved prior result while a run is in-flight (for failure restore).
        self._pending_prior: str = ""

        # Chain-wide busy lock — only the root's value is authoritative.
        self._chain_busy: bool = False
        self._on_finished = None

        # Chain linkage.
        self._chain_parent = chain_parent
        self._chain_child: "InferenceWindow | None" = None
        self._chain_target: "InferenceWindow | None" = None  # routing override; None = follow _chain_child
        self._chain_order = chain_order  # fixed at spawn
        # Stop flag — only the root's value is read; reset before each run.
        self._chain_stop: bool = False

        self.top = tk.Toplevel(app.root)
        self.configure_tool_window(
            f"Inference — Step {self._step}", "820x680", (600, 480)
        )
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(4, weight=1)

        self._build_connection_row()
        self._build_model_row()
        self._build_config_row()
        self._build_context_row()
        self._build_prompt_row()
        self._build_response_area()
        self._build_status_bar()

        if chain_parent is not None:
            parent_model = chain_parent._model_var.get()
            if parent_model:
                self._model_var.set(parent_model)

        self.top.after(50, self._async_refresh_models)

    # ── connection row ────────────────────────────────────────────────────

    def _build_connection_row(self):
        row = self.frame(self.top)
        row.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        row.columnconfigure(1, weight=1)

        self.label(row, text="Endpoint:", panel=True).grid(row=0, column=0, padx=(6, 4), pady=6)

        self._endpoint_var = tk.StringVar(self.top, value=DEFAULT_ENDPOINT)
        entry = tk.Entry(
            row, textvariable=self._endpoint_var,
            font=("Consolas", 10), relief="flat",
            bg=self.colors["field_bg"], fg=self.colors["field_text"],
            insertbackground=self.colors["text"],
        )
        attach_tooltip(entry, "inference.endpoint", self.colors)
        entry.grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        entry.bind("<Return>", lambda _ev: self._async_refresh_models())

        self._status_dot = tk.Label(
            row, text="●", font=("Arial", 11),
            bg=self.colors["panel_bg"], fg=self.colors["muted_text"],
        )
        self._status_dot.grid(row=0, column=2, padx=(4, 2))

        self._status_label = self.label(row, text="—", panel=True, font=("Arial", 9))
        self._status_label.grid(row=0, column=3, padx=(0, 6))

        self.button(
            row, "Refresh", self._async_refresh_models, tooltip_id="inference.refresh_models"
        ).grid(row=0, column=4, padx=(4, 2), pady=4)

        self.button(
            row, "Navigator", self._open_navigator, tooltip_id="inference.navigator"
        ).grid(row=0, column=5, padx=(2, 6), pady=4)

    # ── model row ─────────────────────────────────────────────────────────

    def _build_model_row(self):
        row = self.frame(self.top)
        row.grid(row=1, column=0, sticky="ew", padx=10, pady=2)
        row.columnconfigure(1, weight=1)

        self.label(row, text="Model:", panel=True).grid(row=0, column=0, padx=(6, 4), pady=4)

        self._model_var = tk.StringVar(self.top)
        self._model_combo = ttk.Combobox(
            row, textvariable=self._model_var, state="readonly",
            font=("Arial", 10),
        )
        self.setup_review_styles()
        self._model_combo.configure(style="Review.TCombobox")
        attach_tooltip(self._model_combo, "inference.model", self.colors)
        self._model_combo.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        self._model_combo.bind("<Button-3>", self._model_context_menu)

        self._info_btn = self.button(
            row, "Model Info", self._show_model_info, tooltip_id="inference.model_info"
        )
        self._info_btn.grid(row=0, column=2, padx=(4, 6), pady=4)

    # ── inference config (collapsible) ────────────────────────────────────

    def _build_config_row(self):
        self._config_expanded = tk.BooleanVar(value=False)

        toggle_row = self.frame(self.top)
        toggle_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 0))

        self._config_toggle_btn = tk.Button(
            toggle_row, text="▶ Inference options",
            bg=self.colors["panel_bg"], fg=self.colors["muted_text"],
            relief=tk.FLAT, bd=0, font=("Arial", 9), cursor="hand2",
            activebackground=self.colors["panel_bg"],
            activeforeground=self.colors["text"],
            command=self._toggle_config,
        )
        attach_tooltip(self._config_toggle_btn, "inference.options_toggle", self.colors)
        self._config_toggle_btn.pack(side=tk.LEFT, padx=6, pady=2)

        self._config_frame = self.frame(self.top)

        cfg = self._config_frame
        cfg.columnconfigure(1, weight=1)
        cfg.columnconfigure(3, weight=1)

        def _spinbox(parent, row, col, label, var, from_, to, tooltip_id):
            self.label(parent, text=label, panel=True, font=("Arial", 9)).grid(
                row=row, column=col, padx=(8, 2), pady=3, sticky="w"
            )
            sb = ttk.Spinbox(
                parent, textvariable=var, from_=from_, to=to,
                width=7, style="Review.TSpinbox",
            )
            attach_tooltip(sb, tooltip_id, self.colors)
            sb.grid(row=row, column=col + 1, padx=(2, 12), pady=3, sticky="w")

        self._temp_var = tk.StringVar(value="0.8")
        self._top_p_var = tk.StringVar(value="0.9")
        self._ctx_var = tk.StringVar(value="4096")
        self._seed_var = tk.StringVar(value="0")

        _spinbox(cfg, 0, 0, "Temperature:", self._temp_var, 0.0, 2.0, "inference.temperature")
        _spinbox(cfg, 0, 2, "Top P:",        self._top_p_var, 0.0, 1.0, "inference.top_p")
        _spinbox(cfg, 1, 0, "Context len:",  self._ctx_var, 512, 131072, "inference.context_len")
        _spinbox(cfg, 1, 2, "Seed (0=rand):", self._seed_var, 0, 999999999, "inference.seed")

    def _toggle_config(self):
        expanded = not self._config_expanded.get()
        self._config_expanded.set(expanded)
        if expanded:
            self._config_toggle_btn.config(text="▼ Inference options")
            self._config_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 2))
            self.top.rowconfigure(2, weight=0)
        else:
            self._config_toggle_btn.config(text="▶ Inference options")
            self._config_frame.grid_forget()

    # ── spine context row (collapsible) ───────────────────────────────────

    def _build_context_row(self):
        self._context_expanded = tk.BooleanVar(value=False)

        toggle_row = self.frame(self.top)
        toggle_row.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 0))

        self._context_toggle_btn = tk.Button(
            toggle_row, text="▶ Spine context",
            bg=self.colors["panel_bg"], fg=self.colors["muted_text"],
            relief=tk.FLAT, bd=0, font=("Arial", 9), cursor="hand2",
            activebackground=self.colors["panel_bg"],
            activeforeground=self.colors["text"],
            command=self._toggle_context,
        )
        self._context_toggle_btn.pack(side=tk.LEFT, padx=6, pady=2)

        self._context_frame = self.frame(self.top)
        cf = self._context_frame
        cf.columnconfigure(1, weight=1)

        self.label(cf, text="Spine:", panel=True, font=("Arial", 9)).grid(
            row=0, column=0, padx=(8, 2), pady=3, sticky="w"
        )
        self._spine_path_var = tk.StringVar(value="")
        spine_entry = tk.Entry(
            cf, textvariable=self._spine_path_var,
            font=("Consolas", 9), relief="flat",
            bg=self.colors["field_bg"], fg=self.colors["field_text"],
            insertbackground=self.colors["text"],
        )
        spine_entry.grid(row=0, column=1, sticky="ew", padx=(2, 4), pady=3)

        self.button(cf, "Browse", self._browse_spine,
                    tooltip_id="inference.spine_browse").grid(
            row=0, column=2, padx=(2, 8), pady=3
        )

        self.label(cf, text="Entries:", panel=True, font=("Arial", 9)).grid(
            row=1, column=0, padx=(8, 2), pady=3, sticky="w"
        )
        self._spine_indices_var = tk.StringVar(value="")
        tk.Entry(
            cf, textvariable=self._spine_indices_var,
            font=("Consolas", 9), relief="flat",
            bg=self.colors["field_bg"], fg=self.colors["field_text"],
            insertbackground=self.colors["text"],
        ).grid(row=1, column=1, sticky="ew", padx=(2, 4), pady=3)
        self.label(cf, text="(comma-separated indices, e.g. 0,2,5)",
                   panel=True, font=("Arial", 8)).grid(
            row=1, column=2, padx=(2, 8), pady=3, sticky="w"
        )

        self.button(cf, "Preview Context", self._preview_context,
                    tooltip_id="inference.spine_preview").grid(
            row=2, column=1, padx=(2, 4), pady=(2, 4), sticky="w"
        )

    def _toggle_context(self):
        expanded = not self._context_expanded.get()
        self._context_expanded.set(expanded)
        if expanded:
            self._context_toggle_btn.config(text="▼ Spine context")
            self._context_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 2))
        else:
            self._context_toggle_btn.config(text="▶ Spine context")
            self._context_frame.grid_forget()

    def _browse_spine(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select spine file",
            filetypes=[("Spine files", "*.jsonl"), ("All files", "*.*")],
        )
        if path:
            self._spine_path_var.set(path)

    def _get_spine_entries(self) -> list:
        """Parse _spine_path_var + _spine_indices_var → resolved entries, or []."""
        path = self._spine_path_var.get().strip()
        idx_str = self._spine_indices_var.get().strip()
        if not path or not idx_str:
            return []
        try:
            indices = [int(x.strip()) for x in idx_str.split(",") if x.strip()]
        except ValueError:
            raise SpineContextError("Entry indices must be comma-separated integers (e.g. 0,2,5).")
        return resolve_entries(path, indices)

    def _preview_context(self):
        try:
            entries = self._get_spine_entries()
        except SpineContextError as exc:
            self._status_bar_var.set(f"Spine error: {exc}")
            return
        try:
            prior = self._chain_parent._last_response.strip() if self._chain_parent else ""
            user = self._prompt_text.get("1.0", tk.END).strip()
            text = assemble_prompt(entries, prior, user,
                                   prior_first=(self._chain_order == "before"))
        except AssemblyError as exc:
            self._status_bar_var.set(f"Assembly: {exc}")
            return
        win = tk.Toplevel(self.top)
        win.title("Context Preview")
        win.geometry("600x420")
        win.configure(bg=self.colors["panel_bg"])
        win.transient(self.top)
        box = self.editor(win)
        box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        ToolWindowMixin.show_text(box, text)
        tk.Button(
            win, text="Close", command=win.destroy,
            bg=self.colors["panel_alt_bg"], fg=self.colors["text"],
            relief=tk.FLAT, padx=12, pady=4,
        ).pack(pady=(0, 8))

    # ── prompt row ────────────────────────────────────────────────────────

    def _build_prompt_row(self):
        outer = self.frame(self.top)
        outer.grid(row=4, column=0, sticky="nsew", padx=10, pady=(4, 2))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        self.top.rowconfigure(4, weight=1)

        hdr = self.frame(outer)
        hdr.grid(row=0, column=0, sticky="ew")
        self.label(hdr, text="Prompt", panel=True, font=("Arial", 9, "bold")).pack(
            side=tk.LEFT, padx=6, pady=2
        )
        if self._context_path:
            self.label(
                hdr, text=f"Context: {self._context_path.name}",
                panel=True, font=("Arial", 9),
            ).pack(side=tk.LEFT, padx=4)

        parent_step = self._chain_parent._step if self._chain_parent is not None else self._step - 1
        self._placeholder_before = tk.Label(
            outer,
            text=f"⬆  Step {parent_step} response will be prepended here",
            bg=self.colors["panel_alt_bg"], fg=self.colors["muted_text"],
            font=("Arial", 9, "italic"), anchor="w", padx=8, pady=4,
        )

        self._prompt_text = tk.Text(
            outer, wrap=tk.WORD, font=("Consolas", 10), height=6,
            bg=self.colors["field_bg"], fg=self.colors["field_text"],
            insertbackground=self.colors["text"],
            selectbackground=self.colors["selection"],
            selectforeground=self.colors["text"],
            relief=tk.FLAT, padx=8, pady=6,
        )
        attach_tooltip(self._prompt_text, "inference.prompt", self.colors)
        self._prompt_text.bind("<Alt-Return>", lambda _ev: (self._run(), "break")[1])

        self._placeholder_after = tk.Label(
            outer,
            text=f"⬇  Step {parent_step} response will be appended here",
            bg=self.colors["panel_alt_bg"], fg=self.colors["muted_text"],
            font=("Arial", 9, "italic"), anchor="w", padx=8, pady=4,
        )

        if self._chain_parent is not None:
            if self._chain_order == "before":
                self._placeholder_before.grid(row=1, column=0, sticky="ew")
                self._prompt_text.grid(row=2, column=0, sticky="nsew")
                outer.rowconfigure(2, weight=1)
            else:
                self._prompt_text.grid(row=1, column=0, sticky="nsew")
                outer.rowconfigure(1, weight=1)
                self._placeholder_after.grid(row=2, column=0, sticky="ew")
        else:
            self._prompt_text.grid(row=1, column=0, sticky="nsew")
            outer.rowconfigure(1, weight=1)

        btn_row = self.frame(outer)
        btn_row.grid(row=3, column=0, sticky="ew", pady=(4, 0))

        self._run_btn = self.button(
            btn_row, "Run", self._run, "success",
            tooltip_id="inference.run",
        )
        self._run_btn.pack(side=tk.LEFT, padx=6, pady=4)

        self._stop_btn = self.button(
            btn_row, "Stop", self._stop, "danger",
            state="disabled", tooltip_id="inference.stop",
        )
        self._stop_btn.pack(side=tk.LEFT, padx=2, pady=4)

        # Cascade to end toggle.
        self._cascade_var = tk.BooleanVar(value=False)
        self.checkbutton(
            btn_row, "→ to end", self._cascade_var,
            tooltip_id="inference.cascade",
        ).pack(side=tk.LEFT, padx=(8, 2), pady=4)

        # Chain: spawn next step window.
        self._chain_var = tk.BooleanVar(value=False)
        self.checkbutton(
            btn_row, "Chain", self._chain_var,
            command=self._on_chain_toggle,
            tooltip_id="inference.chain",
        ).pack(side=tk.LEFT, padx=(8, 2), pady=4)

        self._chain_order_var = tk.StringVar(value="after")
        self._chain_controls = tk.Frame(btn_row, bg=self.colors["panel_bg"])
        self._chain_controls.pack(side=tk.LEFT, padx=2, pady=4)
        for value, label in (("before", "Before"), ("after", "After")):
            rb = tk.Radiobutton(
                self._chain_controls, text=label, value=value,
                variable=self._chain_order_var,
                bg=self.colors["panel_bg"], fg=self.colors["muted_text"],
                selectcolor=self.colors["tree_bg"],
                activebackground=self.colors["panel_bg"],
                activeforeground=self.colors["text"],
                font=("Arial", 9),
            )
            attach_tooltip(rb, "inference.chain_order", self.colors)
            rb.pack(side=tk.LEFT, padx=2)
        self._chain_controls.pack_forget()

        self.button(
            btn_row, "Clear", self._clear_all,
            tooltip_id="inference.clear",
        ).pack(side=tk.RIGHT, padx=6, pady=4)

        inherited = self._chain_parent._record_var.get() if self._chain_parent is not None else True
        self._record_var = tk.BooleanVar(value=inherited)
        self.checkbutton(
            btn_row, "Record runs", self._record_var,
            tooltip_id="inference.record",
        ).pack(side=tk.RIGHT, padx=(2, 4), pady=4)

    # ── response area ─────────────────────────────────────────────────────

    def _build_response_area(self):
        outer = self.frame(self.top)
        outer.grid(row=5, column=0, sticky="nsew", padx=10, pady=(2, 4))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        self.top.rowconfigure(5, weight=2)

        self.label(outer, text="Response", panel=True, font=("Arial", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=6, pady=2,
        )

        self._response_box = scrolledtext.ScrolledText(
            outer, wrap=tk.WORD, font=("Consolas", 10),
            bg=self.colors["tree_bg"], fg=self.colors["text"],
            insertbackground=self.colors["text"],
            selectbackground=self.colors["selection"],
            selectforeground=self.colors["text"],
            relief=tk.FLAT, borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.colors["panel_alt_bg"],
            highlightcolor=self.colors["secondary"],
            padx=10, pady=8,
            state="disabled",
        )
        self._response_box.grid(row=1, column=0, sticky="nsew")

    # ── status bar ────────────────────────────────────────────────────────

    def _build_status_bar(self):
        self._status_bar_var = tk.StringVar(value="Ready.")
        tk.Label(
            self.top, textvariable=self._status_bar_var,
            bg=self.colors["status_bg"], fg=self.colors["status_text"],
            anchor="w", padx=8, font=("Arial", 9),
        ).grid(row=6, column=0, sticky="ew")

    # ── async helpers ─────────────────────────────────────────────────────

    def _endpoint(self) -> str:
        return self._endpoint_var.get().strip() or DEFAULT_ENDPOINT

    def _background(self, action, payload, on_done, on_output=None):
        """Run an inference.* action without blocking Tk; returns the Stream or None."""
        try:
            return Stream(self.app, action, payload, widget=self.top,
                          on_output=on_output or (lambda _chunk: None), on_done=on_done)
        except Exception as exc:
            self._status_bar_var.set(f"Error: {exc}")
            return None

    def _async_refresh_models(self):
        self._set_status("Checking…", "muted_text")

        def done(result):
            if result.status == "succeeded":
                self._apply_refresh({"ok": result.data["ok"], "message": result.data["message"]},
                                    result.data["models"])
            else:
                self._apply_refresh({"ok": False, "message": describe(result.error)}, [])

        self._background("inference.models", {"endpoint": self._endpoint()}, done)

    def _apply_refresh(self, status, models):
        if not self.top.winfo_exists():
            return
        if status["ok"]:
            self._set_status("Connected", "success")
        else:
            self._set_status(f"Offline — {status['message']}", "danger")
        self._model_combo["values"] = models
        current = self._model_var.get()
        default = self._config.get("default_model", "")
        if current and current in models:
            pass
        elif default and default in models:
            self._model_var.set(default)
        elif models and not current:
            self._model_var.set(models[0])

    def _model_context_menu(self, event):
        model = self._model_var.get().strip()
        default = self._config.get("default_model", "")
        menu = tk.Menu(self.top, tearoff=False,
                       bg=self.colors["panel_bg"], fg=self.colors["text"],
                       activebackground=self.colors["secondary"],
                       activeforeground=self.colors["text"],
                       relief=tk.FLAT, bd=1)
        if model and model != default:
            menu.add_command(
                label=f"Set '{model}' as default",
                command=lambda: self._set_default_model(model),
            )
        if default:
            menu.add_command(
                label=f"Clear default ('{default}')",
                command=self._clear_default_model,
            )
        if menu.index("end") is None:
            return
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_default_model(self, model: str):
        self._config["default_model"] = model
        _save_inference_config(self._config)
        self._status_bar_var.set(f"Default model set to '{model}'.")

    def _clear_default_model(self):
        self._config.pop("default_model", None)
        _save_inference_config(self._config)
        self._status_bar_var.set("Default model cleared.")

    def _set_status(self, text, color_key):
        color = self.colors.get(color_key, self.colors["muted_text"])
        self._status_dot.config(fg=color)
        self._status_label.config(text=text)

    # ── model info popup ──────────────────────────────────────────────────

    def _show_model_info(self):
        model = self._model_var.get().strip()
        if not model:
            return
        self._status_bar_var.set(f"Fetching info for {model}…")

        def _show(result):
            if not self.top.winfo_exists():
                return
            info = result.data.get("info") if result.status == "succeeded" else {}
            self._status_bar_var.set("Ready.")
            win = tk.Toplevel(self.top)
            win.title(f"Model Info — {model}")
            win.geometry("520x400")
            win.configure(bg=self.colors["panel_bg"])
            win.transient(self.top)
            import json as _json
            text = _json.dumps(info, indent=2) if info else "(no metadata returned)"
            box = self.editor(win)
            box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
            ToolWindowMixin.show_text(box, text)
            tk.Button(
                win, text="Close", command=win.destroy,
                bg=self.colors["panel_alt_bg"], fg=self.colors["text"],
                relief=tk.FLAT, padx=12, pady=4,
            ).pack(pady=(0, 8))

        self._background("inference.model_info", {"endpoint": self._endpoint(), "model": model}, _show)

    # ── chain helpers ─────────────────────────────────────────────────────

    def _open_navigator(self):
        self.app.open_chain_navigator(self._chain_root())

    def _chain_root(self) -> "InferenceWindow":
        node = self
        while node._chain_parent is not None:
            node = node._chain_parent
        return node

    def _all_chain_nodes(self) -> list["InferenceWindow"]:
        """All live nodes in chain order, root → end."""
        nodes: list[InferenceWindow] = []
        node: InferenceWindow | None = self._chain_root()
        while node is not None:
            nodes.append(node)
            c = node._chain_child
            node = c if (c is not None and c.top.winfo_exists()) else None
        return nodes

    def _on_chain_toggle(self):
        if self._chain_var.get():
            self._chain_controls.pack(side=tk.LEFT, padx=2, pady=4)
            self._open_chain_child()
        else:
            self._chain_controls.pack_forget()

    def _open_chain_child(self):
        if self._chain_child is not None and self._chain_child.top.winfo_exists():
            self._chain_child.top.lift()
            return
        order = self._chain_order_var.get()
        child = InferenceWindow(self.app, chain_parent=self, chain_order=order)
        self._chain_child = child
        child.top.lift()
        child.top.focus_force()

    # ── run / stop ────────────────────────────────────────────────────────

    def _get_options(self) -> dict | None:
        try:
            opts: dict = {}
            opts["temperature"] = float(self._temp_var.get())
            opts["top_p"] = float(self._top_p_var.get())
            ctx = int(self._ctx_var.get())
            if ctx > 0:
                opts["num_ctx"] = ctx
            seed = int(self._seed_var.get())
            if seed > 0:
                opts["seed"] = seed
            return opts or None
        except (ValueError, TypeError):
            return None

    def _run(self):
        root = self._chain_root()
        if root._chain_busy:
            self._status_bar_var.set("Chain is busy — wait for the current run to finish.")
            return

        # Build execution queue:
        #   ancestors that have no current result (need to run first)
        #   + this step (always)
        #   + all subsequent steps if cascade is on
        ancestors: list[InferenceWindow] = []
        node = self._chain_parent
        while node is not None:
            ancestors.append(node)
            node = node._chain_parent
        ancestors.reverse()

        queue: list[InferenceWindow] = [n for n in ancestors if not n._last_response.strip()]
        queue.append(self)

        if self._cascade_var.get():
            visited: set[int] = {n._step for n in queue}
            child: InferenceWindow | None = self._chain_target or self._chain_child
            while child is not None and child.top.winfo_exists():
                if child._step in visited:
                    break  # loop detected — stop here
                visited.add(child._step)
                queue.append(child)
                child = child._chain_target or child._chain_child

        # Descendant invalidation happens only after a successful commit
        # (see _invalidate_descendants), not here.

        # Lock the chain and disable Run on all nodes.
        root._chain_stop = False
        root._chain_busy = True
        for n in self._all_chain_nodes():
            if n.top.winfo_exists():
                n.set_button_enabled(n._run_btn, False, "success")

        self._run_queue(queue, 0)

    def _run_queue(self, queue: list, index: int):
        root = self._chain_root()
        if index >= len(queue) or root._chain_stop:
            self._unlock_chain()
            return
        node = queue[index]
        if not node.top.winfo_exists():
            self._run_queue(queue, index + 1)
            return
        node._execute(
            lambda ok, q=queue, i=index: (
                self._run_queue(q, i + 1) if ok else self._unlock_chain()
            )
        )

    def _unlock_chain(self):
        root = self._chain_root()
        root._chain_busy = False
        for n in self._all_chain_nodes():
            if n.top.winfo_exists():
                n.set_button_enabled(n._run_btn, True, "success")

    def _execute(self, on_finished):
        model = self._model_var.get().strip()
        if not model:
            self._status_bar_var.set("Choose a model first.")
            on_finished(False)
            return

        # Resolve spine entries (may be empty — no-spine path unchanged).
        try:
            entries = self._get_spine_entries()
        except SpineContextError as exc:
            self._status_bar_var.set(f"Spine error: {exc}")
            on_finished(False)
            return

        # Chain: require parent result if one exists.
        prior = ""
        if self._chain_parent is not None:
            prior = self._chain_parent._last_response.strip()
            if not prior:
                self._status_bar_var.set(
                    f"Step {self._chain_parent._step} has no result to inject."
                )
                on_finished(False)
                return

        user_prompt = self._prompt_text.get("1.0", tk.END).strip()

        # Assemble and freeze — explicit error if empty.
        try:
            assembled = assemble_prompt(
                entries, prior, user_prompt,
                prior_first=(self._chain_order == "before"),
            )
            req = freeze_request(model, assembled, self._get_options())
        except AssemblyError as exc:
            self._status_bar_var.set(str(exc))
            on_finished(False)
            return

        # Save prior result so a failed run can restore it.
        self._pending_prior = self._last_response
        self._frozen_request = req
        self._on_finished = on_finished
        self._started_at = time.monotonic()
        self.set_button_enabled(self._stop_btn, True, "danger")
        self._status_bar_var.set(f"Running step {self._step} · {model}…")
        ToolWindowMixin.show_text(self._response_box, "")

        # The frozen request is handed to the action unchanged.
        payload = {"endpoint": self._endpoint(), "model": req.model, "prompt": req.prompt}
        if req.options:
            payload["options"] = req.options
        self._stream = self._background("inference.generate", payload, self._done,
                                        on_output=lambda chunk: self._append_token(chunk["text"]))
        if self._stream is None:
            self._show_error("Could not start inference.")

    def _stop(self):
        if self._stream is not None:
            self._stream.cancel()

    def _done(self, result):
        self._stream = None
        if result.status == "succeeded":
            self._finish(result.data.get("meta") or {}, result.data.get("response", ""))
        elif result.status == "cancelled":
            details = (result.error or {}).get("details") or {}
            self._finish({"stopped": True}, details.get("response", ""))
        else:
            self._show_error((result.error or {}).get("message") or describe(result.error))

    def _elapsed(self, meta: dict):
        return meta.get("elapsed_s") or round(time.monotonic() - self._started_at, 2)

    def _record_attempt(self, status: str, response: str | None, error: str | None, elapsed):
        req = self._frozen_request
        record = {"model": req.model if req else "", "options": req.options if req else None,
                  "prompt": req.prompt if req else "", "status": status,
                  "response": response, "error": error, "elapsed_s": elapsed}
        self._execution_records.append(record)
        if self._record_var.get():
            self._persist(record)

    def _persist(self, record: dict):
        """Append the attempt to the project's runs spine through spine.append."""
        try:
            path = self.app.controller.state.root / RUNS_SPINE
            spine = Spine.open(path) if path.exists() else Spine.create(path)
            if RUN_SCHEMA_KEY not in spine.list_schemas():
                spine.register_schema(RUN_SCHEMA)
            data = {"step": self._step, "model": record["model"], "status": record["status"],
                    "prompt": record["prompt"], "options": record["options"] or {},
                    "response": record["response"] or "", "error": record["error"] or "",
                    "elapsed_s": float(record["elapsed_s"] or 0)}
            context = self._spine_path_var.get().strip()
            if context and self._spine_indices_var.get().strip():
                data["context"] = f"{context} [{self._spine_indices_var.get().strip()}]"
            self.app.action("spine.append", {"path": str(path), "schema": RUN_SCHEMA_KEY, "data": data})
        except Exception as exc:  # recording must never break the run itself
            self._status_bar_var.set(f"{self._status_bar_var.get()} · Not recorded: {exc}")

    def _append_token(self, token: str):
        if not self.top.winfo_exists():
            return
        self._response_box.config(state="normal")
        self._response_box.insert(tk.END, token)
        self._response_box.see(tk.END)
        self._response_box.config(state="disabled")

    def _finish(self, meta: dict, response: str):
        stopped = meta.get("stopped")
        elapsed = self._elapsed(meta)
        if stopped:
            # Cancelled — preserve prior result; record the attempt.
            self._last_response = self._pending_prior
            if self.top.winfo_exists():
                self.set_button_enabled(self._stop_btn, False, "danger")
                self._status_bar_var.set(f"Stopped · {elapsed}s — prior result preserved.")
            self._record_attempt("stopped", response, None, elapsed)
        else:
            # Success — commit new result and invalidate descendants.
            self._last_response = response
            self._invalidate_descendants()
            if self.top.winfo_exists():
                self.set_button_enabled(self._stop_btn, False, "danger")
                rtok = meta.get("response_tokens")
                ptok = meta.get("prompt_tokens")
                parts = ["Done", f"{elapsed}s"]
                if rtok is not None:
                    parts.append(f"{rtok} response tokens")
                if ptok is not None:
                    parts.append(f"{ptok} prompt tokens")
                self._status_bar_var.set(" · ".join(parts))
            self._record_attempt("success", response, None, elapsed)
        self._complete(not stopped)

    def _show_error(self, message: str):
        # Failure — preserve prior result; record the attempt.
        self._last_response = self._pending_prior
        if self.top.winfo_exists():
            self.set_button_enabled(self._stop_btn, False, "danger")
            lower = message.lower()
            if "context" in lower and any(w in lower for w in ("length", "limit", "exceed")):
                self._status_bar_var.set(
                    "Context overflow — combined prompt exceeds the model's context window. "
                    f"Reduce context length or shorten the prompt. ({message})"
                )
            else:
                self._status_bar_var.set(f"Error: {message}")
        self._record_attempt("error", None, message, self._elapsed({}))
        self._complete(False)

    def _invalidate_descendants(self):
        """Clear committed current result of all live descendants after a successful commit."""
        node: "InferenceWindow | None" = self._chain_child
        while node is not None and node.top.winfo_exists():
            if node._last_response:
                node._last_response = ""
                ToolWindowMixin.show_text(node._response_box, "")
                node._status_bar_var.set("Cleared — ancestor result changed.")
            node = node._chain_child

    def _complete(self, ok: bool):
        callback, self._on_finished = self._on_finished, None
        if callback is not None:
            callback(ok)

    def _clear_all(self):
        self._prompt_text.delete("1.0", tk.END)
        ToolWindowMixin.show_text(self._response_box, "")
        self._status_bar_var.set("Ready.")

    # ── lifecycle ─────────────────────────────────────────────────────────

    def close(self):
        if self._stream is not None:
            self._stream.detach()
            self._stream = None
            # A closed step never reports back, so release the chain it was holding.
            self._complete(False)
        if self._chain_parent is not None and self._chain_parent._chain_child is self:
            self._chain_parent._chain_child = None
        if self._chain_child is not None and self._chain_child.top.winfo_exists():
            self._chain_child._chain_parent = self._chain_parent
        try:
            self.top.destroy()
        except tk.TclError:
            pass
