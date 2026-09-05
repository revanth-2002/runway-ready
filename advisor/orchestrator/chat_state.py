"""Conversation state — the ground truth for what has already happened in a chat.

The orchestrator is otherwise stateless: every directive is parsed in isolation, so
"produce the recovery options" or "which flights are affected" carry no referent.
This module records what each turn asked, understood, decided and recommended, so
the advisor can work step by step instead of re-deriving or re-asking.

Recording is driven centrally by the graph's `record` node, not by individual tool
branches — an earlier per-branch version silently missed most intents.

Only de-identified values are stored (crew IDs, never names) — the same boundary
`resolve_local_pii` enforces before anything leaves the process.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from advisor.audit.logger import StructuredLogger

logger = StructuredLogger("advisor.orchestrator.chat_state")

MAX_ACTIONS = 50

# Intents that change the operational picture rather than just reading it. Used to
# rank what the conversation is actually about.
_DECISION_INTENTS = {"reassign_crew", "simulate_sick", "request_recovery_options"}

# Objective phrasing per intent: the operational goal the controller is pursuing.
_OBJECTIVES = {
    "simulate_sick": "Restore cover for a disrupted pairing",
    "request_recovery_options": "Restore cover for a disrupted pairing",
    "reassign_crew": "Commit a replacement to the roster",
    "evaluate_crew_move": "Verify a proposed crew move against DGCA limits",
    "check_legality": "Verify a proposed crew move against DGCA limits",
    "lookup_reserves": "Assess standby strength",
    "lookup_crew_by_base": "Assess crew availability at a base",
    "lookup_crew_info": "Inspect a crew member's status",
    "lookup_flights": "Inspect the flight schedule",
    "lookup_uncrewed_flights": "Scope the flights left uncrewed",
    "lookup_flight_crew": "Inspect who operates a flight",
    "lookup_pairing_crew": "Inspect who operates a pairing",
    "lookup_expiring_certs": "Surface upcoming certification lapses",
    "lookup_high_duty_crew": "Surface crew near duty limits",
    "lookup_closure_impact": "Assess a station closure window",
    "lookup_nonstop_destinations": "Inspect network connectivity",
    "cancel_station_departures": "Price a mass cancellation",
    "out_of_scope": "Out of operational scope",
    "ambiguous_time": "Awaiting a concrete time window",
    "general_query": "General operational question",
}

# Entity keys worth carrying forward as referents for later turns.
_TRACKED_ENTITIES = (
    "station",
    "base",
    "crew_id",
    "crew_ids",
    "flight_ids",
    "pairing_id",
    "rank",
    "date",
    "replacement_crew_id",
    "origin",
    "destination",
)


@dataclass(frozen=True)
class ChatTurn:
    """One completed exchange: what was asked, understood, answered and advised."""

    turn: int
    query: str
    intent: str
    entities: Dict[str, Any] = field(default_factory=dict)
    objective: str = ""
    outcome: str = ""
    summary: str = ""
    recommendation: Optional[str] = None
    suggestions: List[Dict[str, str]] = field(default_factory=list)
    question_asked: Optional[str] = None
    timestamp_utc: str = ""

    @property
    def kind(self) -> str:
        """Alias for `intent` — what this turn did."""
        return self.intent

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn": self.turn,
            "query": self.query,
            "intent": self.intent,
            # Legacy alias retained for existing consumers.
            "kind": self.intent,
            "entities": self.entities,
            "objective": self.objective,
            "outcome": self.outcome,
            "summary": self.summary,
            "recommendation": self.recommendation,
            "suggestions": self.suggestions,
            "question_asked": self.question_asked,
            "timestamp_utc": self.timestamp_utc,
        }


@dataclass
class ActiveDisruption:
    """The disruption currently under discussion, if any."""

    crew_id: str
    pairing_id: str
    uncrewed_flight_ids: List[str] = field(default_factory=list)
    station: Optional[str] = None
    turn: int = 0
    resolved: bool = False
    resolved_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "crew_id": self.crew_id,
            "pairing_id": self.pairing_id,
            "uncrewed_flight_ids": list(self.uncrewed_flight_ids),
            "station": self.station,
            "turn": self.turn,
            "resolved": self.resolved,
            "resolved_by": self.resolved_by,
        }


class ConversationState:
    """Ordered memory of one controller conversation."""

    def __init__(self) -> None:
        self.turns: List[ChatTurn] = []
        self.active_disruption: Optional[ActiveDisruption] = None
        # Referents carried forward: most recent non-empty value per entity key.
        self.entities: Dict[str, Any] = {}
        # Unanswered questions the advisor put to the controller.
        self.open_questions: List[Dict[str, Any]] = []
        self._turn = 0

    # -- recording ---------------------------------------------------------

    def record_turn(
        self,
        query: str,
        intent: str,
        entities: Optional[Dict[str, Any]] = None,
        outcome: str = "answered",
        summary: str = "",
        recommendation: Optional[str] = None,
        suggestions: Optional[List[Dict[str, str]]] = None,
        question_asked: Optional[str] = None,
    ) -> ChatTurn:
        """Appends a completed turn and folds its entities into the carried context."""
        self._turn += 1
        entities = {k: v for k, v in (entities or {}).items() if v not in (None, [], "")}

        turn = ChatTurn(
            turn=self._turn,
            query=query,
            intent=intent,
            entities=entities,
            objective=_OBJECTIVES.get(intent, "General operational question"),
            outcome=outcome,
            summary=summary,
            recommendation=recommendation,
            suggestions=list(suggestions or []),
            question_asked=question_asked,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        self.turns.append(turn)

        # Carry referents forward so a later "and at that station?" resolves.
        for key in _TRACKED_ENTITIES:
            if entities.get(key):
                self.entities[key] = entities[key]

        if question_asked:
            self.open_questions.append(
                {"turn": self._turn, "question": question_asked, "answered": False}
            )
        elif outcome == "answered":
            # Any directive that produced an answer closes the pending question.
            for q in self.open_questions:
                q["answered"] = True

        if len(self.turns) > MAX_ACTIONS:
            self.turns = self.turns[-MAX_ACTIONS:]

        logger.debug(
            "Recorded chat turn", turn=turn.turn, intent=intent, outcome=outcome
        )
        return turn

    # Backwards-compatible alias used by older call sites.
    def record(
        self,
        kind: str,
        query: str,
        summary: str,
        entities: Optional[Dict[str, Any]] = None,
    ) -> ChatTurn:
        return self.record_turn(
            query=query, intent=kind, entities=entities, summary=summary
        )

    def open_disruption(
        self,
        crew_id: str,
        pairing_id: str,
        uncrewed_flight_ids: List[str],
        station: Optional[str] = None,
    ) -> None:
        """Marks a disruption as the active subject of the conversation."""
        self.active_disruption = ActiveDisruption(
            crew_id=crew_id,
            pairing_id=pairing_id or "",
            uncrewed_flight_ids=list(uncrewed_flight_ids),
            station=station,
            turn=self._turn,
        )
        logger.info(
            "Opened active disruption in chat state",
            crew_id=crew_id,
            pairing_id=pairing_id,
            uncrewed_count=len(uncrewed_flight_ids),
        )

    def resolve_disruption(self, replacement_crew_id: str) -> None:
        """Marks the active disruption as covered by a committed reassignment."""
        if self.active_disruption is not None:
            self.active_disruption.resolved = True
            self.active_disruption.resolved_by = replacement_crew_id
            logger.info(
                "Resolved active disruption in chat state",
                pairing_id=self.active_disruption.pairing_id,
                replacement=replacement_crew_id,
            )

    def clear(self) -> None:
        """Drops all conversational memory. Digital twin overlays are unaffected."""
        count = len(self.turns)
        self.turns = []
        self.active_disruption = None
        self.entities = {}
        self.open_questions = []
        self._turn = 0
        logger.info("Cleared conversation state", discarded_turns=count)

    # -- retrieval ---------------------------------------------------------

    @property
    def turn_count(self) -> int:
        return self._turn

    @property
    def actions(self) -> List[ChatTurn]:
        """Alias for `turns`."""
        return self.turns

    def open_disruption_context(self) -> Optional[ActiveDisruption]:
        """The active disruption only while it is still unresolved."""
        d = self.active_disruption
        if d is not None and not d.resolved:
            return d
        return None

    def last_turn(self, intent: Optional[str] = None) -> Optional[ChatTurn]:
        for turn in reversed(self.turns):
            if intent is None or turn.intent == intent:
                return turn
        return None

    # Backwards-compatible alias.
    def last_action(self, kind: Optional[str] = None) -> Optional[ChatTurn]:
        return self.last_turn(kind)

    def last_entity(self, key: str) -> Optional[Any]:
        """Most recent non-empty value recorded for an entity key."""
        if self.entities.get(key):
            return self.entities[key]
        for turn in reversed(self.turns):
            value = turn.entities.get(key)
            if value:
                return value
        return None

    def pending_question(self) -> Optional[Dict[str, Any]]:
        """The most recent question the advisor asked that is still unanswered."""
        for q in reversed(self.open_questions):
            if not q["answered"]:
                return q
        return None

    def priorities(self) -> List[Dict[str, Any]]:
        """What this conversation is about, most pressing first.

        Ranked by operational weight: an unresolved disruption outranks a pending
        question, which outranks whatever was last looked up.
        """
        out: List[Dict[str, Any]] = []

        disruption = self.open_disruption_context()
        if disruption is not None:
            out.append(
                {
                    "rank": 1,
                    "kind": "unresolved_disruption",
                    "detail": (
                        f"{disruption.crew_id} unavailable — pairing "
                        f"{disruption.pairing_id}, "
                        f"{len(disruption.uncrewed_flight_ids)} leg(s) uncrewed"
                    ),
                    "pairing_id": disruption.pairing_id,
                }
            )

        pending = self.pending_question()
        if pending is not None:
            out.append(
                {
                    "rank": len(out) + 1,
                    "kind": "awaiting_controller_answer",
                    "detail": pending["question"],
                }
            )

        last = self.last_turn()
        if last is not None and not out:
            out.append(
                {
                    "rank": 1,
                    "kind": "last_enquiry",
                    "detail": last.objective,
                }
            )
        return out

    def objective(self) -> Optional[str]:
        """The conversation's current operational goal."""
        prios = self.priorities()
        if not prios:
            return None
        top = prios[0]
        if top["kind"] == "unresolved_disruption":
            return "Restore legal cover for the disrupted pairing"
        if top["kind"] == "awaiting_controller_answer":
            return "Awaiting controller decision"
        return top["detail"]

    def recommendations(self) -> List[Dict[str, Any]]:
        """Every recommendation issued so far, most recent first."""
        return [
            {"turn": t.turn, "intent": t.intent, "recommendation": t.recommendation}
            for t in reversed(self.turns)
            if t.recommendation
        ]

    def questions_asked(self) -> List[Dict[str, Any]]:
        """Every question the advisor put to the controller."""
        return list(self.open_questions)

    def brief(self) -> str:
        """Compact natural-language digest of the conversation so far.

        Fed to the LLM so narration is aware of what has already been established.
        """
        if not self.turns:
            return "No prior turns in this conversation."

        lines = []
        obj = self.objective()
        if obj:
            lines.append(f"Objective: {obj}")

        disruption = self.open_disruption_context()
        if disruption is not None:
            lines.append(
                f"Open disruption: {disruption.crew_id} on pairing {disruption.pairing_id}, "
                f"{len(disruption.uncrewed_flight_ids)} leg(s) uncrewed"
                + (f" out of {disruption.station}" if disruption.station else "")
            )
        elif self.active_disruption is not None:
            lines.append(
                f"Disruption on {self.active_disruption.pairing_id} was resolved by "
                f"{self.active_disruption.resolved_by}"
            )

        recent = self.turns[-4:]
        lines.append("Recent turns:")
        for t in recent:
            lines.append(f"  {t.turn}. [{t.intent}] {t.summary or t.query}")

        pending = self.pending_question()
        if pending is not None:
            lines.append(f"Awaiting answer to: {pending['question']}")

        if self.entities:
            carried = ", ".join(f"{k}={v}" for k, v in self.entities.items())
            lines.append(f"Referents in play: {carried}")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_count": self._turn,
            "turns": [t.to_dict() for t in self.turns],
            # Legacy key retained for existing API consumers.
            "actions": [t.to_dict() for t in self.turns],
            "entities": dict(self.entities),
            "objective": self.objective(),
            "priorities": self.priorities(),
            "recommendations": self.recommendations(),
            "questions_asked": self.questions_asked(),
            "active_disruption": (
                self.active_disruption.to_dict() if self.active_disruption else None
            ),
        }
