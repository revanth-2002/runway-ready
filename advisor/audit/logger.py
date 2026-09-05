"""Structured logger and audit trail management."""

import contextvars
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Default audit log path
DEFAULT_AUDIT_LOG_PATH = Path(__file__).resolve().parent / "audit_log.jsonl"

_current_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_request_id", default=None
)


def set_request_id(request_id: Optional[str]) -> contextvars.Token[Optional[str]]:
    """Sets the active request_id in context."""
    return _current_request_id.set(request_id)


def get_request_id() -> Optional[str]:
    """Gets the active request_id from context."""
    return _current_request_id.get()


class StructuredLogger:
    """Standardized structured JSON logger for operational observability."""

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(message)s")
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)
            log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
            level = getattr(logging, log_level_str, logging.INFO)
            self._logger.setLevel(level)

    def _inject_request_id(self, context: Dict[str, Any]) -> Dict[str, Any]:
        req_id = context.get("request_id") or get_request_id()
        if req_id is not None and "request_id" not in context:
            context["request_id"] = req_id
        return context

    def debug(self, message: str, **context: Any) -> None:
        ctx = self._inject_request_id(dict(context))
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "DEBUG",
            "logger": self._logger.name,
            "message": message,
            **ctx,
        }
        self._logger.debug(json.dumps(payload))

    def info(self, message: str, **context: Any) -> None:
        ctx = self._inject_request_id(dict(context))
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "INFO",
            "logger": self._logger.name,
            "message": message,
            **ctx,
        }
        self._logger.info(json.dumps(payload))

    def warning(self, message: str, **context: Any) -> None:
        ctx = self._inject_request_id(dict(context))
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "WARNING",
            "logger": self._logger.name,
            "message": message,
            **ctx,
        }
        self._logger.warning(json.dumps(payload))

    def error(
        self, message: str, error: Optional[Exception] = None, **context: Any
    ) -> None:
        ctx = self._inject_request_id(dict(context))
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "logger": self._logger.name,
            "message": message,
            "error_type": error.__class__.__name__ if error else None,
            "error_detail": str(error) if error else None,
            **ctx,
        }
        self._logger.error(json.dumps(payload))


def append_audit_event(
    event_type: str,
    payload: Dict[str, Any],
    log_file: Path = DEFAULT_AUDIT_LOG_PATH,
    request_id: Optional[str] = None,
) -> None:
    """Appends an immutable event entry to the JSONL audit log."""
    req_id = request_id or get_request_id()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": req_id,
        "event_type": event_type,
        "payload": payload,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
