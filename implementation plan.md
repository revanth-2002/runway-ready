# Crew Ops Advisor Implementation Plan

## 1. Implementation Objective

Build the Crew Ops Advisor as a deterministic, auditable operational decision-support system for airline crew control. The implementation must preserve the architecture's hard boundary:

- Deterministic Python owns facts, SQL joins, rules, time windows, calculations, costing, ranking, certificates, and validation.
- LLM calls are limited to natural-language intent parsing and slot-filled presentation.
- Every business logic addition ships with tests.
- REST serves Tier 1 point lookups; WebSockets serve Tier 2 and Tier 3 simulations.

## 2. Selected Implementation Stack

- Backend: Python 3.11+ with FastAPI.
- Database: SQLite, rebuilt deterministically from the synthetic JSON dataset.
- Frontend: Streamlit controller console.
- Tests: `pytest`, in-memory SQLite fixtures, mocked LLM clients, and Streamlit smoke checks where practical.
- Runtime contract: JSON REST responses for Tier 1 and structured WebSocket frames for Tier 2 and Tier 3.

## 3. Non-Negotiable Engineering Rules

1. Use frozen dataclasses for domain models and immutable operational state.
2. Use parameterized SQL only; do not use text-to-SQL or prompt-generated queries.
3. Keep all timestamps timezone-aware UTC ISO-8601 values.
4. Add or update tests for every new function, regulatory rule, API endpoint, and simulation behavior.
5. Use structured logging with contextual fields such as `trace_id`, `crew_id`, `flight_id`, and `latency_ms`.
6. Use typed domain exceptions; never use bare `except:`.
7. Keep Tier 3 pragmatic: candidate enumeration, legal feasibility, deterministic costing, lexicographic ranking, and do-nothing benchmark.
8. Enforce slot substitution and numeric validation before any rendered response reaches the UI.

## 4. Target Repository Structure

```text
docs/
data/
  raw/
  schema.sql
  ops.db
advisor/
  api/
  audit/
  data/
  domain/
  llm/
  orchestrator/
  reasoning/
  rules/
  twin/
ui/
  app.py
  components/
tests/
  unit/
  integration/
eval/
```

## 5. Phase 0: Project Baseline, Assumptions, and Tooling

### Goals

Establish the working skeleton, documented assumptions, test layout, and basic developer commands before implementing operational logic.

### Tasks

- Create the Python backend package skeleton under `advisor/`, `tests/`, and `eval/`.
- Create the Streamlit UI skeleton under `ui/`.
- Add `docs/assumptions.md` documenting rolling-window, deadhead, reserve, certificate, and cost interpretations.
- Add `docs/pii_compliance.md` documenting local name-to-crew-ID resolution and the LLM PII perimeter.
- Add project metadata and developer commands for linting, type checking, tests, API startup, and Streamlit startup.
- Add structured logger shell in `advisor/audit/logger.py`.
- Add domain exception hierarchy in `advisor/domain/exceptions.py`.

### Tests

- Add a smoke test confirming package imports.
- Add unit tests for exception construction and logger payload shape.

### Acceptance Criteria

- `pytest tests/` runs successfully.
- Streamlit run instructions exist.
- No production code uses `print()`.
- All new modules have type annotations.

## 6. Phase 1: Data Schema, Ingestion, and Repository Layer

### Goals

Create the SQLite operational database from the synthetic JSON files and expose typed repository functions for all downstream components.

### Tasks

- Implement `data/schema.sql` with all tables and indexes from `architecture.md`.
- Implement `advisor/data/ingest.py` to rebuild `data/ops.db` from `crew-ops-advisor-dataset/data/*.json`.
- Enable SQLite foreign keys on every connection.
- Derive the duty timeline from pairings and assignments.
- Reconcile derived 7-day duty totals against provided `duty_clock` values within 0.1 hours.
- Validate rotation continuity: destination of leg `N` must equal origin of leg `N+1`.
- Implement `advisor/data/repository.py` with small parameterized methods:
  - `get_crew(crew_id)`
  - `get_flight(flight_id)`
  - `get_pairing(pairing_id)`
  - `get_pairing_for_crew(crew_id, at_utc)`
  - `list_reserves(base, report_time_utc)`
  - `list_flights(origin, destination, after_utc, before_utc)`
  - `list_duty_intervals(crew_id, window_start_utc, window_end_utc)`
  - `list_certifications(crew_id)`
  - `list_ratings(crew_id)`

