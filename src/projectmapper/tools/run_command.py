"""Run a shell command in a project folder through the command.run action and stream its output."""

from pathlib import Path
import tkinter as tk
from tkinter import ttk

if __package__:
    from .ui_base import ToolWindowMixin, attach_tooltip
else:
    from ui_base import ToolWindowMixin, attach_tooltip
try:
    from ..application.desktop import Stream
    from ..application.errors import describe
except ImportError:  # run as a script: tools/ and application/ are top-level packages
    from application.desktop import Stream
    from application.errors import describe

__all__ = ["RunCommandWindow"]

_HISTORY_LIMIT = 50


class RunCommandWindow(ToolWindowMixin):
    def __init__(self, app, folder: Path):
        self.app = app
        self.colors = app.theme
        self.folder = folder
        self._stream = None
        self._history = []
        self._history_index = -1

        self.top = tk.Toplevel(app.root)
        self.configure_tool_window(
            f"Run Command — {folder.name}",
            "800x500",
            (560, 340),
        )
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(2, weight=1)

        # ── folder label ────────────────────────────────────────────────────
        header = self.frame(self.top)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        header.columnconfigure(0, weight=1)
        self.label(header, text=str(folder), panel=True, wraplength=720).grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )

        # ── command entry row ────────────────────────────────────────────────
        cmd_row = self.frame(self.top)
        cmd_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        cmd_row.columnconfigure(1, weight=1)

        self.label(cmd_row, text="Command:", panel=True).grid(
            row=0, column=0, padx=(8, 6), pady=8
        )
        self._cmd_var = tk.StringVar(self.top)
        self._cmd_entry = tk.Entry(
            cmd_row,
            textvariable=self._cmd_var,
            font=("Consolas", 10),
            relief="flat",
            bg=self.colors["field_bg"],
            fg=self.colors["field_text"],
            insertbackground=self.colors["text"],
            selectbackground=self.colors["selection"],
        )
        attach_tooltip(self._cmd_entry, "run_command.command", self.colors)
        self._cmd_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=8)
        self._cmd_entry.bind("<Return>", lambda _e: self._run())
        self._cmd_entry.bind("<Up>", self._history_up)
        self._cmd_entry.bind("<Down>", self._history_down)

        self._run_btn = self.button(
            cmd_row, "Run", self._run, "success", tooltip_id="run_command.run"
        )
        self._run_btn.grid(row=0, column=2, padx=(4, 2), pady=6)
        self._kill_btn = self.button(
            cmd_row, "Kill", self._kill, state="disabled", tooltip_id="run_command.kill"
        )
        self._kill_btn.grid(row=0, column=3, padx=2, pady=6)
        self._clear_btn = self.button(
            cmd_row, "Clear", self._clear_output, tooltip_id="run_command.clear"
        )
        self._clear_btn.grid(row=0, column=4, padx=(2, 8), pady=6)

        # ── output text area ─────────────────────────────────────────────────
        out_frame = self.frame(self.top)
        out_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        out_frame.rowconfigure(0, weight=1)
        out_frame.columnconfigure(0, weight=1)

        self._output = tk.Text(
            out_frame,
            wrap="none",
            state="disabled",
            font=("Consolas", 10),
            bg=self.colors["log_bg"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            selectbackground=self.colors["selection"],
            selectforeground=self.colors["text"],
            relief="flat",
            padx=10,
            pady=8,
        )
        self._output.tag_configure("err", foreground=self.colors.get("error", "#e06c75"))
        self._output.tag_configure("info", foreground=self.colors.get("muted_text", "#888888"))
        self._output.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(out_frame, command=self._output.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(out_frame, orient="horizontal", command=self._output.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self._output.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._cmd_entry.focus_set()

    # ── history ──────────────────────────────────────────────────────────────

    def _history_up(self, _event):
        if not self._history:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
        self._cmd_var.set(self._history[self._history_index])
        self._cmd_entry.icursor("end")
        return "break"

    def _history_down(self, _event):
        if self._history_index <= 0:
            self._history_index = -1
            self._cmd_var.set("")
        else:
            self._history_index -= 1
            self._cmd_var.set(self._history[self._history_index])
        self._cmd_entry.icursor("end")
        return "break"

    # ── run / kill ────────────────────────────────────────────────────────────

    def _run(self):
        cmd = self._cmd_var.get().strip()
        if not cmd or self._stream is not None:
            return
        # record history (most recent first, no consecutive duplicates)
        if not self._history or self._history[0] != cmd:
            self._history.insert(0, cmd)
            del self._history[_HISTORY_LIMIT:]
        self._history_index = -1

        self._append(f"$ {cmd}\n", "info")
        try:
            self._stream = Stream(self.app, "command.run", {"folder": str(self.folder), "command": cmd},
                                  widget=self.top, on_output=lambda chunk: self._append(chunk["text"]),
                                  on_done=self._done)
        except Exception as exc:
            self._append(f"Error: {exc}\n", "err")
            return
        self.set_button_enabled(self._run_btn, False, "success")
        self._kill_btn.configure(state="normal")

    def _done(self, result):
        self._stream = None
        if result.status == "succeeded":
            code = result.data.get("exit_code")
            self._append(f"\n[Exited with code {code}]\n" if code else "\n[Done]\n", "info")
        elif result.status == "cancelled":
            self._append("\n[Stopped]\n", "info")
        else:
            self._append(f"\n{describe(result.error)}\n", "err")
        self.set_button_enabled(self._run_btn, True, "success")
        self._kill_btn.configure(state="disabled")

    def _kill(self):
        if self._stream is not None:
            self._stream.cancel()

    # ── output helpers ────────────────────────────────────────────────────────

    def _append(self, text, tag=None):
        try:
            self._output.configure(state="normal")
            if tag:
                self._output.insert("end", text, tag)
            else:
                self._output.insert("end", text)
            self._output.see("end")
            self._output.configure(state="disabled")
        except tk.TclError:
            pass

    def _clear_output(self):
        try:
            self._output.configure(state="normal")
            self._output.delete("1.0", "end")
            self._output.configure(state="disabled")
        except tk.TclError:
            pass

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def close(self):
        if self._stream is not None:
            self._stream.detach()
            self._stream = None
        self.top.destroy()
