"""T05 — Full chain verification tests.

Covers the complete Resolve→Assemble→Freeze→Infer→Record cycle across
a two-step chain.  All fixtures are disposable; no production spines
are read or written.

Tests:
- Different spine selections per step, correct inherited prior response
- Successful rerun: history grows, current result updates, descendants cleared
- Cascade from step 1: each step gets fresh frozen request + result
- Source unavailability after run: frozen records remain inspectable
- Failure before submission: prior current result preserved
- Failed inference rerun: prior result preserved, descendants intact
- No-spine path: ordinary inference unchanged
"""

import threading
import unittest
from pathlib import Path

from tests.support import temporary_directory
from projectmapper.tools.spine import Spine
from projectmapper.tools.inference_spine import resolve_entries, SpineContextError
from projectmapper.tools.inference_assembler import (
    assemble_prompt, freeze_request, AssemblyError, InferenceRequest,
)

# ── spine fixture helpers ─────────────────────────────────────────────────────

_SCHEMA = {"id": "note", "version": 1,
           "properties": {"body": {"type": "string", "label": "Body"}}}


def _make_spine(folder: Path, name: str, entries: list[str]) -> Path:
    spine = Spine.create(folder / f"{name}.jsonl")
    spine.register_schema(_SCHEMA)
    for body in entries:
        spine.append_entry("note@1", {"body": body})
    return spine.path


# ── fake inference backend ────────────────────────────────────────────────────

class FakeBackend:
    """Captures what the run path would send; never hits the network."""

    def __init__(self, response: str = "ok", *, fail: bool = False, fail_msg: str = "oops"):
        self._response = response
        self._fail = fail
        self._fail_msg = fail_msg
        self.calls: list[dict] = []

    def generate(self, model, prompt, options, on_token, on_done, on_error, stop_event):
        self.calls.append({"model": model, "prompt": prompt, "options": options})
        if self._fail:
            on_error(self._fail_msg)
        else:
            on_token(self._response)
            on_done({"elapsed_s": 0.1, "prompt_tokens": 10,
                     "response_tokens": 5, "context_length": None})


# ── simulated step (no Tk) ────────────────────────────────────────────────────

class StepSim:
    """Minimal simulation of InferenceWindow's run-path logic for unit testing.

    Does not require Tk.  Exercises resolve→assemble→freeze→execute→commit
    with injection of a fake backend.
    """

    def __init__(self, model: str = "llama3",
                 spine_path: str = "", indices: list[int] | None = None,
                 chain_parent: "StepSim | None" = None,
                 chain_order: str = "before",
                 backend: FakeBackend | None = None):
        self.model = model
        self.spine_path = spine_path
        self.indices: list[int] = indices or []
        self.chain_parent = chain_parent
        self.chain_order = chain_order
        self._backend = backend or FakeBackend()

        self._last_response: str = ""
        self._pending_prior: str = ""
        self._frozen_request: InferenceRequest | None = None
        self._execution_records: list[dict] = []
        self._chain_child: "StepSim | None" = None

    def run(self, user_prompt: str) -> bool:
        """Execute one inference step synchronously.  Returns True on success."""
        # Resolve spine
        entries = []
        if self.spine_path and self.indices:
            entries = resolve_entries(self.spine_path, self.indices)

        # Chain prior
        prior = ""
        if self.chain_parent is not None:
            prior = self.chain_parent._last_response.strip()
            if not prior:
                return False  # parent not run yet

        # Assemble + freeze
        try:
            assembled = assemble_prompt(
                entries, prior, user_prompt,
                prior_first=(self.chain_order == "before"),
            )
            req = freeze_request(self.model, assembled, None)
        except AssemblyError:
            return False

        self._pending_prior = self._last_response
        self._frozen_request = req

        # Execute via fake backend
        result: list[str] = []
        error: list[str] = []

        def on_token(tok): result.append(tok)
        def on_done(meta): pass
        def on_error(msg): error.append(msg)

        self._backend.generate(
            req.model, req.prompt, req.options,
            on_token, on_done, on_error, threading.Event(),
        )

        if error:
            # Failure — preserve prior result
            self._execution_records.append({
                "status": "error", "error": error[0],
                "prompt": req.prompt, "model": req.model,
            })
            self._last_response = self._pending_prior
            return False

        # Success — commit and invalidate descendants
        response = "".join(result)
        self._last_response = response
        self._execution_records.append({
            "status": "success", "response": response,
            "prompt": req.prompt, "model": req.model,
        })
        self._invalidate_descendants()
        return True

    def _invalidate_descendants(self):
        node = self._chain_child
        while node is not None:
            node._last_response = ""
            node = node._chain_child


# ── T05 tests ─────────────────────────────────────────────────────────────────