### Tests

- `tests/unit/test_repository.py`
- `tests/unit/test_ingest.py`

Cover:

- Foreign key enforcement.
- Missing entity behavior via `EntityNotFoundError`.
- Duty timeline derivation.
- Duty clock reconciliation failure diagnostics.
- Rotation consistency failures.
- Parameterized lookup behavior.

### Acceptance Criteria

- Database rebuild is deterministic.
- Rebuild completes within the target development budget from the architecture.
- All repository methods return typed dataclasses or typed DTOs.

## 7. Phase 2: Domain Models and Time Utilities

### Goals

Centralize all time parsing, rolling-window slicing, and evidence models so rules and simulations never duplicate date math.

### Tasks

- Implement `advisor/domain/types.py`:
  - `Crew`
  - `Flight`
  - `Pairing`
  - `DutyInterval`
  - `DutyProposal`
  - `Certification`
  - `ReserveWindow`
- Implement `advisor/domain/evidence.py`:
  - `RuleVerdict`
  - `LegalityLedger`
  - `ImpactReport`
  - `EvidenceBundle`
- Implement `advisor/domain/timeutil.py`:
  - strict UTC ISO parsing
  - interval overlap in minutes
  - rolling 7-day and 28-day windows
  - fractional boundary slicing
  - human-display formatting at presentation boundary only

### Tests

- `tests/unit/test_timeutil.py`
- `tests/unit/test_evidence.py`

Cover:

- Midnight UTC crossings.
- Boundary-straddling duties.
- Zero-overlap and full-overlap intervals.
- Naive datetime rejection or normalization policy.
- `LegalityLedger.legal` and `LegalityLedger.breaches`.

### Acceptance Criteria

- Time-window logic exists only in `timeutil.py`.
- Every evidence object exposes source rows needed for auditability.

## 8. Phase 3: Regulatory Rules Engine

### Goals

Implement the 7 pure regulatory rule functions and the uniform engine contract before building LLM or UI behavior.

### Tasks

- Implement `advisor/rules/engine.py` with `evaluate_all()`.
- Implement one pure module per rule:
  - `fdp_01.py`
  - `duty_02.py`
  - `flt_03.py`
  - `rest_04.py`
  - `qual_05.py`
  - `cert_06.py`
  - `base_07.py`
- Load numeric limits from `rules.json`; do not hardcode rule limits in Python.
- Ensure every rule returns `RuleVerdict` with:
  - `rule_id`
  - `passed`
  - `headline`
  - `arithmetic`
  - `inputs`
  - `margin`
  - `source_rows`
  - `assumption`

### Tests

- `tests/unit/test_rules.py`
- `tests/integration/test_scenarios.py`

Cover:

- Passing and failing cases for each rule.
- `RuleVerdict.arithmetic` strings.
- Signed margins.
- Deadhead exclusion from flight-hour limits.
- Missing certification as failure.
- Off-base reserve feasibility using actual scheduled flights.

### Acceptance Criteria

- All 7 rules are deterministic, pure, and unit-tested.
- Scenario tests pass before any LLM integration begins.

## 9. Phase 4: Tier 1 REST Lookup API and CLI

### Goals

Expose fast, reliable lookup capabilities through FastAPI and a CLI fallback.

### Tasks

- Implement `advisor/api/server.py` FastAPI app construction.
- Implement `advisor/api/rest_tier1.py`:
  - `POST /api/v1/query`
  - `GET /api/v1/crew/{crew_id}`
  - `GET /api/v1/flights`
  - `GET /api/v1/certifications/expiring`
- Implement a minimal deterministic intent path for common lookup queries.
- Add structured request logging with `trace_id` and `latency_ms`.
- Add CLI command path such as `advisor ask`.

### Tests

- `tests/integration/test_rest_tier1.py`

Cover:

- Happy-path lookups.
- Unknown crew, station, or flight handling.
- Response schema stability.
- Target latency checks where practical.

### Acceptance Criteria

- Tier 1 lookup endpoints answer without invoking simulation.
- REST responses are stateless and deterministic.
- Unknown or ambiguous requests degrade through typed errors or abstention responses.

## 10. Phase 5: Operational Digital Twin and WebSocket Simulation

### Goals

Build the immutable Operational Digital Twin, shadow overlays, ripple propagation, and diff stream for disrupted operations.

### Tasks

