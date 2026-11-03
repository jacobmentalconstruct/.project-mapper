"""Long-running local tools behind actions: shell commands and Ollama inference.

They run on the dispatcher's background lane, stream output through
``context.output`` and stop when the operation is cancelled. Neither touches
project state, and neither is exposed to agents.
"""

from collections import deque
import queue
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from .contracts import ActionError
try:
    from ..tools.inference import OllamaClient, DEFAULT_ENDPOINT
except ImportError:
    from tools.inference import OllamaClient, DEFAULT_ENDPOINT

STREAM_INTERVAL = 0.05     # seconds between streamed chunks
OUTPUT_TAIL = 64 * 1024    # characters of command output kept in the result
KILL_GRACE = 2.0           # seconds to wait for output to end after a kill


class _Batcher:
    """Coalesce small chunks so a fast producer cannot flood the event stream."""

    def __init__(self, context):
        self.context = context
        self.parts = []
        self.last = 0.0

    def add(self, text):
        self.parts.append(text)
        if time.monotonic() - self.last >= STREAM_INTERVAL:
            self.flush()

    def flush(self):
        if self.parts:
            self.context.output(text="".join(self.parts))
            self.parts = []
        self.last = time.monotonic()


def run_command(folder, command, context):
    if sys.platform == "win32":
        args, flags = ["powershell.exe", "-NoLogo", "-NonInteractive", "-Command", command], subprocess.CREATE_NO_WINDOW
    else:
        args, flags = ["/bin/sh", "-c", command], 0
    try:
        process = subprocess.Popen(args, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        raise ActionError("io_error", f"Could not start the command: {exc}") from exc

    lines = queue.Queue()

    def read():
        try:
            for raw in process.stdout:
                lines.put(raw.decode("utf-8", errors="replace").replace("\r\n", "\n"))
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    threading.Thread(target=read, daemon=True, name="command-output").start()
    batch, tail, size, killed_at = _Batcher(context), deque(), 0, None
    while True:
        if context.cancel_event.is_set() and killed_at is None:
            killed_at = time.monotonic()
            try:
                process.kill()
            except OSError:
                pass
        if killed_at is not None and time.monotonic() - killed_at > KILL_GRACE:
            break  # a child process may still hold the pipe open; stop waiting for it
        try:
            item = lines.get(timeout=STREAM_INTERVAL)
        except queue.Empty:
            batch.flush()
            continue
        if item is None:
            break
        batch.add(item)
        tail.append(item)
        size += len(item)
        while size > OUTPUT_TAIL and len(tail) > 1:
            size -= len(tail.popleft())
    batch.flush()
    try:
        code = process.wait(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        code = None
    if context.cancel_event.is_set():
        raise ActionError("cancelled", "Command stopped.")
    output = "".join(tail)
    return {"exit_code": code, "output": output[-OUTPUT_TAIL:], "output_truncated": size > OUTPUT_TAIL}


def _client(endpoint):
    endpoint = endpoint or DEFAULT_ENDPOINT
    if not isinstance(endpoint, str) or urlparse(endpoint).scheme not in ("http", "https"):
        raise ActionError("invalid_input", "The endpoint must be an http(s) URL.")
    return OllamaClient(endpoint)


def list_models(endpoint):
    status, models = _client(endpoint).check_and_list()
    return {"ok": status["ok"], "message": status["message"], "models": models}


def model_info(endpoint, model):
    if not isinstance(model, str) or not model.strip():
        raise ActionError("invalid_input", "Choose a model.")
    return {"model": model, "info": _client(endpoint).model_info(model)}


def generate(endpoint, model, prompt, options, context):
    if not isinstance(model, str) or not model.strip():
        raise ActionError("invalid_input", "Choose a model.")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ActionError("invalid_input", "The prompt is empty.")
    if options is not None and not isinstance(options, dict):
        raise ActionError("invalid_input", "Options must be an object.")
    client = _client(endpoint)
    batch, parts, meta, errors = _Batcher(context), [], {}, []

    def on_token(token):
        parts.append(token)
        batch.add(token)

    client.generate(model, prompt, options or None, on_token, meta.update, errors.append, context.cancel_event)
    batch.flush()
    response = "".join(parts)
    if context.cancel_event.is_set():
        raise ActionError("cancelled", "Inference stopped.", {"response": response})
    if errors:
        raise ActionError("io_error", f"Inference failed: {errors[0]}", {"response": response})
    return {"model": model, "response": response, "meta": meta}
