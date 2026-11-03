"""Assembler: resolve spine context → assemble prompt → freeze inference request.

The two public callables are:

    assemble_prompt(entries, prior_response, user_prompt) -> str
    freeze_request(model, prompt, options) -> InferenceRequest

Both are pure, deterministic, and have no side effects.
The same logic is used for preview and for the actual submission so that
what the user previews is exactly what gets sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

if __package__:
    from .inference_spine import ResolvedEntry, SpineContextError
else:
    from inference_spine import ResolvedEntry, SpineContextError

__all__ = [
    "AssemblyError",
    "InferenceRequest",
    "assemble_prompt",
    "freeze_request",
]

_SECTION_SEP = "\n\n---\n\n"


class AssemblyError(Exception):
    """Raised when the prompt cannot be assembled (missing required context)."""


@dataclass(frozen=True)
class InferenceRequest:
    """The exact application-level request handed to the inference adapter.

    Frozen at assembly time; never reconstructed after submission.
    Fields map 1-to-1 to what OllamaClient.generate() receives.
    """
    model: str
    prompt: str
    options: dict | None = field(default=None)

    def __post_init__(self):
        if not self.model:
            raise AssemblyError("model must not be empty")
        if not self.prompt:
            raise AssemblyError("prompt must not be empty")


def _format_entry(entry: ResolvedEntry) -> str:
    """Render one resolved spine entry as a labelled block of text."""
    schema_title = entry.schema_def.get("title") or entry.schema_key
    lines = [f"[{schema_title}]"]
    props: dict = entry.schema_def.get("properties", {})
    for key, val in entry.data.items():
        label = props.get(key, {}).get("label") or key
        lines.append(f"{label}: {val}")
    return "\n".join(lines)


def assemble_prompt(
    entries: Sequence[ResolvedEntry],
    prior_response: str | None,
    user_prompt: str,
    *,
    prior_first: bool = True,
) -> str:
    """Assemble the full prompt string from spine context, prior response, and user input.

    Layout (each section present only when non-empty):
        [spine context block]
        ---
        [prior response]  (order relative to user_prompt controlled by prior_first)
        ---
        [user prompt]

    Args:
        entries: Resolved spine entries in selection order (may be empty).
        prior_response: The preceding chain step's current result, or None/empty.
        user_prompt: The text the user typed into this inference step.
        prior_first: When True (default), prior response precedes user_prompt.
            Pass False to append the prior response after the user's prompt
            (matches the "After" chain order option).

    Returns:
        The assembled prompt string.

    Raises:
        AssemblyError: If the assembled prompt would be empty.
    """
    parts: list[str] = []

    if entries:
        context_blocks = [_format_entry(e) for e in entries]
        parts.append("\n\n".join(context_blocks))

    body: list[str] = []
    if prior_response and prior_response.strip():
        body.append(prior_response.strip())
    if user_prompt and user_prompt.strip():
        body.append(user_prompt.strip())
    if not prior_first:
        body.reverse()
    parts.extend(body)

    if not parts:
        raise AssemblyError("Assembled prompt is empty — add a prompt or select spine context.")

    return _SECTION_SEP.join(parts)


def freeze_request(
    model: str,
    prompt: str,
    options: dict | None = None,
) -> InferenceRequest:
    """Freeze the complete inference request.

    This is the single point of capture: the returned object is what will be
    (or was) handed verbatim to the inference adapter.

    Args:
        model: The model name string.
        prompt: The fully assembled prompt (from assemble_prompt).
        options: Optional generation options dict (temperature, etc.), or None.

    Returns:
        An immutable InferenceRequest.

    Raises:
        AssemblyError: If model or prompt is empty.
    """
    if not model:
        raise AssemblyError("Cannot freeze request: model is empty.")
    if not prompt:
        raise AssemblyError("Cannot freeze request: prompt is empty.")
    return InferenceRequest(model=model, prompt=prompt,
                            options=options if options else None)
