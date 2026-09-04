# ✈️ Crew Ops Advisor — Airline Digital Twin & Decision Support System

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.39%2B-FF4B4B.svg)](https://streamlit.io/)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-Live%20SDK-4285F4.svg)](https://ai.google.dev/)
[![Tests](https://img.shields.io/badge/tests-59%2F59%20passed-brightgreen.svg)]()
[![Evaluation](https://img.shields.io/badge/dCortex%20Benchmark-47%2F47%20(100%25)-success.svg)]()
[![DGCA CAR Sec 7](https://img.shields.io/badge/DGCA%20Compliance-Pure%20Deterministic-emerald.svg)]()

**Crew Ops Advisor** is an airline operations control (AOC) decision-support system and digital twin engine. Built for disruption management, pairing recovery, and crew reserve allocation, it delivers **100% deterministic, mathematically verifiable, and cost-ranked recovery recommendations** under Indian DGCA CAR Section 7 regulations.

---

## 📑 Table of Contents

- [Key Highlights](#-key-highlights)
- [System Architecture](#-system-architecture)
- [Project Directory Structure](#-project-directory-structure)
- [Prerequisites & Requirements](#-prerequisites--requirements)
- [Quickstart & Setup Guide](#-quickstart--setup-guide)
- [Running the Streamlit UI Console](#-running-the-streamlit-ui-console)
- [Running the CLI](#-running-the-cli)
- [Automated Testing & Verification](#-automated-testing--verification)
- [Regulatory Rules Grounding (DGCA CAR Section 7)](#-regulatory-rules-grounding-dgca-car-section-7)
- [Disruption Scenarios Evaluated](#-disruption-scenarios-evaluated)

---

## 🌟 Key Highlights

- **Pure Deterministic Rule Sovereignty**: Zero LLM hallucination in legality or costing. Every legality verdict is computed via signed arithmetic margins (`+3.4h Margin` or `-1.2h Violation`) directly against DGCA CAR Section 7.
- **Operational Digital Twin (`advisor/twin/`)**: In-memory immutable overlay stack (`OpsState`) that models disruptions, propagates aircraft tail turnaround delays, and detects cascading pairing breakdowns without corrupting baseline databases.
- **Reconciled Dual-Clock Relational Storage (`advisor/data/`)**: SQLite database (`data/ops.db`) with 12 normalized tables. Reconciles duty history clocks across all 150 crew members down to the minute (**0.0h delta / 0 mismatches**).
- **Google Gemini Reasoning & Privacy Boundary**: Powered by the modern `google-genai` SDK with strict local PII anonymization (`resolver.py`). Zero pilot names or sensitive crew identifiers are ever transmitted externally. Includes a deterministic `StubClient` for 100% air-gapped offline execution.
- **Modern AOC Controller Console (`ui/app.py`)**:
  - **🚨 Disruption & Decision Support**: Split-pane view featuring real-time pairing impact analysis, ranked candidate options, actionable minimal repair levers (`delay_departure`), and legality ledgers.
  - **👥 Standby & Reserve Operations Board**: 2-column responsive layout, fast 20-item cursor pagination, real-time KPI metrics, station/rank/fleet filters, and instant copyable callout dispatch directives.
  - **✈️ Aircraft Tail Gantt Matrix**: Interactive timeline grouped by aircraft tail (`VT-DXA`..`VT-DXF`) with flight route chips, disruption window reference lines, and an operational manifest.
  - **⚓ Static Fixed Command Footer**: Pinned permanently to the bottom of the window, allowing content to scroll smoothly behind it while the command prompt remains static and accessible at all times.
- **Cryptographic SHA-256 Audit Certificate**: Generates tamper-evident cryptographic certificates (`cert.json`) binding input state hashes, candidate rankings, and legality ledger signatures for regulatory reporting.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    User([Controller / Disruption Directive]) --> Resolver[Local PII Anonymizer & Entity Resolver]
    Resolver --> Abstain{Abstention Gatekeeper}
    
    Abstain -->|Unknown / Out of Scope| AbstainExit[Operational Abstention Banner]
    Abstain -->|Valid Directive| Twin[Operational Digital Twin & Overlay Stack]
    
    subgraph Digital Twin Core
        Repo[(SQLite Ops Repository)]
        State[Immutable State: OpsState]
        Ripple[Forward Tail Turnaround & Pairing Ripple]
        Repo <--> State
        State --> Ripple
    end
    
    Twin --> Rules[7 Pure DGCA CAR Sec 7 Rules]
    Rules --> Candidates[Candidate Discovery: On-Base, Day-Off, Deadhead]
    Candidates --> Ranker[Lexicographic Cost & Legality Ranker]
    Ranker --> Repair[Actionable Minimal Repair Engine]
    
    subgraph Output & Verification
        Cert[Cryptographic SHA-256 Certificate]
        LLM[Google Gemini LLM / Offline Stub]
        Console[Streamlit Controller Console]
    end
    
    Ranker --> Cert
    Ranker --> LLM
    LLM --> Console
    Cert --> Console
```

### Data Flow Overview
1. **User Query**: Controller inputs a natural language directive or selects a scenario preset.
2. **PII Anonymization**: `resolver.py` strips pilot names and airport codes locally, replacing them with anonymous tokens (`[CREW_A]`, `[FLIGHT_1]`).
3. **Abstention Gate**: `abstain.py` flags unknown crew IDs, out-of-scope requests (e.g. hotel booking/baggage vouchers), or ambiguous relative times (`afternoon`).
4. **Digital Twin Overlay**: `OpsState` applies the disruption as an overlay on top of the SQLite baseline. `ripple.py` computes delayed turnaround cascades and uncrewed sectors.
5. **Legality & Candidate Discovery**: Enumerates available standby reserves, off-duty crew, and deadhead options across stations, checking all 7 DGCA rules.
6. **Costing & Ranking**: Computes published compensation rates (Reserves: ₹18,500, Day-Off: ₹24,000, Cancellations: ₹250,000) and sorts candidates lexicographically (Legal > Lowest Cost > Earliest Available).
7. **Presentation & Audit**: Emits the structured result to the Streamlit UI and generates an audit certificate with SHA-256 signatures.

---

## 📂 Project Directory Structure

```text
runway-ready/
├── advisor/                        # Core Engine Package
│   ├── audit/                      # Cryptographic certificates & structured logging
│   │   ├── certificate.py          # SHA-256 tamper-evident certificate generator
│   │   └── logger.py               # Standardized JSON structured logger
│   ├── data/                       # Ingestion & Database
│   │   ├── ingest.py               # JSON to SQLite reconciliation engine
│   │   ├── repository.py           # Typed SQLite repository interface
│   │   └── schema.sql              # 12-table relational DDL schema
│   ├── domain/                     # Domain models, exceptions, and time utilities
│   │   ├── evidence.py             # RecoveryOption, LegalityLedger, CostBreakdown
│   │   ├── models.py               # Crew, Flight, Pairing, Rotation, Reserve models
│   │   ├── state.py                # Immutable OpsState & Overlay stack
│   │   └── timeutil.py             # ISO-8601 UTC arithmetic & window parsers
│   ├── llm/                        # Language model gateways & client abstraction
│   │   ├── client.py               # Google Gemini client & deterministic StubClient
│   │   ├── parser.py               # Intent extraction & slot resolution
│   │   └── renderer.py             # Slotted prose template renderer
│   ├── orchestrator/               # Pipeline execution & privacy guards
│   │   ├── abstain.py              # Out-of-scope & unknown entity guardrails
│   │   ├── resolver.py             # Local PII scrub & bidirectional token map
│   │   └── runner.py               # In-process generator stage pipeline
│   ├── reasoning/                  # Regulatory optimization & candidate ranking
│   │   ├── candidates.py           # Standby reserve & callout discovery
│   │   ├── costing.py              # Airline disruption costing models
│   │   ├── deadhead.py             # Positioning flight route finder
│   │   ├── ranker.py               # Lexicographic option comparator
│   │   └── repair.py               # Minimal actionable levers (e.g. delay_departure)
│   ├── rules/                      # Pure DGCA Regulatory Rulebook
│   │   ├── engine.py               # Multi-rule evaluation dispatcher
│   │   └── pure_rules.py           # 7 pure DGCA CAR Section 7 algorithms
│   └── cli.py                      # Interactive terminal CLI tool
├── crew-ops-advisor-dataset/       # Official dCortex benchmark datasets
│   └── data/                       # flights.json, crew.json, rosters.json, etc.
├── data/                           # Reconciled SQLite operational database (ops.db)
├── eval/                           # Automated Benchmark Evaluation Suite
│   ├── harness.py                  # 38 questions + 6 scenarios + 3 abstentions
│   └── test_queries.py             # Benchmark query fixtures
├── tests/                          # Automated Pytest Suite (59 tests)
│   ├── integration/                # Ingestion, runner, and scenario tests
│   └── unit/                       # Rule limits, twin ripples, costing, LLM client
├── ui/                             # Streamlit AOC Controller Console
│   ├── app.py                      # Main entrypoint & 3-tab layout
│   └── components/                 # Modular UI components
│       ├── cards.py                # Ranked candidate option cards
│       ├── gantt.py                # Plotly aircraft rotation Gantt matrix
│       ├── ledger.py               # Regulatory pass/fail ledger table
│       └── reserves.py             # Standby & reserve operations board
├── .env.example                    # Environment template for Gemini API key
├── requirements.txt                # Pinned production dependencies
└── README.md                       # Documentation & setup guide (this file)
```

---

## ⚙️ Prerequisites & Requirements

- **Operating System**: macOS, Linux, or Windows (WSL recommended)
- **Python**: Python 3.11, 3.12, or 3.13
- **Hardware**: Runs lightweight and completely in-process (standard laptop CPU, < 500MB RAM)
- **Google Gemini API Key** *(Optional)*: Required for live generative responses. If omitted, the system seamlessly operates in deterministic offline sandbox mode via `StubClient`.

---

## 🚀 Quickstart & Setup Guide

### 1. Clone & Navigate to Workspace
```bash
git clone <repository-url>
cd "runway ready"
```

### 2. Set Up a Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```
*(On Windows: `venv\Scripts\activate`)*

### 3. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` to supply your Google Gemini API key:
```ini
# Google Gemini API Key (from https://aistudio.google.com/)
GEMINI_API_KEY=your_gemini_api_key_here

# Model Selection (Default: gemini-2.5-flash)
GEMINI_MODEL=gemini-2.5-flash

# Logging Level
LOG_LEVEL=INFO
```

> [!NOTE]
> If `GEMINI_API_KEY` is not provided, the application will automatically fall back to **Offline Deterministic Stub Mode**. The full rule engine, digital twin, and evaluation harness will still function with 100% test passing accuracy.

### 5. Initialize the Digital Twin Database
The database will be automatically created on first launch, or you can build it explicitly:
```bash
python3 -c "from advisor.data.ingest import build_database; build_database()"
```
This reconciles all raw JSON data into SQLite at `data/ops.db`.

---

## 🖥️ Running the Streamlit UI Console

Start the interactive operations cockpit:
```bash
streamlit run ui/app.py
```
Or run headlessly on a custom port:
```bash
python3 -m streamlit run ui/app.py --server.port 8501 --server.headless true
```
Open your browser to: **`http://localhost:8501`**

### Console Navigation:
- **🚨 Disruption & Decision Support**: Execute natural language queries, inspect ranked replacement options, review signed arithmetic margins, and download audit certificates.
- **👥 Standby & Reserve Roster**: Full 2-column standby cards, filterable by station base (`BLR`, `DEL`, `BOM`, `HYD`, `MAA`), rank (`Captain`, `First Officer`, `Cabin Crew`), or fleet, with fast 20-item cursor pagination.
- **✈️ Fleet & Aircraft Rotation Monitor**: Interactive Plotly Gantt matrix showing tail rotations (`VT-DXA` to `VT-DXF`) across time with flight status indicators.
- **⚓ Static Fixed Footer**: Pinned prompt bar at the bottom of the window for entering disruption directives without scrolling.

---

## ⌨️ Running the CLI

You can execute queries directly from the command line:

```bash
# Query reserve availability at BLR
python3 -m advisor.cli ask "Who is on reserve at BLR tomorrow?"

# Simulate a captain sick callout
python3 -m advisor.cli ask "Captain A. Nair is sick for flight DX412 tomorrow. What is the impact and who is the recommended replacement?"

# Test safety abstention on unknown crew
python3 -m advisor.cli ask "Is Captain C-9999 available to fly flight DX412?"
```

---

## 🧪 Automated Testing & Verification

### A. Run Unit & Integration Tests (Pytest)
Executes all 59 tests covering ingestion, twin ripples, DGCA rules, candidate discovery, costing, and Gemini LLM client:
```bash
python3 -m pytest tests/
```
**Result**: `59 passed in ~2.5s`

### B. Run Official dCortex Evaluation Harness
Runs the automated evaluation benchmark across **38 official benchmark questions**, **6 flagship disruption scenarios**, and **3 safety abstention gates**:
```bash
python3 eval/harness.py
```
**Target Output**:
```text
======================================================================
🚀 Crew Ops Advisor — Automated Evaluation Harness
======================================================================
[+] Evaluating 38 Official dCortex Benchmark Questions:
  ✅ [PASS] (Q01 to Q38) ...
[+] Evaluating 6 Flagship Operational Disruption Scenarios:
  ✅ [PASS] (S1 to S6) ...
[+] Evaluating 3 Abstention Gate Queries:
  ✅ [PASS] (u1 to u3) ...
======================================================================
📊 Final Evaluation Score: 47/47 Passed (100.0%)
======================================================================
```

---

## 📜 Regulatory Rules Grounding (DGCA CAR Section 7)

All calculations are pure, deterministic Python algorithms strictly conforming to the Directorate General of Civil Aviation (DGCA) Civil Aviation Requirements:

| Rule Code | Regulation Area | Mathematical Constraint | Implementation Logic |
| :--- | :--- | :--- | :--- |
| **`RULE-FDP-01`** | Max Daily Flight Duty Period | $\text{FDP} \le \text{Max}(\text{sectors}, \text{start\_utc})$ | Table-based limits: 1–2 sectors: 12.5h; 3–4 sectors: 11h; 5–6 sectors: 10h. Reduced for night operations (00:00–06:00 local). |
| **`RULE-DUTY-02`** | Cumulative Duty Time | 7 Days $\le 60.0\text{h}$; 28 Days $\le 190.0\text{h}$ | Sums historical clock + proposed pairing duty. Reconciled with dual-clock verification. |
| **`RULE-FLT-03`** | Cumulative Flight Block Time | 7D $\le 35\text{h}$; 28D $\le 100\text{h}$; 365D $\le 1000\text{h}$ | Tracks block-to-block flight hours against annual and monthly ceilings. |
| **`RULE-REST-04`** | Mandatory Rest Periods | $\text{Rest} \ge \text{Max}(12\text{h}, \text{Preceding Duty})$ | Ensures crew rest elapsed before first sector equals or exceeds previous duty duration. |
| **`RULE-QUAL-05`** | Aircraft Fleet Ratings | Crew Type Rating $\supseteq$ Aircraft Fleet | Enforces strict fleet compatibility (`A320` vs `ATR72`). No cross-fleet flying allowed. |
| **`RULE-CERT-06`** | Mandatory Certifications | $\text{Expiry} \ge \text{Sector STA}$ | Validates Medical, Recurrent Training, Line Check, and Instrument Rating against flight dates. |
| **`RULE-BASE-07`** | Crew Base Compatibility | Base Station = Sector Departure Station | Requires crew home base to match flight origin, or injects deadhead positioning flights. |

---

## 🚨 Disruption Scenarios Evaluated

The system is tested and benchmarked against real-world airline disruption events:

1. **S1 (ATR Captain Sick Call)**: Replaces Captain on ATR72 network with lowest-cost on-base reserve (`C-3315`).
2. **S2 (Flagship 2-Day Disruption)**: Resolves multi-day pairing `P-2291` when Captain A. Nair (`C-1042`) calls in sick. Evaluates 4 candidate options, recommending `C-3310` (₹18,500) and preventing a ₹250,000 cancellation penalty.
3. **S3 (Station Closure)**: Simulates BLR airport runway closure (08:00–14:00Z on Sep 17), computing schedule cascade delays and re-rostering uncrewed flights.
4. **S4 (Technical Cascading Delay)**: 90-minute delay on `VT-DXA` propagating into downstream flights, automatically proposing a `delay_departure` repair lever to maintain minimum legal crew rest.
5. **S5 (Discovered Certification Lapse)**: Flags expired pilot recurrent training pre-flight and dispatches replacement reserve `C-2248`.
6. **S6 (Simultaneous Double Callout)**: Simultaneously resolves dual sick calls on `VT-DXA` and `VT-DXB` without creating schedule conflicts or double-booking reserves.

---

## 🔒 Security, Privacy & Auditability

- **Zero External PII Leakage**: Crew names, contact information, and home addresses are never sent to external LLMs.
- **Air-Gapped Ready**: Operates completely offline with zero external network access required.
- **Cryptographic Audit Trail**: Every decision generates a SHA-256 digital certificate containing the input state hash, rule signatures, and candidate rankings, ensuring complete transparency for civil aviation auditors.
