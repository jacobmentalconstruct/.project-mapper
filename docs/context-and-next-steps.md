# _ProjectMAPPER Parking / Handoff Note
**Date:** 2026-10-27  
**State:** Stable / parked  
**Immediate next action:** Discuss and redesign the main-window panel plan before implementing UI changes.

## 1. Current project state

_ProjectMAPPER is currently running successfully after the latest patching and housekeeping work.

Latest confirmed conditions:

- Application launches and runs without error.
- Snapshot compilation works normally.
- Latest cleaned projection contains approximately:
  - 134 tree entries
  - 115 captured files
  - 0 snapshot errors
- Generated test/temp clutter was removed.
- `src/projectmapper.egg-info/` was removed.
- `.gitignore` was updated to prevent generated scratch/cache/test material from returning.
- Existing `src/projectmapper/` package layout was intentionally retained.

The `src/projectmapper/` structure is considered valid and should NOT be flattened merely because it adds another tree level.

Current major source groupings remain:

- `src/projectmapper/adapters/`
- `src/projectmapper/application/`
- `src/projectmapper/core/`
- `src/projectmapper/tools/`
- `src/projectmapper/app.py`
- `src/projectmapper/cli.py`
- `src/projectmapper/mcp.py`
- `src/projectmapper/tree_view.py`

No source restructuring is presently justified.

---

# 2. Completed tranche: patch reliability

## A1 — `PARTIAL_LINE_MATCH`

Implemented in:

`src/projectmapper/core/hunks.py`

New behavior:

When a patch search block cannot match as complete source lines, the engine now checks whether the raw search text actually exists but starts or ends inside a physical source line.

That case now reports:

`PARTIAL_LINE_MATCH`

rather than collapsing into the generic:

`Search block not found.`

Important design decision:

- Matching behavior was NOT made fuzzier.
- The patcher does NOT automatically expand partial anchors.
- The diagnostic tells the patch generator/user to regenerate a complete-line search block.
- Existing whole-line exact and floating/indent matching semantics remain unchanged.

Conceptual behavior:

- whole-line match → proceed
- ambiguous whole-line match → existing ambiguity error
- text exists but cuts line boundary → `PARTIAL_LINE_MATCH`
- text genuinely absent → `Search block not found`

## A2 — regression tests

Updated:

`tests/test_patcher.py`

Coverage includes:

- partial prefix
- partial suffix
- partial both edges
- CRLF partial-line case
- genuinely missing search text
- valid complete-line search

Local verification:

`python -m pytest tests/test_patcher.py -q`

Result:

**32 passed in 5.16s**

### A status

**A1/A2 COMPLETE / ACCEPTED / PARKED**

Do not reopen unless a concrete regression appears.

---

# 3. Completed tranche: housekeeping

A bounded housekeeping pass was performed.

The goal was explicitly NOT to redesign the architecture. The purpose was to remove generated residue and make the real project shape visible.

Removed/generated clutter included:

- large forest of `tests/tmp*` temporary directories
- `.scratch-tmp/`
- `.test-tmp/`
- `.tmp-vtree-*`
- `.ruff_cache/`
- `src/projectmapper.egg-info/`
- other generated cache/test residue as encountered

The cleaned tree dropped from roughly 260 entries to roughly 134 entries.

The application still runs successfully afterward.

## `.gitignore`

The current ignore rules were updated to cover generated state including:

- `.pytest_cache/`
- `.test-tmp/`
- `.scratch-tmp/`
- `.tmp-vtree-*/`
- `.ruff_cache/`
- temporary test directories
- `*.egg-info/`
- `_projectmapper/`
- SQLite/database outputs
- Python caches
- ordinary build/dist/venv artifacts

Important lesson discovered during cleanup:

Do NOT remove ProjectMAPPER's built-in safety exclusions wholesale.

When exclusions were removed/disabled, snapshot compilation began creating its own `_projectmapper/...building` output inside the scanned project and then detected that self-created file as a project mutation:

`Project changed during capture`

Re-enabling the intended exclusions restored normal operation.

So:

**Delete generated residue; do not remove the internal guardrails.**

### Housekeeping status

**COMPLETE / PARKED**

Do not continue cleaning merely because more cleanup is theoretically possible.

Only reopen housekeeping for an identified concrete problem.

---

# 4. Snapshot self-capture observation

During housekeeping, disabling/removing exclusions caused the compiler to apparently:

1. inspect project state
2. begin creating the snapshot/building file
3. see its own generated output as a source-tree change
4. abort with `Project changed during capture`
5. remove the incomplete snapshot

Normal operation returned after built-in exclusions were restored.

No additional repair was made because intended/default exclusion behavior already prevents the problem.

This may be revisited later only if supporting compilation with all exclusions disabled becomes an actual requirement.

---

# 5. UI work — NOT STARTED

Earlier, a preliminary panel plan was discussed, but it is now explicitly **cancelled/superseded pending discussion**.

The original rough idea was to encapsulate:

- snapshot/project controls
- file explorer
- activity log/history

inside collapsible labeled regions while keeping the bottom status bar owned by the main window.

However, the user stated that the UI update should be planned somewhat differently.

Therefore:

**DO NOT implement the old B1/B2/B3 UI plan without first discussing the revised desired layout.**

What remains valid conceptually:

