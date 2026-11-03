"""Tests for inference_assembler — T03 verification.

Covers assemble_prompt, freeze_request, and a capture-backend check
confirming the frozen request equals the payload received by the adapter.
"""

import unittest
from pathlib import Path

from tests.support import temporary_directory
from projectmapper.tools.spine import Spine
from projectmapper.tools.inference_spine import resolve_entries, ResolvedEntry
from projectmapper.tools.inference_assembler import (
    AssemblyError,
    InferenceRequest,
    assemble_prompt,
    freeze_request,
)

_SCHEMA = {"id": "note", "version": 1, "properties": {
    "body": {"type": "string", "label": "Body"},
    "tag":  {"type": "string", "label": "Tag"},
}}


def _entry(tmp: Path) -> ResolvedEntry:
    spine = Spine.create(tmp / "s.jsonl")
    spine.register_schema(_SCHEMA)
    spine.append_entry("note@1", {"body": "context line", "tag": "ref"})
    return resolve_entries(spine.path, [0])[0]


class AssemblePromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(self)
        self.entry = _entry(Path(self.tmp.name))

    def test_prompt_only(self):
        result = assemble_prompt([], None, "hello world")
        self.assertEqual(result, "hello world")

    def test_spine_context_prepended(self):
        result = assemble_prompt([self.entry], None, "my question")
        self.assertIn("context line", result)
        self.assertIn("my question", result)
        self.assertLess(result.index("context line"), result.index("my question"))

    def test_prior_response_between_context_and_prompt(self):
        result = assemble_prompt([self.entry], "prior output", "follow-up")
        ctx_pos = result.index("context line")
        prior_pos = result.index("prior output")
        prompt_pos = result.index("follow-up")
        self.assertLess(ctx_pos, prior_pos)
        self.assertLess(prior_pos, prompt_pos)

    def test_no_entries_with_prior(self):
        result = assemble_prompt([], "previous answer", "next question")
        self.assertIn("previous answer", result)
        self.assertIn("next question", result)

    def test_schema_label_used(self):
        result = assemble_prompt([self.entry], None, "q")
        self.assertIn("Body:", result)

    def test_empty_prior_response_ignored(self):
        result_none  = assemble_prompt([], None,  "q")
        result_empty = assemble_prompt([], "",     "q")
        result_ws    = assemble_prompt([], "   ",  "q")
        self.assertEqual(result_none, result_empty)
        self.assertEqual(result_none, result_ws)

    def test_empty_prompt_and_no_context_raises(self):
        with self.assertRaises(AssemblyError):
            assemble_prompt([], None, "")

    def test_all_empty_raises(self):
        with self.assertRaises(AssemblyError):
            assemble_prompt([], "", "   ")

    def test_deterministic(self):
        a = assemble_prompt([self.entry], "prior", "prompt")
        b = assemble_prompt([self.entry], "prior", "prompt")
        self.assertEqual(a, b)

    def test_multiple_entries_ordered(self):
        tmp = Path(self.tmp.name)
        spine = Spine.create(tmp / "multi.jsonl")
        spine.register_schema(_SCHEMA)
        spine.append_entry("note@1", {"body": "first"})
        spine.append_entry("note@1", {"body": "second"})
        entries = resolve_entries(spine.path, [0, 1])
        result = assemble_prompt(entries, None, "q")
        self.assertLess(result.index("first"), result.index("second"))


class FreezeRequestTests(unittest.TestCase):
    def test_basic_freeze(self):
        req = freeze_request("llama3", "hello")
        self.assertIsInstance(req, InferenceRequest)
        self.assertEqual(req.model, "llama3")
        self.assertEqual(req.prompt, "hello")
        self.assertIsNone(req.options)

    def test_with_options(self):
        opts = {"temperature": 0.5}
        req = freeze_request("llama3", "hello", opts)
        self.assertEqual(req.options, opts)

    def test_empty_options_normalised_to_none(self):
        req = freeze_request("llama3", "hello", {})
        self.assertIsNone(req.options)

    def test_frozen_immutable(self):
        req = freeze_request("llama3", "hello")
        with self.assertRaises((AttributeError, TypeError)):
            req.model = "other"  # type: ignore[misc]

    def test_empty_model_raises(self):
        with self.assertRaises(AssemblyError):
            freeze_request("", "hello")

    def test_empty_prompt_raises(self):
        with self.assertRaises(AssemblyError):
            freeze_request("llama3", "")


class CaptureFakeBackendTests(unittest.TestCase):
    """Verify that what freeze_request produces equals what the adapter receives."""

    def test_frozen_request_matches_adapter_call(self):
        """Simulate the T04 run path: assemble → freeze → pass to adapter."""
        captured: list[dict] = []

        def fake_generate(model, prompt, options, on_token, on_done, on_error, stop_event):
            captured.append({"model": model, "prompt": prompt, "options": options})
            on_done({"elapsed_s": 0.0, "prompt_tokens": None,
                     "response_tokens": None, "context_length": None})

        prompt = assemble_prompt([], "prior", "user question")
        req = freeze_request("llama3", prompt, {"temperature": 0.7})

        import threading
        fake_generate(
            req.model, req.prompt, req.options,
            lambda tok: None,
            lambda meta: None,
            lambda err: None,
            threading.Event(),
        )

        self.assertEqual(len(captured), 1)
        call = captured[0]
        self.assertEqual(call["model"],   req.model)
        self.assertEqual(call["prompt"],  req.prompt)
        self.assertEqual(call["options"], req.options)

    def test_assembly_error_prevents_freeze(self):
        """An assembly failure must not produce any frozen request."""
        req = None
        try:
            prompt = assemble_prompt([], "", "")
            req = freeze_request("llama3", prompt)
        except AssemblyError:
            pass
        self.assertIsNone(req)


if __name__ == "__main__":
    unittest.main()
