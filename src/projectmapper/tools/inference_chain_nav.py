"""Chain Navigator — lightweight read-only panel over an InferenceWindow chain."""

import tkinter as tk
from tkinter import ttk

if __package__:
    from .ui_base import ToolWindowMixin
else:
    from ui_base import ToolWindowMixin

__all__ = ["ChainNavigatorWindow"]

_POLL_MS = 350


class ChainNavigatorWindow(ToolWindowMixin):
    """Floating panel listing every step in the active chain.

    Owns no execution state.  All data is read from existing InferenceWindow
    instances.  Polling keeps the list current without requiring callbacks.
    """

    def __init__(self, app, chain_root):
        self.app = app
        self.colors = app.theme
        self._root = chain_root  # root InferenceWindow — never changes

        self.top = tk.Toplevel(app.root)
        self.configure_tool_window("Chain Navigator", "360x440", (300, 320))
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(1, weight=1)

        self._nodes: list = []
        self._build_ui()
        self._poll()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        c = self.colors

        # header
        hdr = self.frame(self.top)
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        self.label(hdr, text="Chain Steps", panel=True,
                   font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=4)
        self.button(hdr, "+ Step", self._add_step,
                    tooltip_id="nav.add_step").pack(side=tk.RIGHT, padx=4)

        # list
        lf = self.frame(self.top)
        lf.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        self._lb = tk.Listbox(
            lf, font=("Consolas", 9),
            bg=c["tree_bg"], fg=c["text"],
            selectbackground=c["secondary"], selectforeground=c["text"],
            activestyle="none", relief=tk.FLAT, borderwidth=0,
            highlightthickness=1,
            highlightbackground=c["panel_alt_bg"],
            highlightcolor=c["secondary"],
        )
        self._lb.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lf, command=self._lb.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.bind("<<ListboxSelect>>", self._on_select)

        # actions
        act = self.frame(self.top)
        act.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        self._run_btn = self.button(act, "Run Step", self._run_selected,
                                    "success", tooltip_id="nav.run_step")
        self._run_btn.pack(side=tk.LEFT, padx=4, pady=4)
        self._cascade_btn = self.button(act, "Run → End", self._cascade_from_selected,
                                        tooltip_id="nav.run_to_end")
        self._cascade_btn.pack(side=tk.LEFT, padx=2, pady=4)

        # status bar
        self._status_var = tk.StringVar(value="")
        tk.Label(
            self.top, textvariable=self._status_var,
            bg=c["status_bg"], fg=c["status_text"],
            anchor="w", padx=8, font=("Arial", 9),
        ).grid(row=3, column=0, sticky="ew")

    # ── polling ───────────────────────────────────────────────────────────

    def _poll(self):
        if not self.top.winfo_exists():
            return
        self._refresh()
        self.top.after(_POLL_MS, self._poll)

    def _refresh(self):
        if not self._root.top.winfo_exists():
            self._status_var.set("Chain closed.")
            return

        nodes = self._root._all_chain_nodes()
        self._nodes = nodes

        # Preserve selection across refreshes.
        sel = self._lb.curselection()
        prev_idx = sel[0] if sel else None

        self._lb.delete(0, tk.END)
        c = self.colors
        status_colors = {
            "running": c.get("success", c["text"]),
            "done":    c.get("text",    c["text"]),
            "pending": c.get("muted_text", c["text"]),
            "empty":   c.get("muted_text", c["text"]),
        }

        for i, node in enumerate(nodes):
            status = _node_status(node)
            prompt = node._prompt_text.get("1.0", "end-1c").strip().replace("\n", " ")
            preview = (prompt[:34] + "…") if len(prompt) > 34 else (prompt or "(no prompt)")
            entry = f"  {node._step}  [{status:7s}]  {preview}"
            self._lb.insert(tk.END, entry)
            self._lb.itemconfigure(i, fg=status_colors.get(status, c["text"]))

        if prev_idx is not None and prev_idx < len(nodes):
            self._lb.selection_set(prev_idx)

        chain_busy = self._root._chain_busy
        self.set_button_enabled(self._run_btn, not chain_busy, "success")
        self.set_button_enabled(self._cascade_btn, not chain_busy, "secondary")

        done = sum(1 for n in nodes if n._last_response.strip())
        running_count = sum(1 for n in nodes
                            if n._active_thread and n._active_thread.is_alive())
        total = len(nodes)
        if running_count:
            self._status_var.set(f"Running…  {done}/{total} done")
        else:
            self._status_var.set(f"{done}/{total} steps done")

    # ── selection ─────────────────────────────────────────────────────────

    def _selected_node(self):
        sel = self._lb.curselection()
        if not sel:
            return None
        idx = sel[0]
        return self._nodes[idx] if idx < len(self._nodes) else None

    def _on_select(self, _ev=None):
        node = self._selected_node()
        if node and node.top.winfo_exists():
            node.top.deiconify()
            node.top.lift()
            node.top.focus_force()

    # ── actions ───────────────────────────────────────────────────────────

    def _add_step(self):
        """Append a new step to the end of the chain."""
        if not self._nodes:
            return
        last = self._nodes[-1]
        if not last.top.winfo_exists():
            return
        if not last._chain_var.get():
            last._chain_var.set(True)
            last._on_chain_toggle()
        else:
            last._open_chain_child()

    def _run_selected(self):
        node = self._selected_node()
        if node and node.top.winfo_exists():
            node._cascade_var.set(False)
            node._run()

    def _cascade_from_selected(self):
        node = self._selected_node()
        if node and node.top.winfo_exists():
            node._cascade_var.set(True)
            node._run()

    # ── lifecycle ─────────────────────────────────────────────────────────

    def close(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def _node_status(node) -> str:
    if node._active_thread and node._active_thread.is_alive():
        return "running"
    if node._last_response.strip():
        return "done"
    if node._prompt_text.get("1.0", "end-1c").strip():
        return "pending"
    return "empty"
