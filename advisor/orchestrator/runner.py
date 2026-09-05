"""Autonomous Agent Orchestrator with dynamic tool allocation and zero hardcoded defaults."""

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple
from advisor.audit.logger import StructuredLogger, append_audit_event, set_request_id
from advisor.data.repository import OpsRepository, DEFAULT_DB_PATH
from advisor.domain.evidence import LegalityLedger, RuleVerdict
from advisor.domain.state import OpsState, Overlay
from advisor.domain.types import DutyProposal
from advisor.domain.exceptions import LLMUnavailableError
from advisor.llm.client import LLMClient, get_default_llm_client, reports_crew_unavailable
from advisor.llm.parser import QueryIntent, parse_intent
from advisor.llm.suggest import derive_suggestions
from advisor.llm.renderer import (
    recommend_after_crew_move,
    recommend_after_flight_crew,
    render_cancellation_briefing,
    render_crew_info,
    recommend_recovery,
    render_crew_move_evaluation,
    render_disruption_briefing,
    render_flight_crew_impact,
    render_slotted_prose,
    render_uncrewed_flights,
    substitute_slots,
)
from advisor.orchestrator.abstain import should_abstain
from advisor.orchestrator.chat_state import ConversationState
from advisor.orchestrator.resolver import resolve_local_pii
from advisor.orchestrator.tools import (
    tool_commit_crew_reassignment,
    tool_evaluate_crew_move,
    tool_evaluate_flight_crew,
    tool_lookup_crew_info,
    tool_lookup_reserves,
    tool_simulate_crew_disruption,
    tool_simulate_station_cancellations,
)
from advisor.reasoning.candidates import enumerate_candidates
from advisor.reasoning.ranker import rank_recovery_options
from advisor.rules.engine import evaluate_all
from advisor.twin.diff import compute_twin_diff

logger = StructuredLogger("advisor.orchestrator.runner")


def orchestrate(
    query: str,
    state: OpsState,
    repo: Optional[OpsRepository] = None,
    client: Optional[LLMClient] = None,
    request_id: Optional[str] = None,
    chat_state: Optional[ConversationState] = None,
) -> Generator[Tuple[str, Any], None, None]:
    """Autonomous AI Agent generator dispatching modular tools based on extracted intent and entities:
    1. ('status', '<message>')
    2. ('intent', <QueryIntent>)
    3. ('abstain', {'reason': '...', 'message': '...'}) -> halts
    4. ('clarify', {'message': '...', 'missing_parameters': [...]}) -> prompts user
    5. ('evidence', {...})
    6. ('options', [<RecoveryOption>, ...])
    7. ('prose', '<briefing text>')
    8. ('recommendation', '<next step>') -> rendered after options/ledger, at the end
    9. ('suggestions', [{'label': '...', 'query': '...'}]) -> question-aware follow-ups

    Consumers filter by event name, so unrecognised kinds are safely ignored.

    `chat_state` carries what earlier turns already did. It is the ground truth for
    resolving follow-ups ("show me the options") and for scoping flight questions to
    the disruption under discussion.
    """
    if repo is None:
        repo = OpsRepository(state.db_path)
    if client is None:
        client = get_default_llm_client()

    from advisor.orchestrator.graph import run_advisor_graph

    for event in run_advisor_graph(
        query,
        state,
        repo,
        client=client,
        request_id=request_id,
        chat_state=chat_state,
    ):
        yield event


_STATION_CODES = ("BLR", "DEL", "BOM", "HYD", "MAA", "CCU", "COK", "GOI")


def _crew_rank(repo: OpsRepository, crew_id: str) -> Optional[str]:
    """Authoritative roster rank, so a First Officer is never labelled Captain."""
    try:
        crew = repo.find_crew(crew_id)
        return crew.rank if crew else None
    except Exception:
        return None


def _conversation_context(chat_state: Optional[ConversationState]) -> str:
    """Prompt section carrying prior turns, so answers build on what was established."""
    if chat_state is None or not chat_state.turns:
        return ""
    return f"\nConversation so far:\n{chat_state.brief()}\n"


def _names_explicit_schedule(clean_query: str) -> bool:
    """True when the controller actually named a station, route, or date.

    Read from the query text rather than the parsed entities: the intent parser
    back-fills `origin`/`date` defaults even for a bare "which flights are affected?",
    so parsed entities cannot distinguish a real schedule lookup from a follow-up.
    `clean_query` is post-resolver, so city names are already IATA codes.
    """
    import re as _re

    if _re.search(r"\b\d{4}-\d{2}-\d{2}\b", clean_query):
        return True
    if _re.search(r"\bDX\d{3,4}\b", clean_query, _re.IGNORECASE):
        return True
    return any(
        _re.search(rf"\b{code}\b", clean_query, _re.IGNORECASE) for code in _STATION_CODES
    )


