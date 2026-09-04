"""Single authoritative operational state and overlay stack."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Tuple, TYPE_CHECKING

from advisor.audit.logger import StructuredLogger

if TYPE_CHECKING:
    from advisor.twin.view import DigitalTwinState

logger = StructuredLogger("advisor.domain.state")


@dataclass(frozen=True)
class Overlay:
    overlay_id: str
    kind: Literal["sick", "station_closure", "reassign", "delay", "cancel"]
    payload: dict[str, Any]
    label: str


@dataclass(frozen=True)
class OpsState:
    """Immutable operational state containing db path and overlay stack."""

    db_path: Path
    overlays: Tuple[Overlay, ...] = ()
    clock_mode: str = "reconciled"

    def apply(self, overlay: Overlay) -> "OpsState":
        """Returns a new OpsState with the overlay appended."""
        logger.info("Applied overlay to operational state", overlay_id=overlay.overlay_id, kind=overlay.kind, stack_depth=len(self.overlays) + 1)
        return OpsState(
            db_path=self.db_path,
            overlays=(*self.overlays, overlay),
            clock_mode=self.clock_mode,
        )

    def pop(self) -> "OpsState":
        """Returns a new OpsState with the top overlay removed."""
        if not self.overlays:
            return self
        logger.info("Popped overlay from operational state", popped_id=self.overlays[-1].overlay_id, remaining=len(self.overlays) - 1)
        return OpsState(
            db_path=self.db_path,
            overlays=self.overlays[:-1],
            clock_mode=self.clock_mode,
        )

    def materialize(self) -> "DigitalTwinState":
        """Projects base SQLite records through active overlays into an in-memory twin view."""
        from advisor.twin.view import build_digital_twin_view
        logger.debug("Materializing digital twin view from overlays", overlay_count=len(self.overlays))
        return build_digital_twin_view(self.db_path, self.overlays)
