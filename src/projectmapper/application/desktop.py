"""Thin desktop adapter; all file rules stay in headless action handlers."""
import queue
import threading
from pathlib import Path
from .contracts import Request, ActionError, TERMINAL
from .errors import describe
try:
    from ..tools.patcher import PatchError
except ImportError:
    from tools.patcher import PatchError

# A wait longer than this keeps the windows painting instead of freezing them.
PUMP_AFTER = 0.05


class Session:
    def __init__(self, app, path):
        self.app = app
        self.load(path)

    def load(self, path):
        data = self.app.action("text.open", {"path": str(path)})
        self.path = Path(data["path"])
        self.source = data["text"]
        self.sha256 = data["sha256"]
        self.bom = data["bom"]
        self.original_bytes = (b"\xef\xbb\xbf" if self.bom else b"") + self.source.encode("utf-8")

    def save(self, text, suffix=None, backup=False):
        data = self.app.action("text.save", {"path": str(self.path), "text": text,
            "sha256": self.sha256, "suffix": suffix, "backup": backup})
        self.load(data["path"])
        return self.path


def _toplevels(widget):
    found = [widget] if widget.winfo_class() in ("Tk", "Toplevel") else []
    for child in widget.winfo_children():
        found.extend(_toplevels(child))
    return found


def _busy_name(window):
    return "._Busy" if window._w == "." else f"{window._w}._Busy"


def _busy_hold(root):
    """Block input on every window; returns an undo callable. Painting continues."""
    held = []
    focus = root.tk.call("focus")
    for window in _toplevels(root):
        try:
            root.tk.call("tk", "busy", "hold", window._w)
            # Keys go to the focus, not the pointer: park focus on a shield with no bindings.
            root.tk.call("bindtags", _busy_name(window), _busy_name(window))
            held.append(window)
        except Exception:
            pass
    if held:
        try:
            root.tk.call("focus", _busy_name(held[0]))
        except Exception:
            pass

    def release():
        for window in held:
            try:
                root.tk.call("tk", "busy", "forget", window._w)
            except Exception:
                pass
        if focus:
            try:
                root.tk.call("focus", focus)
            except Exception:
                pass
    return release


def _wait(app, operation):
    """Wait for an operation without freezing the desktop (CR01).

    Fast operations return as before. A slower one keeps the windows painting and
    responsive to the window manager while input is held, so nothing can be clicked
    twice. The GUI queue is paused meanwhile, so action events reach the UI in the
    same order as before: after the call returns.
    """
    dispatcher = app.controller.dispatcher
    result = dispatcher.wait(operation, PUMP_AFTER)
    if dispatcher.settled(operation):
        return result
    root = getattr(app, "root", None)
    if root is None or threading.current_thread() is not getattr(app, "_tk_thread", threading.main_thread()):
        return dispatcher.wait(operation, None)
    outer = not getattr(app, "_pumping", 0)
    app._pumping = getattr(app, "_pumping", 0) + 1
    release = _busy_hold(root) if outer else None
    try:
        while not dispatcher.settled(operation):
            try:
                root.update()
            except Exception:  # the window went away; finish without it
                return dispatcher.wait(operation, None)
            dispatcher.wait(operation, PUMP_AFTER)
        return dispatcher.get(operation)
    finally:
        app._pumping -= 1
        if release is not None:
            release()


def perform(app, name, payload=None, parent=None, approval_guard=None, approval_fn=None):
    """Only composition-root UI code receives the trusted approval capability.

    approval_fn: optional callable(summary) -> bool.  When provided it replaces the
    inline messagebox so callers on a worker thread can show the dialog safely on the
    Tk thread (see ProjectMapperApp._show_approval_and_wait).
    """
    from tkinter import messagebox
    try:
        operation = app.controller.dispatcher.submit(Request(name, payload or {}))
        if not hasattr(app, "desktop_operations"):
            app.desktop_operations = set()
        app.desktop_operations.add(operation)
        app.action_operations.add(operation)
        try:
            result = _wait(app, operation)
            if result.status == "awaiting_approval":
                summary = result.data["summary"]
                if approval_fn is not None:
                    approved = approval_fn(summary)
                else:
                    approved = messagebox.askyesno(summary["title"], summary["message"],
                                                  parent=parent or app.root, default="no")
                if approved and approval_guard and not approval_guard():
                    app.controller.dispatcher.cancel(operation)
                    raise PatchError("Inputs changed while awaiting approval. Review again.")
                app.approve_action(operation, approved)
                result = _wait(app, operation)
            if result.status != "succeeded":
                raise PatchError(describe(result.error or {"code": result.status}))
            return result.data
        finally:
            app.action_operations.discard(operation)
    except ActionError as exc:
        raise PatchError(describe({"code": exc.code, "message": str(exc)})) from exc


class Stream:
    """Run a background action without blocking Tk.

    ``on_output(payload)`` receives each streamed chunk and ``on_done(result)`` the final
    Result, both on the Tk thread of ``widget``. ``cancel()`` stops the operation;
    ``detach()`` also stops delivery (use it when the window closes).
    """
    POLL_MS = 40

    def __init__(self, app, name, payload, *, widget, on_output, on_done):
        self._dispatcher = app.controller.dispatcher
        self._widget = widget
        self._on_output, self._on_done = on_output, on_done
        self._queue = queue.Queue()
        self._lock = threading.RLock()
        self._after = None
        self.operation = None
        self._unsubscribe = self._dispatcher.subscribe(self._observe)
        try:
            with self._lock:  # events cannot be matched until the operation id is known
                self.operation = self._dispatcher.submit(Request(name, payload))
        except Exception:
            self._unsubscribe()
            raise
        self._poll()

    def _observe(self, event):
        with self._lock:
            if event.operation_id != self.operation:
                return
        if event.type == "output":
            self._queue.put(("output", event.payload))
        elif event.type in TERMINAL:
            self._queue.put(("done", None))

    def _poll(self):
        self._after = None
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "output":
                self._on_output(payload)
            else:
                self._unsubscribe()
                self._on_done(self._dispatcher.get(self.operation))
                return
        try:
            self._after = self._widget.after(self.POLL_MS, self._poll)
        except Exception:  # the window was destroyed
            self.detach()

    def cancel(self):
        try:
            self._dispatcher.cancel(self.operation)
        except (ActionError, KeyError):
            pass

    def detach(self):
        self.cancel()
        self._unsubscribe()
        if self._after is not None:
            try:
                self._widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