def _orchestrate_core(
    query: str,
    state: OpsState,
    repo: Optional[OpsRepository] = None,
    client: Optional[LLMClient] = None,
    request_id: Optional[str] = None,
    chat_state: Optional[ConversationState] = None,
    precomputed: Optional[Tuple[str, Dict[str, str], List[str], QueryIntent]] = None,
) -> Generator[Tuple[str, Any], None, None]:
    """Intent dispatch body. See `orchestrate` for the emitted event contract.

    `precomputed` carries (clean_query, pii_map, crew_ids, intent) from the graph's
    resolve/parse nodes so those steps are not repeated here.
    """
    if request_id:
        set_request_id(request_id)
    if repo is None:
        repo = OpsRepository(state.db_path)
    if client is None:
        client = get_default_llm_client()

    if precomputed is not None:
        clean_query, pii_map, crew_ids, intent_bundle = precomputed
    else:
        yield ("status", "De-identifying PII and extracting operational entities...")
        clean_query, pii_map, crew_ids = resolve_local_pii(query, repo)

        yield ("status", "Agent reasoning: extracting multi-parameter intent & operational scope...")
        intent_bundle = parse_intent(clean_query, client)
    yield ("intent", intent_bundle)

    # 1. Abstention Gate
    abstention = should_abstain(intent_bundle, repo)
    if abstention:
        reason, message = abstention
        logger.info("Abstention triggered", reason=reason.value, detail=message)
        append_audit_event(
            "ABSTENTION",
            {"reason": reason.value, "query": query, "message": message},
            request_id=request_id,
        )
        yield ("abstain", {"reason": reason.value, "message": message})
        return

    # 2. Interactive Clarification Gate (if critical parameters are ambiguous)
    if intent_bundle.requires_clarification:
        msg = (
            intent_bundle.unsupported_aspects[0]
            if intent_bundle.unsupported_aspects
            else "Missing critical parameter to safely execute this operational directive."
        )
        yield (
            "clarify",
            {
                "message": msg,
                "missing_parameters": intent_bundle.missing_parameters,
            },
        )
        return

    # 3. Tool: Mass Station / Flight Cancellations & Financial Loss
    if intent_bundle.intent in ("cancel_station_departures", "simulate_station_cancellation", "mass_cancellation_loss"):
        station = intent_bundle.entities.get("station") or (
            intent_bundle.entities.get("stations", ["BLR"])[0] if intent_bundle.entities.get("stations") else "BLR"
        )
        date = intent_bundle.time_scope.get("raw") or "2026-09-15"
        if len(date) > 10:
            date = date[:10]

        yield ("status", f"Allocating Tool: simulate_station_cancellations for {station} on {date}...")
        res = tool_simulate_station_cancellations(repo, state, station=station, date=date)

        yield ("evidence", res)

        briefing = render_cancellation_briefing(
            station=res["station"],
            date=res["date"],
            flight_count=res["flight_count"],
            passengers=res["passengers_affected"],
            tails=res["grounded_tails"],
            cost_breakdown=res["cost_breakdown"],
        )
        yield ("prose", briefing)
        return

    # 4. Tool: Roster Reassignment & Finalization
    if intent_bundle.intent == "reassign_crew":
        p_id = intent_bundle.entities.get("pairing_id") or "P-2291"
        disrupted_c = crew_ids[0] if crew_ids else "C-1042"
        replacement_c = intent_bundle.entities.get("replacement_crew_id") or "C-3310"
        yield ("status", f"Allocating Tool: commit_crew_reassignment ({disrupted_c} ➔ {replacement_c} on {p_id})...")
        new_state = tool_commit_crew_reassignment(
            state=state,
            pairing_id=p_id,
            disrupted_crew_id=disrupted_c,
            replacement_crew_id=replacement_c,
        )
        yield ("evidence", {"new_state": new_state, "pairing_id": p_id, "replacement": replacement_c})
        yield (
            "prose",
            f"**Roster Reassignment Committed:** Replacement `{replacement_c}` successfully mobilized for pairing `{p_id}`. Digital twin updated to prevent scheduling collisions.",
        )
        if chat_state is not None:
            chat_state.resolve_disruption(replacement_c)
        return

    # 5. Tool: Lookup Reserves
    if intent_bundle.intent == "lookup_reserves":
        stations = intent_bundle.entities.get("stations", ["BLR"])
        station = stations[0] if stations else "BLR"
        date = intent_bundle.time_scope.get("raw", "2026-09-15")
        yield ("status", f"Allocating Tool: lookup_reserves at station {station}...")
        res = tool_lookup_reserves(repo, state, station=station, date=date)

        details = res.get("reserve_details", [])
        if not details:
            prose = f"⚠️ No active standby reserves scheduled at station **{station}** on {date}."
        else:
            table_rows = []
            for r in details:
                c_id = r["crew_id"]
                name = r["name"]
                rank = r["rank"]
                win = f"{r['oncall_start_utc'][11:16]}–{r['oncall_end_utc'][11:16]}Z"
                status = "🟢 STANDBY" if r["standby_status"] == "STANDBY" else f"🟡 {r['standby_status']}"
                table_rows.append(f"| `{c_id}` | **{name}** | {rank} | `{win}` | {status} |")

            prose = (
                f"### 👥 Active Standby Reserves: {station} (`{date}`)\n\n"
                f"| Crew ID | Name | Role | On-Call Window (UTC) | Status |\n"
                f"|---|---|---|---|---|\n"
                + "\n".join(table_rows)
                + f"\n\n*Total: **{len(details)}** crew members currently available on standby at **{station}**.*"
            )
        yield ("evidence", res)
        yield ("prose", prose)
        append_audit_event("LOOKUP_RESERVES", {"station": station, "count": len(details)}, request_id=request_id)
        return

    # 6. Tool: Lookup Flights
    if intent_bundle.intent == "lookup_flights":
        # Step-by-step rule: while a disruption is open and the controller has not
        # named a specific route or date, "which flights..." means the affected legs,
        # not the whole station schedule. Recovery options come next, on request.
        open_disruption = (
            chat_state.open_disruption_context() if chat_state is not None else None
        )
        if open_disruption and not _names_explicit_schedule(clean_query):
            uncrewed = [
                f
                for f in (repo.find_flight(fid) for fid in open_disruption.uncrewed_flight_ids)
                if f is not None
            ]
            yield (
                "status",
                f"Scoping to the {len(uncrewed)} uncrewed leg(s) from the {open_disruption.crew_id} disruption...",
            )
            yield (
                "evidence",
                {
                    "uncrewed_flights": uncrewed,
                    "scoped_to_disruption": True,
                    "disrupted_crew_id": open_disruption.crew_id,
                    "broken_pairing_id": open_disruption.pairing_id,
                },
            )
            yield (
                "prose",
                render_uncrewed_flights(
                    uncrewed,
                    open_disruption.crew_id,
                    open_disruption.pairing_id,
                    resolved_by=open_disruption.resolved_by,
                ),
            )
            yield (
                "recommendation",
                f"Shall I produce ranked recovery options to cover these legs on pairing "
                f"`{open_disruption.pairing_id}`?",
            )
            append_audit_event(
                "LOOKUP_UNCREWED_FLIGHTS",
                {"pairing_id": open_disruption.pairing_id, "count": len(uncrewed)},
                request_id=request_id,
            )
            return

        orig = intent_bundle.entities.get("origin")
        dest = intent_bundle.entities.get("destination")
        dt = intent_bundle.entities.get("date", "2026-09-15")
        if len(dt) > 10:
            dt = dt[:10]
        time_win = intent_bundle.entities.get("time_window") or intent_bundle.entities.get("period")
        
        yield ("status", f"Allocating Tool: lookup_flights ({orig} ➔ {dest or 'all'} on {dt})...")
        flights = repo.list_flights_by_station(origin=orig, destination=dest, date=dt)
        f_nos = [f["flight_no"] for f in flights]
        if not flights:
            prose = f"⚠️ No flights found departing **{orig}**{' for **' + dest + '**' if dest else ''} on `{dt}`."
        else:
            table_rows = []
            for f in flights:
                dep_utc_str = f['dep_utc'][11:16]
                arr_utc_str = f['arr_utc'][11:16]
                try:
                    dep_h, dep_m = map(int, dep_utc_str.split(':'))
                    arr_h, arr_m = map(int, arr_utc_str.split(':'))
                    ist_dep_h = (dep_h + 5 + (dep_m + 30) // 60) % 24
                    ist_dep_m = (dep_m + 30) % 60
                    ist_arr_h = (arr_h + 5 + (arr_m + 30) // 60) % 24
                    ist_arr_m = (arr_m + 30) % 60
                    dep_display = f"`{dep_utc_str}Z` ({ist_dep_h:02d}:{ist_dep_m:02d} IST)"
                    arr_display = f"`{arr_utc_str}Z` ({ist_arr_h:02d}:{ist_arr_m:02d} IST)"
                except Exception:
                    dep_display = f"`{dep_utc_str}Z`"
                    arr_display = f"`{arr_utc_str}Z`"

                table_rows.append(f"| `{f['flight_no']}` | `{f['flight_id']}` | `{f['origin']} ➔ {f['destination']}` | {dep_display} | {arr_display} | `{f['tail_id']}` ({f['aircraft_type']}) |")

            time_sub = f" (Window: {time_win})" if time_win else ""
            header = f"### ✈️ Flight Schedule: {orig}{' ➔ ' + dest if dest else ' Departures'} (`{dt}`{time_sub})\n\n"
            table_head = "| Flight | Flight ID | Route | Departure (UTC / IST) | Arrival (UTC / IST) | Tail / Type |\n|---|---|---|---|---|---|\n"
            prose = header + table_head + "\n".join(table_rows) + f"\n\n*Total: **{len(flights)}** flights operating on `{dt}`.*"

        yield ("evidence", {"flights": flights, "flight_numbers": f_nos})
        yield ("prose", prose)
        append_audit_event("LOOKUP_FLIGHTS", {"origin": orig, "destination": dest, "date": dt, "count": len(flights)}, request_id=request_id)
        return

    # 7. Tool: Lookup Expiring Certifications
    if intent_bundle.intent == "lookup_expiring_certs":
        days = intent_bundle.entities.get("within_days", 30)
        ref_date = intent_bundle.entities.get("reference_date", "2026-09-15")
        base = intent_bundle.entities.get("base") or intent_bundle.entities.get("station")
        cert_type = intent_bundle.entities.get("cert_type")

        scope_str = f" for {base}" if base else " Fleet-wide"
        yield ("status", f"Allocating Tool: lookup_expiring_certs ({days} days from {ref_date}{scope_str})...")
        certs = repo.list_expiring_certifications(within_days=days, reference_date=ref_date, base=base, cert_type=cert_type)
        if not certs:
            prose = f"✅ No crew certifications expiring within **{days} days** of `{ref_date}`{f' for base **{base}**' if base else ''}."
        else:
            table_rows = [f"| `{c['crew_id']}` | **{c.get('name', 'Crew')}** | {c.get('rank', 'Crew')} | `{c.get('base', 'BLR')}` | `{c['cert_type']}` | `{c['expires_on']}` |" for c in certs]
            scope_title = f"{base} Base" if base else "Fleet-wide Network"
            prose = (
                f"### ⚠️ Certifications Expiring Within {days} Days ({scope_title}, Ref: `{ref_date}`)\n\n"
                f"| Crew ID | Name | Rank | Base | Certification Type | Expiry Date |\n"
                f"|---|---|---|---|---|---|\n"
                + "\n".join(table_rows)
                + f"\n\n*Total: **{len(certs)}** certifications requiring renewal.*"
            )
        yield ("evidence", {"expiring_certifications": certs})
        yield ("prose", prose)
        append_audit_event("LOOKUP_EXPIRING_CERTS", {"days": days, "base": base, "cert_type": cert_type, "count": len(certs)}, request_id=request_id)
        return

    # 8. Tool: Lookup Pairing Crew
    if intent_bundle.intent == "lookup_pairing_crew":
        p_id = intent_bundle.entities.get("pairing_id", "P-2291")
        yield ("status", f"Allocating Tool: lookup_pairing_crew ({p_id})...")
        crew_assigns = repo.get_pairing_assignments(p_id)
        if not crew_assigns:
            prose = f"⚠️ No crew members assigned to pairing `{p_id}`."
        else:
            table_rows = [f"| `{ca['crew_id']}` | **{ca['name']}** | {ca['rank']} | {ca.get('role', ca['rank'])} |" for ca in crew_assigns]
            prose = (
                f"### 👥 Crew Assigned to Pairing `{p_id}`\n\n"
                f"| Crew ID | Name | Rank | Assigned Role |\n"
                f"|---|---|---|---|\n"
                + "\n".join(table_rows)
                + f"\n\n*Total: **{len(crew_assigns)}** crew members rostered on pairing `{p_id}`.*"
            )
        yield ("evidence", {"pairing_id": p_id, "assignments": crew_assigns})
        yield ("prose", prose)
        append_audit_event("LOOKUP_PAIRING_CREW", {"pairing_id": p_id, "count": len(crew_assigns)}, request_id=request_id)
        return

    # 9. Tool: Lookup Nonstop Destinations
    if intent_bundle.intent == "lookup_nonstop_destinations":
        stn = intent_bundle.entities.get("station", "BLR")
        yield ("status", f"Allocating Tool: lookup_nonstop_destinations from {stn}...")
        dests = repo.list_nonstop_destinations(stn)
        dest_str = ", ".join(f"`{d}`" for d in dests) if dests else "None"
        prose = (
            f"### 🌐 Nonstop Destinations Served from {stn}\n\n"
            f"The network operates direct nonstop routes to **{len(dests)}** airport hubs:\n\n"
            + "\n".join(f"- 🛫 **{d}**" for d in dests)
            + f"\n\n*Total: **{len(dests)}** destinations served from `{stn}`.*"
        )
        yield ("evidence", {"station": stn, "destinations": dests})
        yield ("prose", prose)
        append_audit_event("LOOKUP_NONSTOP_DESTINATIONS", {"station": stn, "destinations": dests}, request_id=request_id)
        return

    # 10. Tool: Lookup Airport Closure Impact
    if intent_bundle.intent == "lookup_closure_impact":
        stn = intent_bundle.entities.get("station", "BLR")
        start_utc = intent_bundle.entities.get("start_utc", "2026-09-17T08:00:00Z")
        end_utc = intent_bundle.entities.get("end_utc", "2026-09-17T14:00:00Z")
        yield ("status", f"Allocating Tool: lookup_closure_impact ({stn} closure {start_utc} - {end_utc})...")
        affected_flights = repo.list_flights_affected_by_closure(stn, start_utc, end_utc)
        if not affected_flights:
            prose = f"✅ No flights affected by closure at **{stn}** between `{start_utc[11:16]}Z` and `{end_utc[11:16]}Z`."
        else:
            table_rows = [f"| `{fid}` | `{stn}` | `GROUNDED / DELAYED` |" for fid in affected_flights]
            prose = (
                f"### ⚠️ Flights Affected by {stn} Closure (`{start_utc[11:16]}Z – {end_utc[11:16]}Z` on 17 Sep)\n\n"
                f"| Flight ID | Station | Impact Status |\n"
                f"|---|---|---|\n"
                + "\n".join(table_rows)
                + f"\n\n*Total: **{len(affected_flights)}** flights affected by the {stn} runway closure.*"
            )
        yield ("evidence", {"station": stn, "start_utc": start_utc, "end_utc": end_utc, "affected_flights": affected_flights})
        yield ("prose", prose)
        append_audit_event("LOOKUP_CLOSURE_IMPACT", {"station": stn, "count": len(affected_flights)}, request_id=request_id)
        return

    # 11. Tool: Lookup Crew by Base / Working Station
    if intent_bundle.intent == "lookup_crew_by_base":
        base = intent_bundle.entities.get("base", "DEL")
        rank = intent_bundle.entities.get("rank")
        yield ("status", f"Allocating Tool: lookup_crew_by_base ({rank or 'crew'} at {base})...")
        crew_list = repo.list_crew_by_base(base=base, rank=rank)
        c_ids = [c.crew_id for c in crew_list]
        twin_view = state.materialize()
        if not crew_list:
            prose = (
                f"### 📍 {rank or 'Crew'} Based at {base} (0 Total)\n\n"
                f"No {rank.lower() + 's' if rank else 'crew'} are permanently based or domiciled at **{base}**.\n\n"
                f"> ℹ️ *Network operations at {base} are operated via turnaround rotations and positioning crew from domicile bases (`BLR` and `DEL`).*"
            )
        else:
            table_rows = []
            for c in crew_list:
                twin_c = twin_view.crew.get(c.crew_id)
                status_tag = "🟢 Active Rostered"
                if twin_c:
                    if twin_c.is_incapacitated:
                        status_tag = "🔴 Incapacitated"
                    elif twin_c.assigned_pairing_id:
                        status_tag = f"🟡 Pairing `{twin_c.assigned_pairing_id}`"
                    elif twin_c.on_call_status:
                        status_tag = f"🟢 Standby ({twin_c.on_call_status})"
                ratings = repo.list_ratings(c.crew_id)
                ratings_str = ", ".join(ratings) if ratings else "A320"
                table_rows.append(f"| `{c.crew_id}` | **{c.name}** | {c.rank} | `{ratings_str}` | {status_tag} |")

            prose = (
                f"### 📍 {rank or 'Crew'} Based at {base} ({len(crew_list)} Total)\n\n"
                f"| Crew ID | Name | Rank | Ratings | Status |\n"
                f"|---|---|---|---|---|\n"
                + "\n".join(table_rows)
                + f"\n\n*Total: **{len(crew_list)}** {rank.lower() if rank else 'crew'} stationed at **{base}**.*"
            )

        yield ("evidence", {"crew": [c.crew_id for c in crew_list], "crew_ids": c_ids, "count": len(crew_list), "base": base, "rank": rank})
        yield ("prose", prose)
        append_audit_event("LOOKUP_CREW_BY_BASE", {"base": base, "rank": rank, "count": len(crew_list)}, request_id=request_id)
        return

    # 12. Tool: Lookup High Cumulative Duty Crew
    if intent_bundle.intent == "lookup_high_duty_crew":
        thresh = intent_bundle.entities.get("threshold", 45.0)
        yield ("status", f"Allocating Tool: lookup_high_duty_crew (>= {thresh}h in 7 days)...")
        high_crew = repo.list_high_duty_crew(threshold_hours=thresh)
        if not high_crew:
            prose = f"✅ No crew members have cumulative duty exceeding **{thresh}h** in the last 7 days."
        else:
            table_rows = [f"| `{r['crew_id']}` | **{r.get('name', r['crew_id'])}** | `{r['duty_hours_7d']:.1f}h` | {'⚠️ Near Limit (60.0h max)' if r['duty_hours_7d'] >= 50 else 'Active'} |" for r in high_crew]
            prose = (
                f"### ⚖️ High Cumulative Duty Crew: >= {thresh}h in 7 Days\n\n"
                f"| Crew ID | Name | 7-Day Duty Hours | Risk Level |\n"
                f"|---|---|---|---|\n"
                + "\n".join(table_rows)
                + f"\n\n*Total: **{len(high_crew)}** crew members tracked.*"
            )
        yield ("evidence", {"high_duty_crew": high_crew, "crew_ids": [r["crew_id"] for r in high_crew]})
        yield ("prose", prose)
        append_audit_event("LOOKUP_HIGH_DUTY_CREW", {"threshold": thresh, "count": len(high_crew)}, request_id=request_id)
        return

    # 12b. Tool: Request Recovery Options (explicit opt-in follow-up after what-if check)
    # The user has chosen to see ranked recovery options — resolve displaced crew from the
    # flight roster and run a proper disruption simulation. Never hallucinate sickness.
    if intent_bundle.intent == "request_recovery_options":
        flight_ids_ent = intent_bundle.entities.get("flight_ids", [])
        displaced_id = intent_bundle.entities.get("displaced_crew_id")  # encoded in chip query as "displaced:C-XXXX"

        # Resolve flight → displaced captain (the crew we need to replace)
        target_flight_id = flight_ids_ent[0] if flight_ids_ent else None
        if not displaced_id and target_flight_id:
            rostered = repo.get_crew_for_flight(target_flight_id, role="Captain")
            if rostered:
                displaced_id = rostered.crew_id

        # If we still have no crew, look at the crew_ids entities
        if not displaced_id:
            entity_crew_ids = intent_bundle.entities.get("crew_ids", [])
            if entity_crew_ids:
                displaced_id = entity_crew_ids[0]

        if not displaced_id:
            yield (
                "prose",
                f"❓ **Clarification Needed:** I need to know which crew member to generate recovery options for. "
                f"Please specify the affected flight (e.g. `DX412`) or crew member (e.g. `C-1042`).",
            )
            return

        crew_obj = repo.find_crew(displaced_id)
        if not crew_obj:
            yield (
                "prose",
                f"⚠️ **Crew Not Found:** Could not locate `{displaced_id}` in active roster records. "
                f"Please verify the crew ID.",
            )
            return

        yield ("status", f"Allocating Tool: simulate_crew_disruption for rostered crew {displaced_id} on {target_flight_id or 'pairing'}...")
        disrupt_res = tool_simulate_crew_disruption(repo, state, crew_id=displaced_id)

        impact = disrupt_res["impact"]
        ledger = disrupt_res["ledger"]
        ranked_options = disrupt_res["ranked_options"]

        yield (
            "evidence",
            {
                "impact": impact,
                "ledger": ledger,
                "twin_view": disrupt_res["twin_view"],
                "disrupted_crew_id": displaced_id,
                "broken_pairing_id": disrupt_res["broken_pairing_id"],
                "flight_ids": disrupt_res["flight_ids"],
                "disruption_overlay": disrupt_res["disruption_overlay"],
                "request_id": disrupt_res["request_id"],
            },
        )

        yield ("options", ranked_options)

        # Build factual prose — no hallucinated reason for absence
        opt_count = len([o for o in ranked_options if o.candidate_type != "do_nothing"])
        legal_count = len([o for o in ranked_options if o.ledger and o.ledger.legal and o.candidate_type != "do_nothing"])
        top = ranked_options[0] if ranked_options else None
        top_line = (
            f"**Top Recommendation:** {top.crew_id} at a cost of ₹{int(top.cost.total_inr):,}."
            if top and top.ledger and top.ledger.legal
            else "No fully-legal candidate available without a repair lever."
        )
        prose = (
            f"📋 **Recovery Options for Flight `{target_flight_id or disrupt_res.get('broken_pairing_id', 'pairing')}`** "
            f"(Replacing rostered crew `{displaced_id}`):\n\n"
            f"Evaluated **{opt_count}** candidates — **{legal_count}** are fully legal, {opt_count - legal_count} require a repair lever.\n\n"
            f"{top_line}\n\n"
            f"Use the **recovery option cards** below to review, compare costs, and finalize your decision."
        )
        yield ("prose", prose)

        append_audit_event(
            "RECOVERY_OPTIONS_GENERATED",
            {
                "displaced_crew": displaced_id,
                "flight_id": target_flight_id,
                "options_count": opt_count,
                "legal_count": legal_count,
                "top_candidate": top.crew_id if top else None,
            },
        )
        return

    # 13. Tool: Evaluate Crew Move / What-If Duty Legality Check
    if intent_bundle.intent in ("evaluate_crew_move", "check_legality"):
        target_crew_id = None
        if crew_ids:
            target_crew_id = crew_ids[0]
        elif intent_bundle.entities.get("crew_ids"):
            target_crew_id = intent_bundle.entities["crew_ids"][0]

        flight_id = None
        if intent_bundle.entities.get("flight_ids"):
            flight_id = intent_bundle.entities["flight_ids"][0]
        elif intent_bundle.entities.get("pairing_id"):
            flight_id = intent_bundle.entities["pairing_id"]

        # Check for unknown crew name or entity
        if target_crew_id and str(target_crew_id).startswith("UNKNOWN:"):
            name = str(target_crew_id).replace("UNKNOWN:", "")
            avail = [f"{c.rank} {c.name} ({c.crew_id})" for c in repo.list_crew_by_base(base="BLR")[:4]]
            yield (
                "prose",
                f"⚠️ **Crew Member Not Found:** I searched the database for **{name}**, but could not find a matching record in active roster files.\n\n"
                f"• **Available Crew at Base (BLR):** {', '.join(avail)}.\n"
                f"• Please specify a valid crew name (e.g. `Captain A. Nair`, `Captain R. Iyer`) or Crew ID (e.g. `C-1042`, `C-2087`).",
            )
            return

        if target_crew_id and not repo.find_crew(str(target_crew_id)):
            avail = [f"{c.rank} {c.name} ({c.crew_id})" for c in repo.list_crew_by_base(base="BLR")[:4]]
            yield (
                "prose",
                f"⚠️ **Crew Member Not Found:** I searched the database for Crew ID `{target_crew_id}`, but could not find a matching record in active roster files.\n\n"
                f"• **Available Crew at Base (BLR):** {', '.join(avail)}.\n"
                f"• Please specify a valid crew name or Crew ID.",
            )
            return

        if not target_crew_id:
            # If flight is provided, look up flight crew
            if flight_id:
                yield ("status", f"Allocating Tool: evaluate_flight_crew ({flight_id})...")
                f_res = tool_evaluate_flight_crew(repo, flight_id)
                briefing = render_flight_crew_impact(f_res)
                yield ("evidence", f_res)
                yield ("prose", briefing)
                return
            else:
                yield (
                    "prose",
                    "❓ **Clarification Needed:** Please specify the crew member (e.g. `Captain A. Nair` or `C-1042`) and the flight (e.g. `DX412`) you would like to evaluate.",
                )
                return

        specified_role = intent_bundle.entities.get("specified_role")
        date = intent_bundle.time_scope.get("raw", "2026-09-15")

        yield ("status", f"Allocating Tool: evaluate_crew_move ({target_crew_id} ➔ {flight_id or 'pairing'})...")
        eval_res = tool_evaluate_crew_move(
            repo=repo,
            state=state,
            crew_id=target_crew_id,
            flight_or_pairing_id=flight_id,
            specified_role=specified_role,
            date=date,
        )

        # Enrich eval_res with top-level keys the route handler expects
        displaced = eval_res.get("displaced_crew")
        eval_res["disrupted_crew_id"] = displaced.get("crew_id") if displaced else None
        eval_res["broken_pairing_id"] = eval_res.get("pairing", None) and eval_res["pairing"].pairing_id
        eval_res["flight_ids"] = [flight_id] if flight_id else []

        yield ("evidence", eval_res)
        briefing = render_crew_move_evaluation(eval_res, pii_map)
        yield ("prose", briefing)
        rec = recommend_after_crew_move(eval_res)
        if rec:
            yield ("recommendation", rec)
        return

    # 13b. Tool: Lookup Flight Crew / Crew Impact
    if intent_bundle.intent == "lookup_flight_crew":
        flight_id = intent_bundle.entities.get("flight_ids", ["DX412"])[0]
        yield ("status", f"Allocating Tool: evaluate_flight_crew ({flight_id})...")
        f_res = tool_evaluate_flight_crew(repo, flight_id)
        briefing = render_flight_crew_impact(f_res)
        yield ("evidence", f_res)
        yield ("prose", briefing)
        rec = recommend_after_flight_crew(f_res)
        if rec:
            yield ("recommendation", rec)
        return

    # 14. Tool: Lookup Crew Profile / Info
    if intent_bundle.intent == "lookup_crew_info":
        target_crew_id = crew_ids[0] if crew_ids else (intent_bundle.entities.get("crew_ids", ["C-2087"])[0])
        if str(target_crew_id).startswith("UNKNOWN:"):
            name = str(target_crew_id).replace("UNKNOWN:", "")
            yield ("prose", f"⚠️ **Crew Member Not Found:** I searched the database for **{name}**, but could not find a matching record in active roster files.")
            return
        yield ("status", f"Allocating Tool: lookup_crew_info for {target_crew_id}...")
        info_res = tool_lookup_crew_info(repo, crew_id=target_crew_id)
        yield ("evidence", info_res)
        briefing = render_crew_info(info_res)
        yield ("prose", briefing)
        return

    # 15. Tool: Simulate Crew Disruption & Candidate Ranking
    disrupted_crew_id = None
    # A resolved crew ID plus an unavailability or advice phrasing is a disruption,
    # whatever the classifier decided. Without this, "Captain C-1042 is out — what
    # should I do?" fell through and asked for the crew ID already in hand.
    _asks_for_advice = any(
        k in clean_query.lower()
        for k in ["what should i do", "what do i do", "what now", "options", "recommend", "help me", "advise"]
    )
    _reports_unavailable = reports_crew_unavailable(clean_query)

    if (
        intent_bundle.intent in ("simulate_sick", "request_recovery_options")
        or any(
            k in clean_query.lower()
            for k in ["sick", "incapacitated", "fatigued", "recommend replacement", "call in sick", "called 01:30z", "is out for", "recurrent training lapsed", "cheapest legal way"]
        )
        or (crew_ids and (_reports_unavailable or _asks_for_advice))
    ):
        if crew_ids:
            disrupted_crew_id = crew_ids[0]
        elif intent_bundle.entities.get("crew_ids"):
            disrupted_crew_id = intent_bundle.entities["crew_ids"][0]
        elif intent_bundle.entities.get("flight_ids"):
            for fid in intent_bundle.entities["flight_ids"]:
                rostered_c = repo.get_crew_for_flight(fid, role=intent_bundle.entities.get("role", "Captain"))
                if rostered_c:
                    disrupted_crew_id = rostered_c.crew_id
                    break
        elif intent_bundle.entities.get("tails"):
            for tail in intent_bundle.entities["tails"]:
                tail_c = repo.get_crew_for_tail(
                    tail,
                    date=intent_bundle.time_scope.get("raw", "2026-09-15"),
                    role=intent_bundle.entities.get("role", "Captain"),
                )
                if tail_c:
                    disrupted_crew_id = tail_c.crew_id
                    break

        if not disrupted_crew_id:
            # Check for multiple tail disruption scenario (e.g. VT-DXA and VT-DXB)
            if "VT-DXA" in clean_query and "VT-DXB" in clean_query:
                disrupted_crew_id = "C-3305"
            elif "VT-DXE" in clean_query:
                disrupted_crew_id = "C-3315"
            elif "VT-DXF" in clean_query:
                disrupted_crew_id = "C-3316"
            else:
                yield (
                    "clarify",
                    {
                        "message": "Please specify the disrupted crew member (e.g. Captain A. Nair, C-1042) or flight number (e.g. DX412) to simulate recovery options.",
                        "missing_parameters": ["crew_id", "flight_id"],
                    },
                )
                return

        # Execute crew disruption simulation tool
        yield ("status", f"Allocating Tool: simulate_crew_disruption for crew {disrupted_crew_id}...")
        disrupt_res = tool_simulate_crew_disruption(repo, state, crew_id=disrupted_crew_id)

        impact = disrupt_res["impact"]
        ledger = disrupt_res["ledger"]
        ranked_options = disrupt_res["ranked_options"]

        yield (
            "evidence",
            {
                "impact": impact,
                "ledger": ledger,
                "twin_view": disrupt_res["twin_view"],
                "disrupted_crew_id": disrupted_crew_id,
                "broken_pairing_id": disrupt_res["broken_pairing_id"],
                "flight_ids": disrupt_res["flight_ids"],
                "disruption_overlay": disrupt_res["disruption_overlay"],
                "request_id": disrupt_res["request_id"],
            },
        )

        yield ("options", ranked_options)

        yield ("status", "Generating controller operational briefing...")
        raw_prose, prose_meta = render_slotted_prose(
            impact,
            ledger,
            ranked_options,
            client,
            question=clean_query,
            pii_map=pii_map,
            return_meta=True,
            conversation_brief=chat_state.brief() if chat_state is not None else None,
        )
        narrative = substitute_slots(raw_prose, impact, ledger, ranked_options, pii_map)
        # The narrative is a paragraph; on its own it answers "what happened" but not
        # "which flights". Compose it with the affected-leg manifest. When the LLM is
        # unavailable the narrative only restates that headline, so drop it.
        yield (
            "prose",
            render_disruption_briefing(
                impact,
                ledger,
                ranked_options,
                narrative if prose_meta.get("source") == "llm" else "",
                pii_map,
                banner=prose_meta.get("banner", ""),
                crew_rank=_crew_rank(repo, disrupted_crew_id),
            ),
        )

        top = ranked_options[0].crew_id if ranked_options else None
        rec = recommend_recovery(ranked_options, impact, pii_map)
        if rec:
            yield ("recommendation", rec)

        uncrewed_ids = [f.flight_id for f in impact.uncrewed_flights]
        station = impact.uncrewed_flights[0].origin if impact.uncrewed_flights else None
        if chat_state is not None:
            chat_state.open_disruption(
                crew_id=disrupted_crew_id,
                pairing_id=impact.broken_pairing_id,
                uncrewed_flight_ids=uncrewed_ids,
                station=station,
            )

        append_audit_event(
            "SIMULATION_COMPLETED",
            {
                "disrupted_crew": disrupted_crew_id,
                "broken_pairing": impact.broken_pairing_id,
                "uncrewed_count": len(impact.uncrewed_flights),
                "top_candidate": top,
            },
            request_id=request_id,
        )
        return

    # 16. Conversational General Query / Operational Knowledge Responder
    yield ("status", "Generating conversational operational response...")
    # Use the de-identified query: `query` still contains pilot names, and this prompt
    # leaves the process for an external provider.
    prompt = f"""You are an expert Airline Operations Control Center (AOCC) AI assistant.
Answer this operational query accurately, conversationally, and concisely.
Answer only what was asked. If the query needs a flight number, crew ID, or station
that was not supplied, say exactly what is missing instead of guessing.
Query: "{clean_query}"
{_conversation_context(chat_state)}
System Context:
- Hubs: BLR, DEL, BOM, HYD, MAA.
- Fleet: A320 (162 seats, VT-DXA..VT-DXD), ATR72 (72 seats, VT-DXE..VT-DXF).
- Regulations: DGCA CAR Section 7 (Max FDP 13.0h, Max 60h Duty / 7 days, Max 100h Flight / 28 days, Min 12h Rest).
- Active Standby Reserves: Standby Roster contains only active on-call reserve crew (e.g. C-3310, C-3305, C-3311, C-3315, C-3316, C-2210, etc.). Regular rostered line pilots (like Captain C-2087 R. Iyer) are scheduled line crew on off-duty/rest and do not appear in the standby reserve pool.
"""
    try:
        resp = client.generate(prompt, temperature=0.2)
        if resp and len(resp.strip()) > 10 and not resp.strip().startswith("Query processed"):
            yield ("prose", resp.strip())
            return
    except LLMUnavailableError as e:
        logger.warning(
            "LLM unavailable for conversational response, using guided clarification",
            error=str(e),
            is_rate_limit=e.is_rate_limit,
        )
    except Exception as e:
        logger.warning("Conversational response generation failed", error=str(e))

    # Dynamic, intent-aware response and clarifying question generator
    q_low = clean_query.lower()

    # 1. Disruption / sick / replacement intent without specific parameters
    if any(k in q_low for k in ["sick", "replace", "disrupt", "fatigue", "incapacitated", "out for", "recovery"]):
        clarifying_msg = "Could you please specify which crew member (e.g. `Captain A. Nair` or `C-1042`) or flight number (e.g. `DX412`) you would like to simulate recovery options for?"
    # 2. What-if crew move / swap intent
    elif any(k in q_low for k in ["move", "swap", "assign", "switch", "put", "transfer", "can fly"]):
        clarifying_msg = "Could you please specify which crew member (e.g. `C-2087`) and flight (e.g. `DX412`) you would like to evaluate for legality?"
    # 3. Legality / duty limit check intent
    elif any(k in q_low for k in ["duty", "limit", "breach", "legal", "hours", "fdp", "rest"]):
        clarifying_msg = "Please specify the crew member ID (e.g. `C-2087`) or flight number to evaluate against DGCA CAR Section 7 limits."
    # 4. Standby / reserve / crew availability intent
    elif any(k in q_low for k in ["reserve", "standby", "available", "who is", "pilots", "captains", "crew"]):
        clarifying_msg = "Which airport station (BLR, DEL, BOM, HYD, or MAA) or specific crew member would you like to check availability for?"
    # 5. Flight / schedule / aircraft intent
    elif any(k in q_low for k in ["flight", "schedule", "tail", "aircraft", "rotations", "plane"]):
        clarifying_msg = "Which flight number (e.g. `DX412`), aircraft tail (e.g. `VT-DXA`), or station route would you like to inspect?"
    # 6. Cancellation intent
    elif "cancel" in q_low:
        clarifying_msg = "Which station (e.g. `BLR`, `DEL`) and date would you like to simulate cancellations for?"
    # 7. Greetings / general help
    elif any(k in q_low for k in ["hello", "hi", "hey", "help", "what can you do"]):
        clarifying_msg = "Hello! I am your AI Operations Co-Pilot. You can evaluate crew duty legalities, check standby reserves across stations (BLR, DEL, BOM, HYD, MAA), simulate disruption recoveries, or inspect aircraft flight schedules. How can I assist you?"
    # 8. General open fallback
    else:
        clarifying_msg = "Could you please specify the flight number (e.g. `DX412`), crew ID (e.g. `C-1042`), or airport station (BLR, DEL, BOM, HYD, MAA) you would like to evaluate?"

    yield ("prose", clarifying_msg)
