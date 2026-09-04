"""Structured logger and audit trail management."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Default audit log path
DEFAULT_AUDIT_LOG_PATH = Path(__file__).resolve().parent / "audit_log.jsonl"


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

    def debug(self, message: str, **context: Any) -> None:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "DEBUG",
            "logger": self._logger.name,
            "message": message,
            **context,
        }
        self._logger.debug(json.dumps(payload))

    def info(self, message: str, **context: Any) -> None:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "INFO",
            "logger": self._logger.name,
            "message": message,
            **context,
        }
        self._logger.info(json.dumps(payload))

    def warning(self, message: str, **context: Any) -> None:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "WARNING",
            "logger": self._logger.name,
            "message": message,
            **context,
        }
        self._logger.warning(json.dumps(payload))

    def error(
        self, message: str, error: Optional[Exception] = None, **context: Any
    ) -> None:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "logger": self._logger.name,
            "message": message,
            "error_type": error.__class__.__name__ if error else None,
            "error_detail": str(error) if error else None,
            **context,
        }
        self._logger.error(json.dumps(payload))


def append_audit_event(
    event_type: str,
    payload: Dict[str, Any],
    log_file: Path = DEFAULT_AUDIT_LOG_PATH,
) -> None:
    """Appends an immutable event entry to the JSONL audit log."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