- Implement `advisor/twin/state.py`:
  - `AircraftTailState`
  - `CrewMemberState`
  - `DigitalTwinState`
  - `Overlay`
  - `OpsState`
- Implement `advisor/twin/ripple.py`:
  - sick crew disruption
  - pairing breakage
  - uncrewed leg marking
  - companion crew stranding inspection
  - aircraft tail rotation delay propagation
  - duty clock sliding
- Implement `advisor/twin/diff.py` to produce `ImpactReport`.
- Implement `advisor/twin/timeline.py` for continuous duty intervals and rolling projections.
- Implement `advisor/api/ws_tier2_3.py` with event frames:
  - `twin:forked`
  - `twin:ripple`
  - `ledger:ready`
  - `options:ranked`
  - `prose:stream`
  - `simulate:complete`
- Handle `abort` messages and WebSocket disconnect cleanup.

### Tests

- `tests/unit/test_twin_ripple.py`
- `tests/integration/test_ws_simulate.py`

Cover:

- Overlay apply and undo behavior.
- No mutation of baseline state.
- Crew pairing cascade.
- Tail rotation propagation.
- Companion stranding.
- WebSocket frame ordering.
- Cancellation cleanup.

### Acceptance Criteria

- Initial simulation delta can stream before final prose.
- Baseline database is never mutated by simulations.
- Impact reports include exact affected flights, passengers, source rows, and confidence.

## 11. Phase 6: Tier 3 Candidate Search, Costing, and Ranking

### Goals

Deliver the pragmatic MVP recommender: legal candidates, feasible deadheads, cost breakdowns, ranked options, and do-nothing benchmark.

### Tasks

- Implement `advisor/reasoning/candidates.py`:
  - on-base active reserve enumeration
  - off-base reserve enumeration
  - rest-day roster swap fallback
- Implement `advisor/reasoning/deadhead.py`:
  - scheduled-flight feasibility
  - reachability and report-time checks
  - 30-minute arrival buffer
- Implement `advisor/reasoning/costing.py`:
  - callout fee
  - overtime fee
  - deadhead fare
  - delay penalty
  - cancellation benchmark
- Implement `advisor/reasoning/ranker.py`:
  - legal options before illegal options
  - full coverage before partial coverage
  - lowest total INR cost
  - lowest disruption risk
  - do-nothing benchmark card
- Add decision half-life expiration timestamps.

### Tests

- `tests/unit/test_candidates.py`
- `tests/unit/test_deadhead.py`
- `tests/unit/test_costing.py`
- `tests/unit/test_ranker.py`

Cover:

- On-base candidate preference.
- Off-base deadhead feasibility and infeasibility.
- Cost line items sourced from `costs.json`.
- Deterministic tie-breaking.
- Do-nothing benchmark generation.
- Option expiry calculation.

### Acceptance Criteria

- No optimizer or solver dependency is introduced.
- Ranking is explainable from candidate ledgers and cost line items.
- Every option includes legality, cost, expiry, and source rows.

## 12. Phase 7: LLM Perimeter, Abstention, Slots, and Verification

### Goals

Integrate the LLM only at the approved perimeter while ensuring all facts and numbers are substituted by deterministic code.

### Tasks

- Implement `advisor/orchestrator/resolver.py` for local PII resolution and relative date resolution.
- Implement `advisor/llm/client.py` as a provider-agnostic injected interface.
- Implement `advisor/llm/parser.py` returning typed `QueryIntent` objects.
- Implement `advisor/orchestrator/abstain.py` with reasons:
  - unknown entity
  - ambiguous time
  - out of scope
  - unsupported rule
  - no legal options
- Implement `advisor/llm/renderer.py` for slot-token prose.
- Implement `advisor/orchestrator/validator.py` for token substitution and numeric validation.
- Implement deterministic fallback templates for parser or renderer failure.
- Implement `advisor/verify.py` for machine-readable answer certificates.
- Append finalized answers, verdicts, and slot resolutions to `advisor/audit/audit_log.jsonl`.

### Tests

- `tests/unit/test_slot_validator.py`
- `tests/unit/test_abstain.py`
- `tests/unit/test_verify.py`
- `tests/integration/test_llm_perimeter.py`

Cover:

- Unknown slot failure.
- Numeric hallucination prevention.
- Renderer fallback behavior.
- Parser malformed JSON fallback.
- Certificate re-execution without LLM access.
- Abstention precision on unanswerable cases.

### Acceptance Criteria

