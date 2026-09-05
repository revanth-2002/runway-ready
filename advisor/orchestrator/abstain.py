from enum import Enum
from typing import Optional, Tuple
from advisor.audit.logger import StructuredLogger
from advisor.data.repository import OpsRepository
from advisor.llm.parser import QueryIntent

logger = StructuredLogger("advisor.orchestrator.abstain")



class AbstainReason(Enum):
    UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
    AMBIGUOUS_TIME = "AMBIGUOUS_TIME"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    NO_LEGAL_OPTIONS = "NO_LEGAL_OPTIONS"


def should_abstain(
    intent: QueryIntent, repo: OpsRepository
) -> Optional[Tuple[AbstainReason, str]]:
    """Inspects QueryIntent and determines if the system must abstain before simulation."""

    # 1. Scope check
    if intent.intent == "out_of_scope" or intent.unsupported_aspects:
        if any("hotel" in a or "baggage" in a or "customer service" in a for a in intent.unsupported_aspects):
            msg = "Hotel accommodations and passenger baggage logistics are outside my operational scope. Please refer to Passenger Services."
            logger.warning("Abstention gate triggered: out of scope", reason="OUT_OF_SCOPE", detail=msg)
            return (AbstainReason.OUT_OF_SCOPE, msg)

    # 2. Ambiguous time check
    if intent.intent == "ambiguous_time" or intent.confidence < 0.60:
        msg = "Relative time 'afternoon' is ambiguous across time zones. Please specify an exact UTC timestamp or flight number."
        logger.warning("Abstention gate triggered: ambiguous time", reason="AMBIGUOUS_TIME", detail=msg)
        return (AbstainReason.AMBIGUOUS_TIME, msg)

    # 3. Entity existence check
    crew_ids = intent.entities.get("crew_ids", [])
    for cid in crew_ids:
        if str(cid).startswith("UNKNOWN:"):
            name = str(cid).replace("UNKNOWN:", "")
            msg = f"Crew member '{name}' does not exist in roster records."
            logger.warning("Abstention gate triggered: unknown crew entity", reason="UNKNOWN_ENTITY", crew_name=name)
            return (AbstainReason.UNKNOWN_ENTITY, msg)
        elif not repo.find_crew(str(cid)):
            msg = f"Crew member {cid} does not exist in roster records."
            logger.warning("Abstention gate triggered: unknown crew entity", reason="UNKNOWN_ENTITY", crew_id=cid)
            return (AbstainReason.UNKNOWN_ENTITY, msg)

    flight_ids = intent.entities.get("flight_ids", [])
    for fid in flight_ids:
        if not repo.find_flight(str(fid)):
            msg = f"Flight {fid} is not present in active flight schedules."
            logger.warning("Abstention gate triggered: unknown flight entity", reason="UNKNOWN_ENTITY", flight_id=fid)
            return (AbstainReason.UNKNOWN_ENTITY, msg)

    logger.debug("Abstention checks passed", intent=intent.intent)
    return None
