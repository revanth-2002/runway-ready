"""Domain exception hierarchy for Crew Ops Advisor."""

from typing import Optional


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
