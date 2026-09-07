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
Choose the smallest correct change. The user selects the runtime model and effort; role configuration owns model, effort, and role instructions. Use one bounded writer for settled work, adaptive implementation only for material native/platform uncertainty, and read-only exploration or review only when independently useful. Keep one writer for overlapping files. A changed candidate after review must be restabilized; after a second correction or two blocked returns, reset the contract before continuing. Persisted handoffs are for named cross-session, interruption-sensitive, risky, or separately executed work; otherwise use the approved inline scope. Optional cheap direct regression tests are appropriate when they materially support changed behavior; do not require a new harness or full suite by default.

## Safety
Do not inspect or edit secrets, credentials, local settings, runtime or production state, generated heavy artifacts, dependencies, CI/CD, deployment, publication, or external exposure unless explicitly in scope. Never reproduce private values. Do not commit, push, or publish unless explicitly requested. Report source readiness separately from unavailable runtime verification.

## Completion
Review the stable diff against every criterion and protected behavior, verify affected references and Markdown fences, run the smallest relevant checks plus git diff --check, and report changed files, evidence, blocked checks, partial edits, and unresolved questions.

## Checks
Focused checks: python -m pytest tests/test_stream_status.py -q; VOD/worker changes: python -m pytest tests/test_vod_routes.py tests/test_workers_snapshot.py -q; broader code changes: python -m pytest tests/ -q; compose changes: docker compose config. Documentation-only changes use git diff --check and link/fence scans.
For incomplete delegated work, report the blocker, resume condition, and next owner/action. Requested model or effort is configuration context, not execution evidence; unknown stays unknown, with no diagnostic-only agents or probes to fill observation fields. After stable-diff review, read deeper only for gaps, conflicts, or concrete risk; rerun checks only for a mandatory contract, changed target or assumption, insufficient evidence, or integration risk. Return concise results and evidence references without raw logs or unchanged inventories. While children run, continue useful work within existing ownership and parallelism rules; otherwise wait for notifications. Avoid liveness-only polling, rereads, or state rewrites; respond to errors, inconsistent state, and user steering, and follow host progress rules.
