# Agent Engineering Instructions & Development Guidelines
**Project:** Crew Ops Advisor (Operational Digital Twin for Airline Crew Control)  
**Reference Document:** [`architecture.md`](file:///Users/vikrambabugaddam/Documents/runway%20ready/architecture.md)

---

## 1. Prime Directives for Coding Agents

1. **Deterministic Separation of Concerns:**  
   Never let an LLM execute database queries, calculate duty/rest hours, perform date arithmetic, or make arithmetic assertions. The LLM handles natural language parsing and slotted sentence presentation. Deterministic Python owns 100% of facts, joins, rules, and costs.
2. **Every Logic Addition Demands a Test:**  
   No function or business logic may be committed without an accompanying test file in `tests/`. New regulatory rules must have explicit test cases matching `scenarios.json`.
3. **Structured Logging, Zero Print Statements:**  
   Never use `print()`. Use the project-wide structured logger with explicit contextual fields (`trace_id`, `crew_id`, `flight_id`, `latency_ms`).
4. **Explicit, Typed Error Handling & Soft Degradation:**  
   Never write a bare `except:`. Catch targeted exceptions, wrap them into domain-specific exception types, log them with stack traces, and degrade gracefully. Ingest must never abort due to minor accrual reconciliation deltas.
5. **Strict Typing & Immutability:**  
   All domain models must be frozen dataclasses (`@dataclass(frozen=True)`). Full type annotations are mandatory. `OpsState` must be immutable, holding a `Path` to the database (never an open connection object) so it remains 100% hashable and thread-safe. All timestamps must be UTC ISO-8601 (`datetime` with `tzinfo=timezone.utc`).

---

## 2. Core Architectural & Design Principles

### 2.1 SOLID Principles Implementation

* **Single Responsibility Principle (SRP):**
  * Each rule module (`fdp_01.py`, `duty_02.py`, etc.) evaluates exactly one regulatory rule and nothing else.
  * The Repository layer only reads and writes from SQLite; it performs zero business constraint validation.
  * The Digital Twin (`twin/`) only models operational state and propagation; it does not format user-facing text.
  * The Orchestrator Runner (`orchestrator/runner.py`) coordinates the functional pipeline; it delegates rule logic to the rules engine.
* **Open/Closed Principle (OCP):**
  * The rules engine evaluates an extensible list of pure functions. Adding an 8th rule requires creating a new rule file and registering it in `rules/engine.py` without modifying existing rules.
  * The tool catalog in `tools/registry.py` accepts new tool signatures via a standardized decorator or registration list.
* **Liskov Substitution Principle (LSP):**
  * Every regulatory rule adheres to the uniform callable signature:
    ```python
    Callable[[Crew, DutyProposal, RuleContext], RuleVerdict]
    ```
  * Every overlay in the Digital Twin inherits from a base `Overlay` protocol and implements uniform state transformation.
* **Interface Segregation Principle (ISP):**
  * Keep tool definitions small and purpose-built (e.g., `get_crew(crew_id)` rather than a mega-query function returning the entire database).
  * Separate the read-only inspection view (`DigitalTwinState`) from the state-forking overlay engine (`OpsState`).
* **Dependency Inversion Principle (DIP):**
  * High-level orchestrators depend on abstract interfaces (`LLMClient`, `IRulesEngine`, `IOpsRepository`), not concrete vendor SDKs (e.g., OpenAI or Anthropic directly). Vendor implementations are injected at runtime.

### 2.2 Design Patterns Applied

| Pattern | Where It Is Used | Purpose |
|---|---|---|
| **Repository Pattern** | `advisor/data/repository.py` | Encapsulates all raw SQL queries behind typed Python functions. Isolates SQLite dialect and indexing details from the rest of the application. |
| **Strategy Pattern** | `advisor/rules/*.py` | Each regulatory rule is an independent strategy for constraint checking, executed uniformly by `evaluate_all()`. |
| **State / Overlay Pattern** | `advisor/domain/state.py` | The immutable `OpsState` uses an overlay stack (like Git commits) to simulate disruptions without mutating the base database. |
| **Materialized View Pattern**| `advisor/twin/view.py` | Projects `OpsState` and active overlays into an in-memory `DigitalTwinState` snapshot (`state.materialize()`) without dual-state drift. |
| **Generator / Pipeline Pattern**| `advisor/orchestrator/runner.py` | Yields execution stages (`status` $\to$ `evidence` $\to$ `options` $\to$ `prose`) in-process for progressive Streamlit UI rendering without WebSocket overhead. |
| **Template Method** | `advisor/llm/renderer.py` | Generates text using pre-validated slot tokens (`{{token.path}}`), falling back to a deterministic string template if token resolution fails. |

### 2.3 DRY, KISS & YAGNI Guardrails

* **KISS (Keep It Simple, Stupid):**
  * Do not use WebSockets or external message brokers between Streamlit and Python logic. Call the orchestrator in-process as a Python library.
  * Do not use LangGraph or complex multi-agent debate loops. A linear pipeline with one conditional gate and two LLM calls is an `if/else` function.
  * Do not introduce an integer linear programming solver (CP-SAT/Gurobi) for Tier 3 ranking. Use deterministic lexicographic sorting:
    $$\text{Legal} \succ \text{Full Coverage} \succ \text{Lowest INR Cost} \succ \text{Seniority}$$
* **DRY (Don't Repeat Yourself):**
  * Time-window slicing, date parsing, and interval overlapping must only be written once in `advisor/domain/timeutil.py`.
  * Cost calculation formulas must only reside in `advisor/reasoning/costing.py`.
  * Single binding repair calculations must only reside in `advisor/reasoning/repair.py`.
* **YAGNI (You Aren't Gonna Need It):**
  * Do not build authentication, user management, or multi-tenant database partitioning.
  * Do not integrate real-world live flight tracking APIs. The 10 synthetic JSON files are the sole dataset.
  * Do not write predictive machine-learning models; consume `risk_signals.json` directly as an input signal.

---

## 3. Mandatory Testing Strategy (TDD / Accompanying Tests)

**Rule:** Every time a new function, rule, or module is introduced, the corresponding test file must be created or updated before considering the task complete.

### 3.1 Test Organization Structure
```
tests/
├── unit/
│   ├── test_timeutil.py          # Window math, ISO-8601 parsing, rolling window overlaps
│   ├── test_repository.py        # SQL queries, parameter binding, index usage
│   ├── test_rules.py             # All 7 rules tested independently against edge cases
│   ├── test_twin_ripple.py       # Aircraft rotation delay and crew cascade logic
│   ├── test_repair.py            # Minimal repair lever inversion for binding breaches
│   └── test_slot_validator.py    # Template substitution and anti-hallucination checks
├── integration/
│   ├── test_scenarios.py         # The 6 worked disruption scenarios from scenarios.json
│   ├── test_ingest.py            # JSON schema loading and soft-reconciliation assertions
│   └── test_runner.py            # In-process generator event pipeline
└── conftest.py                   # Reusable in-memory SQLite fixtures & seed data
```

### 3.2 Testing Standards
1. **Rule Assertions against Ground Truth:**
   * Every rule module must be asserted against both passing and failing sample inputs.
   * `RuleVerdict.arithmetic` and `RuleVerdict.margin` must be explicitly verified in the test assertion.
2. **Deterministic In-Memory DB Fixture:**
   * Unit tests must run against an in-memory SQLite database (`sqlite3.connect(":memory:")`) pre-seeded with synthetic fixtures to ensure tests execute in $<1\text{ second}$.
3. **No External Network Calls in Tests:**
   * LLM calls must be mocked using `StubClient` (returning pre-canned JSON intent fixtures) to guarantee offline execution and zero test latency.

---

## 4. Structured Logging & Observability

### 4.1 Logger Setup (`advisor/audit/logger.py`)
Use a standardized Python logger outputting JSON or structured key-value pairs:

```python
import logging
import json
from datetime import datetime, timezone
from typing import Optional, Any

class StructuredLogger:
    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def info(self, message: str, **context: Any) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "INFO",
            "logger": self._logger.name,
            "message": message,
            **context
        }
        self._logger.info(json.dumps(payload))

    def error(self, message: str, error: Optional[Exception] = None, **context: Any) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "logger": self._logger.name,
            "message": message,
            "error_type": error.__class__.__name__ if error else None,
            "error_detail": str(error) if error else None,
            **context
        }
        self._logger.error(json.dumps(payload))
```

### 4.2 Logging Standards
* **Entry and Exit Points:** Log every incoming query with a unique `trace_id`.
* **Decision Checkpoints:**
  * Log when an intent is parsed (`intent`, `confidence`).
  * Log when the Abstention Gate trips (`abstain_reason`).
  * Log the execution duration of the Digital Twin projection (`latency_ms`).
* **Audit Trail (JSONL):** Append all finalized answers, rule verdicts, and slot resolutions to `advisor/audit/audit_log.jsonl`.

---

## 5. Domain Exception Hierarchy & Error Handling

Never allow unhandled server crashes. Use a unified domain exception hierarchy:

```python
# advisor/domain/exceptions.py

class OpsAdvisorError(Exception):
    """Base exception for all domain errors."""
    def __init__(self, message: str, trace_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.trace_id = trace_id

class EntityNotFoundError(OpsAdvisorError):
    """Raised when crew, flight, or pairing does not exist."""
    pass

class DataIngestionError(OpsAdvisorError):
    """Raised when JSON fixtures fail structural schema checks."""
    pass

class RuleEvaluationError(OpsAdvisorError):
    """Raised when rule inputs are malformed or missing required timeline data."""
    pass

class AbstentionTriggered(OpsAdvisorError):
    """Raised when the query is ambiguous, low-confidence, or out-of-scope."""
    def __init__(self, reason: str, message: str, trace_id: Optional[str] = None):
        super().__init__(message, trace_id)
        self.reason = reason

class SlotSubstitutionError(OpsAdvisorError):
    """Raised when LLM-rendered prose contains unknown {{slot}} tokens."""
    pass
```

### 5.1 Graceful Degradation Strategy
* **Ingestion Reconciliation Discrepancy:**
  * If derived duty hours differ from `duty_clock.json`, do NOT crash. Log a warning with the delta, flag `OpsState.clock_mode = "scalar_anchored"`, document in `assumptions.md`, and continue.
* **LLM Call Failure (Timeout or Malformed JSON):**
  * Catch `LLMError` $\to$ Log warning $\to$ Emit pre-compiled deterministic template text using data from `EvidenceBundle`. The controller still receives 100% correct data without interruption.
* **Slot Substitution Mismatch:**
  * Catch `SlotSubstitutionError` $\to$ Log token diff $\to$ Fallback to deterministic template text.

---

## 6. Coding Standards & Conventions

### 6.1 Python Version & Type Annotations
* **Target Environment:** Python 3.11+.
* **Type Safety:** 100% type hint coverage. Run `mypy --strict` with zero type errors.
* **Imports:**
  ```python
  # 1. Standard library imports
  from dataclasses import dataclass, field
  from datetime import datetime, timezone
  from pathlib import Path
  from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

  # 2. Local application imports
  from advisor.domain.types import Crew, Flight, DutyProposal
  from advisor.domain.evidence import RuleVerdict, LegalityLedger
  ```

### 6.2 Data Model Rules
* All domain entities (`Crew`, `Flight`, `Pairing`, `RuleVerdict`, `OpsState`) must be defined as frozen dataclasses:
  ```python
  @dataclass(frozen=True)
  class Crew:
      crew_id: str
      name: str
      rank: str
      base: str
      seniority: Optional[int]
      reachability_minutes: Optional[int]
  ```
* `OpsState` must never hold a live `sqlite3.Connection` object. It holds `db_path: Path`, ensuring it is hashable, serializable, and thread-safe.

### 6.3 Time Handling Discipline
* **Never use `datetime.now()` without timezone.** Always use `datetime.now(timezone.utc)`.
* **String Parsing:** Always parse using `datetime.fromisoformat()` with strict UTC normalization.
* **Display Format:** Only format to human-readable strings (e.g. `"14:00 UTC"`) at the UI presentation boundary.

---

## 7. Operational Workflow for New Features

When instructed to add a new capability:
1. **Read & Check:** Review `architecture.md` to confirm which tier and module owns the capability.
2. **Contract First:** Define the dataclass in `advisor/domain/` or SQL query in `advisor/data/repository.py`.
3. **Write the Test:** Add a test case in `tests/` covering both nominal and error/breach scenarios.
4. **Implement Cleanly:** Write the business logic following SOLID principles, without hardcoded literals.
5. **Log & Guard:** Add structured logger calls and wrap unsafe operations with typed domain exceptions.
6. **Verify:** Run `pytest tests/` to confirm zero regressions before committing.
