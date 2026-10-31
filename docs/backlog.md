# ProjectMapper Backlog

Items parked from the 2026-10-31 adversarial complexity review and SchemaTOUCHER V1 sprint.
Highest-leverage work first within each tier.

---

## Tier A — Codebase hygiene (no new features)

### A1 · Star-import elimination in `app.py`
Four `from X import *` blocks make dead-symbol detection impossible and Pyright accuracy low.
Replace with explicit imports.  
**Effort:** medium. **Risk:** low (mechanical; tests cover regressions).

### A2 · `ProgressPopup` extraction
`ProgressPopup` (app.py:152–217) is a self-contained class; move it to `tools/ui_base.py` so
it can be shared and tested in isolation.  
**Effort:** medium. **Risk:** low.

### A3 · `_setup_ui` sub-builder decomposition
`_setup_ui` (app.py:642–881) is one large method. Decompose into named sub-builder methods per
UI zone (no abstraction, just named methods). No new files needed.  
**Effort:** medium. **Risk:** low.

### A4 · `_run_export` orchestrator
Scan → snapshot → export pipeline: three methods share the same 4-step shape. Extract a shared
`_run_export` orchestrator.  
**Effort:** medium. **Risk:** low; well-covered by tests.

---

## Tier B — Correctness / safety

### B1 · `write_text_file` atomicity
`snapshots.py:write_text_file` is non-atomic. The ZIP-staging callsite (exports.py) is
intentional; the controller.py callsite is a latent risk if the process crashes mid-write.
Consider adding an atomic write path (write to `.tmp`, then rename) for the controller callsite.  
**Effort:** small. **Risk:** low; current comment documents the gap.

### B2 · `controller.py` `state.root` None-guards
Pyright flags ~10 places where `state.root` is used without a None-guard. No runtime failures
known, but narrowing these closes a class of crash on unexpected early calls.  
**Effort:** medium. **Risk:** low.

### B3 · `project_patcher.py` duplicate DiffFile construction
Lines 135–136 build DiffFile twice and run 4 SequenceMatcher passes where 1 suffices.
Refactor to single construction.  
**Effort:** small. **Risk:** low.

---

## Tier C — Deferred / low-priority

### C1 · `_try_import` inline (diagnostics.py)
Single-use helper; reviewed 2026-10-31, kept for readability. Revisit if diagnostics.py grows.

### C2 · `EXAMPLE_MANIFEST` in `project_patcher.py`
Used only by one test, not in controller. Either promote to a fixture or remove.

### C3 · Pyright `exports.py:98` `SOURCE_ROOT` None narrowing
No runtime risk; tighten the type annotation.

### C4 · Pyright `app.py:1254` `**_mb_kw` dict unpacking
No runtime risk; annotate the dict type.

### C5 · Spine deduplication / growth management
Full text stored inline per entry compounds quickly once verbatim evidence is included.
Options to evaluate: shared text blocks referenced by hash, truncation policy, or a separate
`blobs/` store alongside the JSONL. Read complexity is the main tradeoff.
Parked until spine size becomes a real friction point in day-to-day use.

### C6 · `project-lineage.jsonl` duplicate timestamps
The last three entries in the public repo's `project-lineage.jsonl` share the same timestamp
(`2026-10-31T00:00:00+00:00`). Not broken, but should carry distinct timestamps when touched next.

### C6 · Tool rename audit
Several internal tool names read strangely to outside contributors. Candidates:
- **SchemaTOUCHER** → SpineForge (or similar; raised 2026-10-31)
- **TokenizingPatcher** — review name clarity
- **TextTOUCHER** — same concern as SchemaTOUCHER

A rename touches: class name, menu label, docs, spine filename, memory files. No logic changes.
**Effort:** medium per tool. **Risk:** low (mechanical).

### C7 · Retire "Export Sidecar ZIP"
`create_sidecar_export` targets the old `.projectmapper/app/` layout which no longer exists.
Public distribution is now via `gh repo clone ... .project-mapper` — no ZIP needed.
Remove the button and deprecate the function. Audit whether any test or downstream code still
references `sidecar_export`.

### C8 · "Vend a Copy" — rename + destination picker for vendor export
`Export Vendor App` → **"Vend a Copy"**: rename the button, add a folder picker so the user
chooses the destination, and have the export create a self-titled subfolder there
(e.g. `<chosen-dir>/ProjectMapper/`). No logic change beyond the picker and folder naming.

### C10 · Reference library (`refs/` in sidecar)
Allow users to copy files or folders into `.project-mapper/refs/` as reference material
(docs, specs, context) without touching the project tree. Literal file copy — no indexing.
Gitignored alongside `spines/`. Access via right-click context menu on folders/files in
the project tree: "Copy to refs". No new UI panel needed for V1.

---

## References

- Adversarial complexity review: `memory/project-complexity-review.md`  
- SchemaTOUCHER design decisions: `.spine/schematoucher-design.jsonl`  
- Repair sprint plan: `docs/plans/2026-10-30-repair-sprint.md`