class T05ChainBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(self)
        folder = Path(self.tmp.name)
        self.spine1 = _make_spine(folder, "s1", ["context for step 1"])
        self.spine2 = _make_spine(folder, "s2", ["context for step 2"])

    def test_different_spine_selections_per_step(self):
        """Each step uses its own spine entries."""
        b1, b2 = FakeBackend("reply-1"), FakeBackend("reply-2")
        step1 = StepSim(spine_path=str(self.spine1), indices=[0], backend=b1)
        step2 = StepSim(chain_parent=step1, spine_path=str(self.spine2), indices=[0], backend=b2)
        step1._chain_child = step2

        step1.run("prompt1")
        step2.run("prompt2")

        # Each step's frozen request includes its own spine context
        self.assertIn("context for step 1", b1.calls[0]["prompt"])
        self.assertNotIn("context for step 2", b1.calls[0]["prompt"])
        self.assertIn("context for step 2", b2.calls[0]["prompt"])
        self.assertNotIn("context for step 1", b2.calls[0]["prompt"])

    def test_step2_receives_step1_result(self):
        """Step 2 inherits step 1's current result."""
        b1, b2 = FakeBackend("step1-output"), FakeBackend("step2-output")
        step1 = StepSim(backend=b1)
        step2 = StepSim(chain_parent=step1, chain_order="before", backend=b2)
        step1._chain_child = step2

        step1.run("hello")
        step2.run("follow-up")

        self.assertIn("step1-output", b2.calls[0]["prompt"])

    def test_successful_rerun_appends_history(self):
        """Rerunning a step keeps prior result in history and updates current."""
        b = FakeBackend("first")
        step = StepSim(backend=b)
        step.run("q")
        self.assertEqual(step._last_response, "first")
        self.assertEqual(len(step._execution_records), 1)

        b._response = "second"
        step.run("q")
        self.assertEqual(step._last_response, "second")
        self.assertEqual(len(step._execution_records), 2)
        self.assertEqual(step._execution_records[0]["response"], "first")
        self.assertEqual(step._execution_records[1]["response"], "second")

    def test_successful_rerun_invalidates_descendants(self):
        """After a successful upstream rerun, downstream current result is cleared."""
        b1, b2 = FakeBackend("r1"), FakeBackend("r2")
        step1 = StepSim(backend=b1)
        step2 = StepSim(chain_parent=step1, backend=b2)
        step1._chain_child = step2

        step1.run("q")
        step2.run("q")
        self.assertEqual(step2._last_response, "r2")

        b1._response = "r1-new"
        step1.run("q")
        self.assertEqual(step1._last_response, "r1-new")
        self.assertEqual(step2._last_response, "")  # invalidated

    def test_failed_rerun_preserves_prior_result(self):
        """A failed rerun must not replace a valid current result."""
        good_b = FakeBackend("good-result")
        step = StepSim(backend=good_b)
        step.run("q")
        self.assertEqual(step._last_response, "good-result")

        fail_b = FakeBackend(fail=True)
        step._backend = fail_b
        ok = step.run("q")
        self.assertFalse(ok)
        self.assertEqual(step._last_response, "good-result")  # preserved

    def test_failed_rerun_preserves_valid_descendants(self):
        """When step 1 rerun fails, step 2's valid result is NOT cleared."""
        b1, b2 = FakeBackend("r1"), FakeBackend("r2")
        step1 = StepSim(backend=b1)
        step2 = StepSim(chain_parent=step1, backend=b2)
        step1._chain_child = step2

        step1.run("q")
        step2.run("q")
        self.assertEqual(step2._last_response, "r2")

        b1._fail = True
        step1.run("q")
        self.assertEqual(step2._last_response, "r2")  # still valid

    def test_failure_before_submission_leaves_result_intact(self):
        """Assembly failure must not touch the current result."""
        b = FakeBackend("existing")
        step = StepSim(backend=b)
        step.run("q")
        self.assertEqual(step._last_response, "existing")

        # Force assembly failure by using an empty prompt with no context
        ok = step.run("")  # empty prompt → AssemblyError
        self.assertFalse(ok)
        self.assertEqual(step._last_response, "existing")

    def test_cascade_each_step_gets_fresh_frozen_request(self):
        """Running a cascade produces distinct frozen requests per step."""
        b1, b2 = FakeBackend("out1"), FakeBackend("out2")
        step1 = StepSim(backend=b1)
        step2 = StepSim(chain_parent=step1, backend=b2)
        step1._chain_child = step2

        step1.run("q1")
        step2.run("q2")

        self.assertEqual(len(b1.calls), 1)
        self.assertEqual(len(b2.calls), 1)
        self.assertNotEqual(b1.calls[0]["prompt"], b2.calls[0]["prompt"])

    def test_frozen_record_inspectable_after_source_unavailable(self):
        """Frozen records remain readable even after the spine is deleted."""
        spine_path = _make_spine(Path(self.tmp.name), "ephemeral", ["ephemeral data"])
        b = FakeBackend("result")
        step = StepSim(spine_path=str(spine_path), indices=[0], backend=b)
        step.run("q")

        # Delete the spine
        spine_path.unlink()

        # Frozen request is still in the execution record
        self.assertEqual(len(step._execution_records), 1)
        rec = step._execution_records[0]
        self.assertIn("ephemeral data", rec["prompt"])
        self.assertEqual(rec["status"], "success")

    def test_no_spine_path_executes_unchanged(self):
        """Steps without a spine work exactly as before."""
        b = FakeBackend("plain result")
        step = StepSim(backend=b)
        ok = step.run("simple question")
        self.assertTrue(ok)
        self.assertEqual(step._last_response, "plain result")
        self.assertNotIn("---", b.calls[0]["prompt"])  # no section separators


class T05PriorOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(self)

    def test_prior_first_order(self):
        """chain_order='before' puts prior response before user prompt."""
        b1, b2 = FakeBackend("prior-text"), FakeBackend("x")
        step1 = StepSim(backend=b1)
        step2 = StepSim(chain_parent=step1, chain_order="before", backend=b2)

        step1.run("q")
        step2.run("user-text")

        prompt = b2.calls[0]["prompt"]
        self.assertLess(prompt.index("prior-text"), prompt.index("user-text"))

    def test_prior_after_order(self):
        """chain_order='after' puts user prompt before prior response."""
        b1, b2 = FakeBackend("prior-text"), FakeBackend("x")
        step1 = StepSim(backend=b1)
        step2 = StepSim(chain_parent=step1, chain_order="after", backend=b2)

        step1.run("q")
        step2.run("user-text")

        prompt = b2.calls[0]["prompt"]
        self.assertLess(prompt.index("user-text"), prompt.index("prior-text"))


if __name__ == "__main__":
    unittest.main()