- No LLM-generated numeric value reaches a user unchanged.
- LLM calls are mocked in tests.
- Tier 2 and Tier 3 outputs include replayable verification certificates.

## 13. Phase 8: Streamlit Controller Console

### Goals

Build the controller-facing Streamlit UI around real operational workflows: lookup, simulate, inspect, compare options, and verify.

### Tasks

- Implement `ui/app.py` as a 3-pane console:
  - left: chat, actions, scenario controls
  - center: legality ledger and ranked recovery options
  - right: digital twin timeline, roster diff, and affected rotation view
- Implement `ui/components/ws_client.py` for `/ws/simulate` WebSocket handling.
- Implement `ui/components/gantt.py` for planned vs simulated state.
- Implement `ui/components/ledger.py` with verdicts, arithmetic, margins, and source rows.
- Implement `ui/components/ranked_options.py` for Tier 3 cards and do-nothing benchmark display.
- Implement `ui/components/countdown.py` for option expiry.
- Add abort/cancel interaction for active simulations.
- Ensure UI text stays operational and compact.

### Tests

- Add Streamlit UI smoke tests where practical.
- Add WebSocket client tests for frame ordering and abort behavior.
- Add integration coverage for the lookup to simulate to ranked-options workflow where practical.

### Acceptance Criteria

- The first screen is the usable controller console, not a marketing page.
- REST lookups do not block WebSocket simulation state.
- Gantt, ledger, prose, and option cards update progressively.
- Streamlit UI launches against the local FastAPI backend.
- UI tests pass without requiring external network access.

## 14. Phase 9: Evaluation, Demo, and Operational Hardening

### Goals

Verify the full system against known scenarios, unanswerable prompts, and demo workflows.

### Tasks

- Implement `eval/harness.py`.
- Add evaluation fixtures from `questions.json`, `scenarios.json`, and `unanswerable.json`.
- Verify:
  - Tier 1 lookup correctness
  - Tier 2 impact report correctness
  - Tier 3 ranker correctness
  - abstention behavior
  - certificate replay
  - audit log completeness
- Rehearse demo sequence:
  - Tier 1 reserve lookup
  - sick crew simulation
  - WebSocket Gantt diff
  - ranked replacement option
  - illegal backup explanation
  - unknown crew abstention
  - certificate verification

### Tests

- Full `pytest tests/`.
- Streamlit UI smoke run.
- Evaluation harness run.
- Manual UI demo checklist.

### Acceptance Criteria

- All tests pass.
- Evaluation harness reports deterministic outcomes.
- Demo can complete without external network dependency except optional LLM calls.
- LLM failures fall back to deterministic, correct responses.

## 15. Cross-Cutting Implementation Checklist

Use this checklist for every phase:

- Confirm module ownership from `architecture.md`.
- Define contracts before business logic.
- Add or update tests before considering logic complete.
- Keep functions small and typed.
- Avoid duplicated date, cost, SQL, and slot-substitution logic.
- Emit structured logs at external boundaries and decision checkpoints.
- Raise typed domain exceptions with actionable messages.
- Include source rows in evidence, ledgers, options, and certificates.
- Run focused tests for the touched area, then `pytest tests/` before finalizing a phase.
- Run focused UI tests after Streamlit changes.

## 16. MVP Completion Definition

The MVP is complete when the system can:

1. Ingest the synthetic dataset into SQLite with reconciliation checks.
2. Answer Tier 1 lookup questions via REST in a deterministic response shape.
3. Simulate a sick crew disruption through an immutable shadow twin.
4. Stream WebSocket frames for twin fork, ripple, ledger, ranked options, prose, and completion.
5. Evaluate all 7 rules with auditable arithmetic and margins.
6. Recommend legal reserve replacements using on-base, off-base deadhead, and fallback search.
7. Produce deterministic cost breakdowns and a do-nothing cancellation benchmark.
8. Render prose only through validated slots or deterministic fallback templates.
9. Abstain on unknown, ambiguous, unsupported, or unsafe requests.
10. Emit verification certificates that replay deterministic conclusions without an LLM.

## 17. Explicit Post-MVP Items

Defer these until the MVP is green:

- Integer programming, CP-SAT, or external optimization solvers.
- Multi-agent LLM debate loops.
- Live flight tracking integrations.
- Authentication, multi-tenancy, or user management.
- Predictive ML models.
- Slack vector repair inversion.
- Network robustness depletion scoring.
- Anticipatory pre-computation for high-risk crew.