- We want more room for additional domain-specific controls.
- The file explorer should become an identifiable UI region.
- The terminal/activity log and its History button should become an identifiable region.
- The bottom status bar remains global/window-owned.
- Existing actions should not be rewritten merely to reorganize presentation.
- Avoid building a generalized docking/layout framework.

Immediate next task:

**Discuss the desired UI panel structure and lock a revised plan before editing `app.py`.**

---

# 6. Git Controls — planned but intentionally parked

The first proposed additional domain after the UI container work is Git control.

Do NOT implement this before the UI layout is agreed upon and completed.

The intended first variant is intentionally small:

- Refresh / Status
- Stage Changes
  - normal Git / `.gitignore` mode
  - ProjectMAPPER-selection-aware mode
- multiline commit message
- Commit
- Pull
- Push

No large Git client.

Explicitly excluded from v1 unless later justified:

- merge UI
- rebase
- stash
- reset
- conflict editor
- force push
- tags
- PRs/issues
- GitHub API integration
- VCS abstraction framework
- virtual Git filesystem
- branch-management UI
- remote-management UI

---

# 7. `_GitPUSHER` donor project

A complete projection of the user's existing `_GitPUSHER` helper was reviewed.

Important discovery:

`_GitPUSHER` is essentially:

- a small `GitOpsEngine`
- wrapped in a Tkinter GUI

The useful engine already demonstrates:

- `subprocess.run(["git", ...], cwd=repo_path)`
- Git status
- current branch
- branch listing
- checkout
- pull
- initialization workflow
- add/commit/push behavior

The existing GUI should NOT be embedded wholesale into ProjectMAPPER.

Preferred direction:

1. strip `_GitPUSHER` down into a small headless/reusable Git tool
2. verify it independently
3. optionally retain a thin CLI
4. integrate the same tool beneath ProjectMAPPER actions
5. expose only the subset needed by the Git Controls panel

This is preferred over reimplementing Git logic directly inside ProjectMAPPER.

---

# 8. Git operating model decision

Git should operate against the **actual selected project directory on disk**.

It should NOT operate against:

- the SQLite snapshot
- ProjectMAPPER `VirtualTree`
- a materialized virtual projection

Those are different concerns.

Desired model:

Actual project working files:
- observed/snapshotted/transformed by ProjectMAPPER

`.git/`:
- owned by Git

Git tool:
- runs normal system Git commands with the project root as `cwd`

ProjectMAPPER should not back up or manipulate `.git` internals.

Typical command implications:

- `git status`: read only
- `git add`: modifies Git index, not normal working files
- `git commit`: modifies Git history/index metadata
- `git push`: mutates remote repository
- `git pull`: may modify working files

For the initial safe pull behavior, `git pull --ff-only` is preferred over automatic merge/rebase behavior.

No force push.

---

# 9. Planned Git implementation sequence

This sequence is parked for later.

## C — headless Git tool

Extract/adapt `_GitPUSHER` into a small reusable component exposing approximately:

- status
- current_branch
- stage_all
- stage_paths
- commit
- pull_ff_only
- push

Possible later capabilities already present in donor code but NOT part of initial scope:

- branches
- checkout
- initialize repository

Prove the tool against temporary local Git repositories before integrating it.

## D — ProjectMAPPER integration

Wrap the proven Git tool with thin ProjectMAPPER application actions:

- `git.status`
- `git.stage`
- `git.commit`
- `git.pull`
- `git.push`

Desktop first.

Do not automatically expose Git mutation actions through MCP/CLI in the initial tranche.

## Selection-aware staging

Two modes are planned:

Normal Git mode:

`git add -A`

ProjectMAPPER selection mode:

Git determines changed paths  
→ ProjectMAPPER inclusion/selection filters them  
→ only approved paths are staged

Git remains authority on what has changed.

ProjectMAPPER remains authority on what selected portion is allowed.

---

# 10. Project philosophy / constraints

These constraints should govern the next session:

- We are not building a spaceship.
- Prefer small, functional daily-use implementations.
- Proof of concept before extensibility.
- Avoid generalized frameworks unless demanded by a concrete use case.
- One file / one patch at a time when practical.
- Prefer exact patches and deterministic stop conditions.
- Separate concerns rather than growing one large subsystem.
- Existing working machinery should be reused.
- “Could build more architecture” is NOT equivalent to “needs more architecture.”
- Housekeeping should stop once obvious clutter is removed.
- Do not restructure working package layout without evidence of a real problem.
- Keep Tokenizing Patcher as the fast single-file path.
- Keep Project Patcher as the ordered whole-project transformation path.

---

# 11. Current resume point

A fresh session should begin here:

**Do not write code yet.**

First discuss the revised main-window UI organization with the user.

Specifically determine:

- which domains should receive collapsible/labeled regions
- desired spatial relationship between explorer, controls, log, and future Git controls
- which regions should initially be expanded/collapsed
- whether Git Controls belongs directly in the main vertical flow or somewhere adjacent
- what should remain permanently visible

Once that shape is agreed:

1. write the smallest UI plan
2. establish stop conditions
3. patch incrementally
4. verify
5. park UI
6. only then resume the Git tool work

## Current overall status

**Patch reliability:** COMPLETE  
**Housekeeping:** COMPLETE  
**Application sanity:** PASS  
**UI redesign:** DISCUSSION NEXT  
**Git tool:** PLANNED / PARKED  
**Git integration:** PLANNED / PARKED