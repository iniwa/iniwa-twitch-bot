# AGENTS.md

This entry governs Iniwa's Twitch Bot and management dashboard.

## Verified project facts and protected behavior
- Python web application with Twitch IRC/Helix integrations, scheduler workers, analytics, VOD archive, and stream-status API.
- config.py:49-91 owns lock-protected detached current-stream snapshots; routes/analytics.py:12-19 validates stream IDs. GET /api/stream/status is read-only and must not call Twitch per request.
- Preserve stream/session snapshot semantics, scheduler lifecycle, Twitch pacing/authentication, external-service test isolation, Docker image, port 8501, /app/data and /app/downloads mounts, and existing deployment boundaries. Do not add secretary-bot, OBS control/archive, administrator VOD gating, or VOD-to-OBS migration.
- docs/rebuild/*.md is an accepted future product plan; distinguish planned requirements from current implementation.

## Authority and scope
Apply runtime, tool, organization, and safety policy, then explicit user policy, then this entry and the approved task. Verified repository facts replace defaults; they do not grant authorization. Preserve unrelated work and stop on an overlap that requires guessing.

## Execution
Choose the smallest correct change. Default to primary execution. The primary owns design, implementation, related discovery, verification, corrections, and final acceptance at any task size. Before implementation, decide whether to delegate, then choose the role and route: `small-primary` for direct work of any size, `bounded` for a settled delegated outcome, `adaptive` for delegated material technical uncertainty, or `non-implementation`. delegate autonomously within existing authority only when replacing primary work lowers expected total effort including handoff, communication, waiting, integration, verification, and corrections, or when a named material risk or mandatory independent verification gate warrants it. Size or technical uncertainty alone is insufficient; routine direct work needs no per-task justification. Use one `bounded_implementer` for settled cohesive work, `adaptive_implementer` for material native/platform or cross-system acceptance uncertainty, `bounded_explorer` only for independently valuable read-only discovery, and `bounded_reviewer` only for a named material risk or existing mandatory gate. The user's runtime model and effort remain authoritative and role configuration owns role settings. Keep one writer for overlapping files; delegated writers do not redelegate. A changed candidate after review must be restabilized; after a second correction or two qualifying blocked or partial returns, reset the contract before continuing. Persisted handoffs are for named cross-session, interruption-sensitive, risky, or separately executed work; otherwise use the approved inline scope. Optional cheap direct regression tests are appropriate when they materially support changed behavior; do not require a new harness or full suite by default.

## Safety
Do not inspect or edit secrets, credentials, local settings, runtime or production state, generated heavy artifacts, dependencies, CI/CD, deployment, publication, or external exposure unless explicitly in scope. Never reproduce private values. Do not commit, push, or publish unless explicitly requested. Report source readiness separately from unavailable runtime verification.

## Completion
Review the stable diff against every criterion and protected behavior, verify affected references and Markdown fences, run the smallest relevant checks plus git diff --check, and report changed files, evidence, blocked checks, partial edits, and unresolved questions.

## Checks
Focused checks: python -m pytest tests/test_stream_status.py -q; VOD/worker changes: python -m pytest tests/test_vod_routes.py tests/test_workers_snapshot.py -q; broader code changes: python -m pytest tests/ -q; compose changes: docker compose config. Documentation-only changes use git diff --check and link/fence scans.
Reassess ownership when primary execution lowers expected remaining total effort or delegation is unavailable. The primary may reclaim work of any size before correction thresholds, after confirming child writes have stopped and ownership has returned, and resetting acceptance, protected boundaries, authority, environment, and evidence. Required verification and preservation of safe blocked work still apply. Existing mandatory independent and multi-reviewer gates remain in force.

For incomplete delegated work, report the blocker, resume condition, and next owner/action. Requested model or effort is configuration context, not execution evidence; unknown stays unknown, with no diagnostic-only agents or probes to fill observation fields. After stable-diff review, read deeper only for gaps, conflicts, or concrete risk; rerun checks only for a mandatory contract, changed target or assumption, insufficient evidence, or integration risk. Return concise results and evidence references without raw logs or unchanged inventories. While children run, continue useful work within existing ownership and parallelism rules; otherwise wait for notifications. Do not add research/checks, inspect a changing candidate, or repeat liveness polling, rereads, or state updates merely to fill the wait or observe liveness. Respond to errors, inconsistent state, and user steering, and follow host progress rules.
