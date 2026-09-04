# Operational Assumptions & Regulatory Interpretations

**Project:** Crew Ops Advisor  
**Reference Specification:** [`architecture.md`](file:///Users/vikrambabugaddam/Documents/runway%20ready/architecture.md)

---

## 1. Regulatory Rolling Window Calculations

### 1.1 RULE-DUTY-02 (Max 60.0h Duty in 7 Days)
* **Window Anchor:** The 7-day (168-hour) window is end-anchored at the scheduled completion timestamp of the proposed duty period:
  $$[T_{\text{proposed\_end}} - 168\text{h}, T_{\text{proposed\_end}}]$$
* **Fractional Midnight & Boundary Straddling:** When a historical duty period starts prior to the window start ($T_w = T_{\text{proposed\_end}} - 168\text{h}$) and ends inside the window, only the minutes strictly inside the 168-hour window are counted:
  $$\text{EffectiveDutyMinutes} = \min(duty.end\_utc, T_{\text{window\_end}}) - \max(duty.start\_utc, T_{\text{window\_start}})$$
* **Discrete Accumulation:** Historical duties are retrieved from discrete `duty` records derived from pairings.

### 1.2 RULE-FLT-03 (Max 100.0h Flight Hours in 28 Days)
* **Window Anchor:** The 28-day (672-hour) window is end-anchored at the scheduled arrival of the proposed duty's last flight leg.
* **Deadhead Exclusion:** Deadhead positioning sectors are strictly excluded from flight block hours. They are logged as duty time (contributing to FDP and 7-day duty) but contribute 0.0 hours to the 28-day cumulative flight limit.

### 1.3 RULE-FDP-01 (Max Flight Duty Period)
* **Sector Count:** Sectors count only flight legs operated within *this single duty period* (grouped by `pairing_leg.duty_id` or contiguous flight blocks separated by $<4\text{h}$ rest), not across the entire multi-day pairing.
* **Deadhead Impact:** Positioning legs operated as a passenger/deadhead do not increase the operating sector penalty on FDP caps.

### 1.4 RULE-REST-04 (Min 12.0h Rest Before Duty)
* **Anchor Point:** Compares the start of the proposed duty against the end of the previous duty period:
  $$\Delta t_{\text{rest}} = T_{\text{proposed\_duty\_start}} - T_{\text{previous\_duty\_end}} \ge 12.0\text{ hours}$$
* **Fallback / Standby:** If no prior duty exists within 48 hours, `duty_clock.last_rest_ended` is inspected. If a crew member is currently on active duty, the signed rest margin is negative.

### 1.5 RULE-QUAL-05 & RULE-CERT-06 (Ratings & Certifications)
* **Rating Match:** Exact case-insensitive string match on aircraft family (e.g. `"A320"` matches `"A320"`).
* **Certification Requirement:** Every crew member must have valid certifications covering the entire duty window ($expires\_on \ge duty.date$). Missing certification records are treated strictly as **FAIL** (never assumed valid).

---

## 2. Ingest & Accrual Reconciliation Strategy

* **Dual-Clock Reconciliation:** Historical duty is derived from historical pairings and flight records. The derived 7-day sum is compared against `duty_clock.duty_hours_7d`.
* **Soft Fallback Policy:**
  * If $|\text{derived} - \text{scalar}| \le 0.2\text{ hours}$, status is `VERIFIED`.
  * If the discrepancy exceeds $0.2\text{ hours}$, ingest logs a structured warning containing the exact delta and sets `OpsState.clock_mode = "scalar_anchored"`. Startup never aborts, preventing demo-day downtime.

---

## 3. Candidate Search & Deadhead Feasibility (Tier 3)

* **Search Hierarchy:**
  1. On-base active reserves (`reserve.base == flight.origin`).
  2. Off-base reserves with schedule-feasible deadhead passenger flights.
  3. Rest-day roster swaps (unassigned crew at station with $\ge 12\text{h}$ rest buffer).
* **Feasible Deadhead Constraints:**
  * Passenger flight must exist in `flight` table connecting reserve base to duty origin.
  * Departure time: $T_{\text{deadhead\_dep}} \ge T_{\text{now}} + reachability\_minutes$.
  * Arrival time with buffer: $T_{\text{deadhead\_arr}} + 30\text{ minutes} \le T_{\text{report\_time}}$.

---

## 4. Line-Item Costing Rates & Benchmarks

Loaded dynamically from `costs.json` (with standard INR fallback rates):
* Reserve Callout Base Fee: ₹15,000 per callout.
* Crew Overtime: ₹1,400 per hour for duty beyond 8 hours.
* Deadhead Flight Fare: ₹8,500 – ₹22,700 depending on route distance / standard passenger ticket fare.
* Flight Delay Penalty: ₹1,200 per minute of departure delay.
* Cancellation Benchmark (Do Nothing): ₹180,000 fixed penalty + ₹3,000 per stranded passenger compensation.
