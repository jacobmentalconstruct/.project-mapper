"""Isolated fixtures with cleanup registered before test setup continues."""

import gc
import re
import tempfile
import time
import tkinter as tk


def temporary_directory(case):
    fixture = tempfile.TemporaryDirectory(prefix="projectmapper-test-")
    case.addCleanup(fixture.cleanup)
    return fixture


def fire_binding(widget, sequence):
    """Invoke a widget's registered Tk binding without OS focus or event delivery."""
    script = widget.bind(sequence)
    funcid = re.search(r"\[(\S+) %#", script).group(1)
    fields = dict.fromkeys(widget._subst_format, "0")
    fields.update({"%A": "", "%T": "2", "%K": sequence.strip("<>"), "%W": str(widget)})
    return widget.tk.call(funcid, *(fields[name] for name in widget._subst_format))


class PumpMixin:
    """Drain app.gui_queue until a threaded task finishes, then flush once more."""
    def pump(self, task=None, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            while not self.app.gui_queue.empty():
                self.app.gui_queue.get_nowait()()
            self.root.update()
            if task is None or task not in self.app.running_tasks:
                break
            time.sleep(0.02)
        while not self.app.gui_queue.empty():
            self.app.gui_queue.get_nowait()()


def tk_root(case):
    gc.collect()
    root = tk.Tk()

    def close():
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()
        for name in ("window", "popup", "app", "root"):
            if hasattr(case, name):
                setattr(case, name, None)
        gc.collect()

    case.addCleanup(close)
    return root
