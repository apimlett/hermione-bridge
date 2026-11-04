# Worker visibility: bounded first increment

## Recorded cause and plan

The client ends its Runs API subscription at the parent terminal event; its chat reducer has no child state. Its bridge has no worker route. Thus a parent's completed delegation tool step cannot represent an independently running child, and later child completion cannot be recovered by this client. This is a client observability gap, not proof of why any particular historical completion wake was lost.

Source inspected (read-only): Hermes `gateway/platforms/api_server_runs.py` forwards **existing** `subagent.start` and `subagent.complete` events, with identity fields, but deliberately drops `subagent.tool` and progress. A run stream is not a durable, independently followed child roster. `tui_gateway/methods_session.py` exposes `delegation.status`, but returns the live in-process registry, not durable results. No invented upstream worker HTTP endpoint is used.

Hermes `tools/async_delegation.py` records dispatched jobs and per-task results in `state.db/async_delegations`; `record_unit_child` retains partial batch outcomes. The canonical schema is in `hermes_state_common.py`. The generic API adapter declares async delivery unsupported, but session-history delivery and wake paths also exist: the original wake/delivery failure remains undiagnosed.

Implement an optional, read-only bridge ledger adapter, a capability-bearing authenticated worker snapshot, and client polling independent of parent turns. Keep terminal outcome separate from parent review and Hermes result-delivery state. Restrict activity to fixed safe labels; do not forward goals, summaries, transcripts, tool arguments, or reasoning. Add replay-safe local notification attempts using durable client acknowledgement. Expose state in the existing Activity view without redesign.

## Boundaries

This is a new **bridge-specific** `/v1/profiles/{profile}/workers` contract, not an existing Hermes HTTP endpoint. Opt-in `worker_observability = true` (environment `HERMES_BRIDGE_WORKER_OBSERVABILITY=true`); default disabled. Existing profile authorization runs before filesystem access. The configured Hermes root must be the root containing `profiles/`, not a named profile directory. No live configuration has been changed.

Ledger presence is not a live heartbeat. Running means last recorded running; liveness, parent run/job identity and parent review are explicitly unknown when unavailable. Stable identity is delegation ID plus task index, not an invented runtime child-session ID. Task labels are generic ordinals because arbitrary goals can contain private data. No per-tool activity is claimed. This first increment covers retained **asynchronous ledger** jobs only, not all synchronous or external workers. Retention is Hermes-owned; snapshots are bounded and signal truncation.

Killed/frozen-app delivery is **BLOCKED**. There is no server push sender, registration, receipt processing or real-device verification. No cloud project, provider dependency, telemetry, credential change or deployment is introduced. A provider seam would not make killed-app delivery work and is deferred until the operator chooses infrastructure compatible with tailnet policy. Proposed follow-up: upstream scoped durable worker/status API with safe user-authored task labels and explicit review acknowledgement, plus opt-in server push validated on a device.

## Temporary task-bot integration (proposed, not orchestration)

A worktree is code isolation, not the user-facing work identity. Future temporary task bots need a task brief/context accessible outside the initiating parent chat, user steering with actual worker acknowledgement, and published artefacts. Preserve the current delegation ID, task index, parent profile and parent session as linkage keys; add canonical task/session/child identifiers only when supplied by a real source. Parent run/job IDs remain null here, not fabricated.

Native Hermes Bot means a profile; a delegated worker is a separate primitive. This increment does not turn ledger records into addressable chat bots. No profile/credential copying, artificial roster entries or separate bot framework. Proposed follow-up: an opt-in task/session projection over real worker routing, stable task detail deep links, and explicit steering/artefact capabilities. It must preserve task context, review and decisions across archiving, independently from worktree cleanup. The Activity panel and notification worker ID can later link to that task detail surface without changing worker identity.

## Parent-owned PR integration / RAS handoff

Local new-feature, evidence-driven-testing and greploop instructions were read. After fetching origin, bridge HEAD and `origin/main` both equal `37e6d449262a161c49234424141db9fa8983ed31`; unlike the app, no design-system base dependency exists here. The read-only origin scope check found no open bridge PRs. The app's base is the existing design-system PR HEAD, not main; its design document records the dependency and task-only manifest for the parent.

Task-only paths:

```text
src/hermes_bridge/app.py
src/hermes_bridge/config.py
src/hermes_bridge/workers.py
tests/test_workers.py
docs/AGENT_OBSERVABILITY.md
```

Full unittest output is retained privately outside the repositories as `agent-notifications-bridge-tests.log`; client/POC/typecheck outputs and an assertion/caveat report are in the app's ignored `dist/agent-notifications-evidence/`. They are local evidence only, not uploaded or claimed as device/production proof. No raw development log is tracked.

Greptile: **not run, 0 cycles, no score**. Parent owns commits/push/PR/review transitions; no review trigger or publication occurred. The new route inherits the existing authenticated profile guard, including the route-derived denial coverage in `tests/test_mobile_scope.py`; the new worker tests separately prove scope/auth happen before disk reads.

## Evidence

Baseline app: typecheck passes; npm test 149 tests / 141 pass / 8 bridge skips; test:poc 52 pass. Baseline bridge: 49 pass; existing ResourceWarnings. npm install reports 13 inherited vulnerabilities (1 low, 10 moderate, 2 high); no audit fixes applied.

Regression RED: authenticated worker snapshot returns 404 instead of 200 for a durable child still running after its parent request. GREEN: bridge regression verifies two tasks remain running independently, later completed/failed outcomes replay through a new Bridge instance, no private goal/result text returned. Strict opt-in configuration RED (missing field) then GREEN. Synthetic fixtures only: not live Hermes or device verification.

Final verification: **57 bridge unittest tests pass** (49 baseline plus the new worker regression cases, no failures). The app with this feature bridge passes typecheck, **149/149 npm tests**, and **63/63 POC tests**, zero skips/failures. Real feature bridge -> client -> independent observer -> mocked local scheduling/reopen acknowledgement is exercised; this is not live runtime or device notification delivery. Partial child completion retains its stable event ID when its batch later joins; unknown states and malformed child statuses remain unknown; unit join time is not misreported as a batch child's completion time. Snapshot truncation is explicit. Existing upstream ResourceWarnings remain. Both worktrees pass `git diff --check`.

No commits, deployment, service restart, installed-app change, profile or credential/config edit occurred. The Hermes runtime was inspected read-only. The optional ledger adapter remains disabled by default. Full push/results/steering acceptance is blocked as described above, not completed by these fixture tests.
