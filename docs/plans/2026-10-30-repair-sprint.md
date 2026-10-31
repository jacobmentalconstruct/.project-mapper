# Repair Sprint — 2026-10-30

**Status:** pending  
**Branch:** legacy  
**Artifact:** https://claude.ai/artifact/ShLYi3nSGQeW9Sz3pbLNRZ  
**Data:** `docs/plans/2026-10-30-repair-sprint.json`

## Purpose

Systematic cleanup and bug-fix pass on the `legacy` branch following a combined
adversarial code review + deep architecture review + three AST scanning passes.
31 tasks across 5 tranches. Designed to be executed by a repair agent iterating
over the JSON task list.

## Tranches

| ID | Title | Tasks | Notes |
|----|-------|-------|-------|
| CW | Cheap Wins | 8 | S-cost only. Execute directly, no design pass. |
| BF | Bug Fixes | 5 | S–M cost. Real bugs: freezes, silent failures, races. |
| DC | Dead Code Sweep | 9 | AST-confirmed. Verify callers → delete → test. |
| CR | Critical Architecture | 1 | L cost. dispatcher.wait() blocks Tk thread. **Decompose first.** |
| LR | Larger Refactors | 8 | Parked. Assess after CW+BF+DC+CR complete. |

## Agent instructions

1. Read `docs/plans/2026-10-30-repair-sprint.json`
2. Follow the `agent_note` field exactly
3. Find the first `"lifecycle": "pending"` task (skip `parked` tranches)
4. Execute it: trace anchor → implement → run tests
5. Update `execution_log`, `decisions`, `lifecycle` in the JSON
6. Update `updated_at` timestamp
7. Commit the JSON file
8. Republish the artifact from it (same URL)
9. Repeat until no pending tasks remain, then STOP and write an assessment

**Do not invent work. Do not skip verification. Record exact test exit codes.**

## Archive

When the sprint is complete:
- Move `docs/plans/2026-10-30-repair-sprint.json` to `docs/plans/archive/`
- Move this file to `docs/plans/archive/`
- Write final summary in `.dev-log/` per tranche-workflow
