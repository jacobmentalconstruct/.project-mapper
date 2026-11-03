"""Chain Navigator — panel over an InferenceWindow chain."""

import tkinter as tk
from tkinter import ttk

if __package__:
    from .ui_base import ToolWindowMixin
else:
    from ui_base import ToolWindowMixin

__all__ = ["ChainNavigatorWindow"]

_POLL_MS = 350
_END_LABEL = "— End —"


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
        self.configure_tool_window("Chain Navigator", "360x500", (300, 320))
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(1, weight=1)

        self._nodes: list = []
        self._last_rows: list = []
        self._route_var = tk.StringVar(value=_END_LABEL)
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

        # routing row
        route_row = self.frame(self.top)
        route_row.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 2))
        self.label(route_row, text="Selected routes to →", panel=True,
                   font=("Arial", 9)).pack(side=tk.LEFT, padx=4)
        self._route_menu = ttk.Combobox(
            route_row, textvariable=self._route_var,
            state="readonly", width=18, font=("Arial", 9),
        )
        self._route_menu.pack(side=tk.LEFT, padx=4)
        self._route_menu.bind("<<ComboboxSelected>>", self._on_route_change)

        # actions
        act = self.frame(self.top)
        act.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))
        self._run_btn = self.button(act, "Run Step", self._run_selected,
                                    "success", tooltip_id="nav.run_step")
        self._run_btn.pack(side=tk.LEFT, padx=4, pady=4)
        self._cascade_btn = self.button(act, "Run → End", self._cascade_from_selected,
                                        tooltip_id="nav.run_to_end")
        self._cascade_btn.pack(side=tk.LEFT, padx=2, pady=4)
        self._stop_btn = self.button(act, "Stop", self._stop_chain, "error",
                                     tooltip_id="nav.stop")
        self._stop_btn.pack(side=tk.RIGHT, padx=4, pady=4)

        # status bar
        self._status_var = tk.StringVar(value="")
        tk.Label(
            self.top, textvariable=self._status_var,
            bg=c["status_bg"], fg=c["status_text"],
            anchor="w", padx=8, font=("Arial", 9),
        ).grid(row=4, column=0, sticky="ew")

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

        c = self.colors
        status_colors = {
            "running": c.get("success", c["text"]),
            "done":    c.get("text",    c["text"]),
            "pending": c.get("muted_text", c["text"]),
            "empty":   c.get("muted_text", c["text"]),
        }

        rows = []
        for node in nodes:
            status = _node_status(node)
            prompt = node._prompt_text.get("1.0", "end-1c").strip().replace("\n", " ")
            preview = (prompt[:34] + "…") if len(prompt) > 34 else (prompt or "(no prompt)")
            rows.append((node._step, status, preview))

        if rows != self._last_rows:
            self._last_rows = rows
            self._lb.delete(0, tk.END)
            for i, (step, status, preview) in enumerate(rows):
                self._lb.insert(tk.END, f"  {step}  [{status:7s}]  {preview}")
                self._lb.itemconfigure(i, fg=status_colors.get(status, c["text"]))

        if prev_idx is not None and prev_idx < len(nodes):
            self._lb.selection_set(prev_idx)

        # Update route dropdown choices.
        step_labels = [f"Step {n._step}" for n in nodes] + [_END_LABEL]
        if list(self._route_menu["values"]) != step_labels:
            self._route_menu["values"] = step_labels
        self._sync_route_display()

        chain_busy = self._root._chain_busy
        self.set_button_enabled(self._run_btn, not chain_busy, "success")
        self.set_button_enabled(self._cascade_btn, not chain_busy, "secondary")
        self.set_button_enabled(self._stop_btn, chain_busy, "error")

        running_count = sum(1 for n in nodes if n._stream is not None)
        done = sum(1 for n in nodes if n._last_response.strip())
        total = len(nodes)
        if running_count:
            self._status_var.set(f"Running…  {done}/{total} done")
        else:
            self._status_var.set(f"{done}/{total} steps done")

    def _sync_route_display(self):
        """Update route dropdown to reflect the selected node's current target."""
        node = self._selected_node()
        if node is None:
            return
        target = node._chain_target
        if target is None or not target.top.winfo_exists():
            self._route_var.set(_END_LABEL)
        else:
            self._route_var.set(f"Step {target._step}")

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
        self._sync_route_display()

    # ── routing ───────────────────────────────────────────────────────────

    def _on_route_change(self, _ev=None):
        node = self._selected_node()
        if node is None:
            return
        label = self._route_var.get()
        if label == _END_LABEL:
            node._chain_target = None
            return
        # Find the target node by step number.
        try:
            step_num = int(label.split()[-1])
        except (ValueError, IndexError):
            return
        for n in self._nodes:
            if n._step == step_num:
                node._chain_target = n
                return

    # ── actions ───────────────────────────────────────────────────────────

    def _add_step(self):
        """Append a new step to the end of the chain."""
        nodes = self._root._all_chain_nodes()
        if not nodes:
            return
        last = nodes[-1]
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

    def _stop_chain(self):
        """Signal the running cascade to stop after the current step."""
        root = self._root
        root._chain_stop = True
        # Also cancel any active stream on a running node.
        for node in self._root._all_chain_nodes():
            if node._stream is not None:
                node._stream.cancel()
                break

    # ── lifecycle ─────────────────────────────────────────────────────────

    def close(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def _node_status(node) -> str:
    if node._stream is not None:
        return "running"
    if node._last_response.strip():
        return "done"
    if node._prompt_text.get("1.0", "end-1c").strip():
        return "pending"
    return "empty"
