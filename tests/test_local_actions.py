"""Background lane, streamed output, command/inference/spine actions and the CR01 wait."""

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import temporary_directory, tk_root
from projectmapper.adapters.session import AdapterSession
from projectmapper.application import local_tools
from projectmapper.application.contracts import Request
from projectmapper.application.controller import create_application
from projectmapper.application.desktop import Stream, perform
from projectmapper.application.dispatcher import Dispatcher
from projectmapper.core.settings import Settings
from projectmapper.tools.spine import Spine

SLEEP = "Start-Sleep -Seconds 30" if sys.platform == "win32" else "sleep 30"


def collect(dispatcher):
    events = []
    dispatcher.subscribe(events.append)
    return events


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = Dispatcher()
        self.addCleanup(self.dispatcher.close)
        self.release = threading.Event()

    def test_background_action_does_not_hold_up_the_serial_worker(self):
        self.dispatcher.register("slow.network", lambda p, c: self.release.wait(5) and {}, background=True)
        self.dispatcher.register("quick.state", lambda p, c: {"ok": True})
        slow = self.dispatcher.submit(Request("slow.network"))
        quick = self.dispatcher.execute(Request("quick.state"), timeout=2)
        self.assertEqual(quick.status, "succeeded")
        self.assertFalse(self.dispatcher.settled(slow))
        self.release.set()
        self.assertEqual(self.dispatcher.wait(slow, 5).status, "succeeded")

    def test_output_reaches_listeners_but_not_replay_or_history(self):
        controller, _ = create_application(Path(temporary_directory(self).name))
        self.addCleanup(controller.close)
        dispatcher = controller.dispatcher
        dispatcher.register("talk.stream", lambda p, c: c.output(text="hi") or {}, background=True)
        live = collect(dispatcher)
        result = dispatcher.execute(Request("talk.stream"), timeout=5)
        self.assertEqual(result.status, "succeeded")
        self.assertIn("output", [e.type for e in live])
        self.assertNotIn("output", [e.type for e in dispatcher.events()["events"]])
        record = controller.history.query(category="talk")[0]
        self.assertEqual(record["progress_count"], 0)
        self.assertEqual(record["status"], "succeeded")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(temporary_directory(self).name).resolve()
        self.controller, _ = create_application(self.root)
        self.addCleanup(self.controller.close)

    def test_runs_in_the_folder_and_streams_output(self):
        events = collect(self.controller.dispatcher)
        result = self.controller.execute("command.run", {"folder": str(self.root), "command": "echo hello"}, timeout=60)
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(result.data["exit_code"], 0)
        self.assertIn("hello", result.data["output"])
        streamed = "".join(e.payload["text"] for e in events if e.type == "output")
        self.assertIn("hello", streamed)

    def test_cancel_stops_a_long_command(self):
        dispatcher = self.controller.dispatcher
        operation = dispatcher.submit(Request("command.run", {"folder": str(self.root), "command": SLEEP}))
        time.sleep(0.5)
        started = time.monotonic()
        dispatcher.cancel(operation)
        result = dispatcher.wait(operation, 15)
        self.assertEqual(result.status, "cancelled")
        self.assertLess(time.monotonic() - started, 10)

    def test_folder_must_be_inside_the_project(self):
        outside = Path(temporary_directory(self).name)
        result = self.controller.execute("command.run", {"folder": str(outside), "command": "echo x"})
        self.assertEqual(result.error["code"], "unsafe_path")

    def test_agents_cannot_run_commands_or_inference(self):
        session = AdapterSession(self.root, "mcp")
        self.addCleanup(session.close)
        for action in ("command.run", "inference.generate", "inference.models"):
            outcome = session.call(action, {"folder": str(self.root), "command": "echo x"})
            self.assertEqual(outcome["status"], "failed")
            self.assertIn("not available to agents", outcome["error"]["message"])


