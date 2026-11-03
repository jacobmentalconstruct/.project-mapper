"""Reusable Ollama inference interface, independent of any UI."""

import json
import threading
import time
import urllib.request
import urllib.error
from typing import Callable

__all__ = ["OllamaClient", "DEFAULT_ENDPOINT"]

DEFAULT_ENDPOINT = "http://localhost:11434"
_REQUEST_TIMEOUT = 10


class OllamaClient:
    """Thin synchronous/streaming interface to a local Ollama server.

    All network I/O runs on the caller's thread.  The UI drives everything
    from a background thread and posts results back to the GUI queue itself.
    """

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT):
        self.endpoint = endpoint.rstrip("/")

    # ── connectivity ─────────────────────────────────────────────────────────

    def check_status(self) -> dict:
        """Return {"ok": bool, "message": str}."""
        status, _ = self.check_and_list()
        return status

    def check_and_list(self) -> tuple[dict, list[str]]:
        """Single /api/tags round-trip returning (status, model_names)."""
        try:
            req = urllib.request.Request(self.endpoint + "/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read())
            models = sorted(m["name"] for m in data.get("models", []))
            return {"ok": True, "message": "Connected"}, models
        except urllib.error.URLError as exc:
            return {"ok": False, "message": str(exc.reason)}, []
        except Exception as exc:
            return {"ok": False, "message": str(exc)}, []

    # ── model listing ─────────────────────────────────────────────────────────

    def list_models(self) -> list[str]:
        """Return sorted model name strings, or [] on failure."""
        _, models = self.check_and_list()
        return models

    def model_info(self, model: str) -> dict:
        """Return raw model metadata dict, or {} on failure."""
        try:
            body = json.dumps({"model": model, "verbose": False}).encode()
            req = urllib.request.Request(
                self.endpoint + "/api/show", data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read())
        except Exception:
            return {}

    # ── inference ─────────────────────────────────────────────────────────────

    def generate(
        self,
        model: str,
        prompt: str,
        options: dict | None,
        on_token: Callable[[str], None],
        on_done: Callable[[dict], None],
        on_error: Callable[[str], None],
        stop_event: threading.Event,
    ) -> None:
        """Stream a completion.  Call from a background thread.

        Calls *on_token(chunk)* for each token, then *on_done(meta)* with timing
        and token counts, or *on_error(message)* on failure.
        """
        body = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        if options:
            body["options"] = options
        encoded = json.dumps(body).encode()
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(
                self.endpoint + "/api/generate", data=encoded, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw_line in resp:
                    if stop_event.is_set():
                        break
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get("response", "")
                    if token:
                        on_token(token)
                    if chunk.get("done"):
                        elapsed = time.perf_counter() - t0
                        meta = {
                            "elapsed_s": round(elapsed, 2),
                            "prompt_tokens": chunk.get("prompt_eval_count"),
                            "response_tokens": chunk.get("eval_count"),
                            "context_length": chunk.get("context_length"),
                        }
                        on_done(meta)
                        return
        except urllib.error.URLError as exc:
            on_error(str(exc.reason))
        except Exception as exc:
            on_error(str(exc))
        if stop_event.is_set():
            on_done({"elapsed_s": round(time.perf_counter() - t0, 2),
                     "prompt_tokens": None, "response_tokens": None,
                     "context_length": None, "stopped": True})
