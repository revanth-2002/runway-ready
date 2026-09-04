# PII Protection & India DPDP Act 2023 Compliance

**Project:** Crew Ops Advisor  
**Reference Specification:** [`architecture.md`](file:///Users/vikrambabugaddam/Documents/runway%20ready/architecture.md)

---

## 1. Statutory Context & Principles

Under the **Digital Personal Data Protection (DPDP) Act 2023** (India):
1. **Data Fiduciary Obligations:** The airline and its operational systems must process identifiable crew personal data (names, employee IDs, medical certifications, rest locations) solely for operational crew scheduling purposes.
2. **Data Minimization:** No personal identifiers (pilot/FO names) should ever be transmitted to external, non-sovereign third-party LLM cloud APIs (OpenAI, Anthropic, Gemini cloud endpoints).
3. **Purpose Limitation:** Anonymized tokens (`C-1042`) protect individual identity while retaining exact referential integrity for deterministic graph and rule execution.

---

## 2. Local PII Sanitization Perimeter (`advisor/orchestrator/resolver.py`)

All incoming natural language queries from controllers pass through a local, deterministic pre-parser:

```
[Controller Query] ──► [Local Fuzzy Matcher] ──► [Anonymized Query] ──► [External LLM API]
"Capt A. Nair is sick"                           "Crew C-1042 is sick"
```

* **Lookup Mechanics:**
  * Uses exact and token-fuzzy matching against the local SQLite `crew` table (`name` and `crew_id`).
  * Normalizes titles: `"Capt Nair"`, `"Captain A. Nair"`, `"Nair"` $\to$ `C-1042`.
  * Creates an ephemeral reverse lookup map in memory: `{"C-1042": "Captain A. Nair"}`.

---

## 3. UI Presentation Re-hydration

* External LLMs only receive queries containing anonymized IDs (`C-1042`).
* When generating slotted response prose:
  ```text
  Captain {{impact.crew_id}} is incapacitated for {{impact.date}}...
  ```
* The deterministic slot substitutor re-hydrates the human-readable name at the UI presentation boundary (`ui/app.py`), ensuring that sensitive personal names never leave the client device or internal network boundary.