class FakeClient:
    tokens = ("Hel", "lo")
    delay = 0
    error = None
    seen = []

    def __init__(self, endpoint):
        self.endpoint = endpoint

    def check_and_list(self):
        return {"ok": True, "message": "Connected"}, ["llama3"]

    def model_info(self, model):
        return {"details": {"family": "llama"}}

    def generate(self, model, prompt, options, on_token, on_done, on_error, stop_event):
        FakeClient.seen.append((self.endpoint, model, prompt, options))
        for token in self.tokens:
            if stop_event.wait(self.delay):
                break
            on_token(token)
        if self.error:
            on_error(self.error)
        else:
            on_done({"elapsed_s": 0.1, "response_tokens": len(self.tokens)})


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(temporary_directory(self).name)
        self.controller, _ = create_application(self.root)
        self.addCleanup(self.controller.close)
        patcher = patch.object(local_tools, "OllamaClient", FakeClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeClient.tokens, FakeClient.delay, FakeClient.error, FakeClient.seen = ("Hel", "lo"), 0, None, []

    def run_action(self, name, payload):
        return self.controller.execute(name, payload, timeout=10)

    def test_generate_sends_the_request_and_returns_the_response(self):
        events = collect(self.controller.dispatcher)
        result = self.run_action("inference.generate", {"model": "llama3", "prompt": "hi",
                                                       "options": {"temperature": 0.2}})
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data["response"], "Hello")
        self.assertEqual(FakeClient.seen, [(local_tools.DEFAULT_ENDPOINT, "llama3", "hi", {"temperature": 0.2})])
        self.assertEqual("".join(e.payload["text"] for e in events if e.type == "output"), "Hello")
        self.assertEqual(self.controller.history.query(category="inference")[0]["status"], "succeeded")

    def test_failure_and_bad_input_are_reported(self):
        FakeClient.error = "connection refused"
        failed = self.run_action("inference.generate", {"model": "m", "prompt": "p"})
        self.assertEqual(failed.error["code"], "io_error")
        self.assertIn("connection refused", failed.error["message"])
        empty = self.run_action("inference.generate", {"model": "m", "prompt": "  "})
        self.assertEqual(empty.error["code"], "invalid_input")
        bad = self.run_action("inference.models", {"endpoint": "file:///etc/passwd"})
        self.assertEqual(bad.error["code"], "invalid_input")

    def test_cancel_returns_cancelled(self):
        FakeClient.tokens, FakeClient.delay = ("slow",) * 200, 0.05
        dispatcher = self.controller.dispatcher
        operation = dispatcher.submit(Request("inference.generate", {"model": "m", "prompt": "p"}))
        time.sleep(0.2)
        dispatcher.cancel(operation)
        self.assertEqual(dispatcher.wait(operation, 5).status, "cancelled")

    def test_models_and_info(self):
        self.assertEqual(self.run_action("inference.models", {}).data["models"], ["llama3"])
        self.assertEqual(self.run_action("inference.model_info", {"model": "llama3"}).data["info"]["details"],
                         {"family": "llama"})


NOTE = {"id": "note", "version": 1, "required": ["body"],
        "properties": {"body": {"type": "string"}, "level": {"type": "string", "enum": ["low", "high"]}}}


class SpineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(temporary_directory(self).name).resolve()
        self.spine_path = self.root / "_spine" / "journal.jsonl"
        spine = Spine.create(self.spine_path)
        spine.register_schema(NOTE)
        for body in ("one", "two", "three"):
            spine.append_entry("note@1", {"body": body})
        (self.root / "data.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
        self.controller, _ = create_application(self.root)
        self.addCleanup(self.controller.close)

    def run_action(self, name, payload):
        return self.controller.execute(name, payload, timeout=10)

    def test_list_finds_spines_and_skips_other_jsonl(self):
        found = self.run_action("spine.list", {}).data
        self.assertEqual([s["path"] for s in found["spines"]], ["_spine/journal.jsonl"])

    def test_read_pages_entries_with_their_index(self):
        data = self.run_action("spine.read", {"path": "_spine/journal.jsonl", "start": 1, "limit": 1}).data
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["entries"][0]["index"], 1)
        self.assertEqual(data["entries"][0]["data"], {"body": "two"})
        self.assertEqual(data["next"], 2)
        self.assertIn("note@1", data["schemas"])

    def test_append_checks_the_schema(self):
        ok = self.run_action("spine.append", {"path": str(self.spine_path), "schema": "note@1",
                                              "data": {"body": "four", "level": "high"}})
        self.assertEqual(ok.status, "succeeded")
        self.assertEqual(ok.data["index"], 3)
        for data, needle in (({"body": 4}, "must be string"), ({"level": "low"}, "Required"),
                             ({"body": "x", "extra": 1}, "not in the schema"),
                             ({"body": "x", "level": "mid"}, "one of")):
            bad = self.run_action("spine.append", {"path": str(self.spine_path), "schema": "note@1", "data": data})
            self.assertEqual(bad.error["code"], "invalid_input")
            self.assertIn(needle, bad.error["message"])
        missing = self.run_action("spine.append", {"path": str(self.spine_path), "schema": "nope@1", "data": {}})
        self.assertEqual(missing.error["code"], "invalid_input")
        self.assertEqual(len(Spine.open(self.spine_path).read_entries()), 4)


class AgentSpineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(temporary_directory(self).name).resolve()
        self.paths = {}
        for rel in ("_spine/journal.jsonl", ".project-mapper/spines/memory.jsonl",
                    ".project-mapper/examples/sample.jsonl", "_projectmapper/out.jsonl"):
            spine = Spine.create(self.root / rel)
            spine.register_schema(NOTE)
            self.paths[rel] = self.root / rel

    def session(self, approver=None, ask=False):
        session = AdapterSession(self.root, "mcp", approver=approver,
                                 settings=Settings(ask_before_structural_writes=ask))
        self.addCleanup(session.close)
        return session

    def append(self, session, rel):
        return session.call("spine.append", {"path": rel, "schema": "note@1", "data": {"body": "agent"}})

    def test_default_appends_without_a_prompt_inside_allowed_places(self):
        session = self.session()
        for rel in ("_spine/journal.jsonl", ".project-mapper/spines/memory.jsonl"):
            self.assertEqual(self.append(session, rel)["status"], "succeeded", rel)
        for rel in (".project-mapper/examples/sample.jsonl", "_projectmapper/out.jsonl"):
            self.assertEqual(self.append(session, rel)["error"]["code"], "unsafe_path", rel)
            self.assertEqual(Spine.open(self.paths[rel]).read_entries(), [])
        self.assertEqual(session.call("spine.read", {"path": "../x.jsonl"})["error"]["code"], "unsafe_path")

    def test_owner_setting_asks_and_denial_writes_nothing(self):
        shown = []
        session = self.session(approver=lambda request: shown.append(request) or False, ask=True)
        outcome = self.append(session, "_spine/journal.jsonl")
        self.assertEqual(outcome["error"]["code"], "approval_denied")
        self.assertEqual(shown[0].title, "Append spine entry?")
        self.assertIn('"body": "agent"', shown[0].diff)
        self.assertEqual(Spine.open(self.paths["_spine/journal.jsonl"]).read_entries(), [])
        no_window = self.session(ask=True)
        self.assertEqual(self.append(no_window, "_spine/journal.jsonl")["status"], "approval_required")


class DesktopWaitTests(unittest.TestCase):
    """CR01: a slow synchronous action keeps Tk alive; a fast one behaves as before."""

    def setUp(self):
        self.root = tk_root(self)
        self.dispatcher = Dispatcher()
        self.addCleanup(self.dispatcher.close)
        self.dispatcher.register("test.slow", lambda p, c: time.sleep(0.4) or {"done": True})
        self.dispatcher.register("test.fast", lambda p, c: {"done": True})
        self.app = SimpleNamespace(root=self.root, controller=SimpleNamespace(dispatcher=self.dispatcher),
                                   action_operations=set(), _tk_thread=threading.current_thread())

    def test_slow_action_keeps_timers_running_and_holds_input(self):
        ticks, busy = [], []
        self.root.after(100, lambda: (ticks.append(1),
                                      busy.append(self.root.tk.call("tk", "busy", "status", "."))))
        self.assertEqual(perform(self.app, "test.slow"), {"done": True})
        self.assertEqual(ticks, [1])
        self.assertEqual(busy, [1])
        self.assertEqual(self.root.tk.call("tk", "busy", "status", "."), 0)
        self.assertEqual(self.app._pumping, 0)

    def test_fast_action_does_not_pump(self):
        ticks = []
        self.root.after(0, lambda: ticks.append(1))
        self.assertEqual(perform(self.app, "test.fast"), {"done": True})
        self.assertEqual(ticks, [])

    def test_stream_delivers_output_then_the_result(self):
        self.dispatcher.register("test.talk", lambda p, c: c.output(text="a") or c.output(text="b") or {"n": 2},
                                 background=True)
        chunks, done = [], []
        Stream(self.app, "test.talk", {}, widget=self.root, on_output=lambda p: chunks.append(p["text"]),
               on_done=done.append)
        deadline = time.monotonic() + 5
        while not done and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(chunks, ["a", "b"])
        self.assertEqual(done[0].data, {"n": 2})


if __name__ == "__main__":
    unittest.main()
