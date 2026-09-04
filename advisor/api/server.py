"""FastAPI Server Application Entrypoint for Crew Ops Advisor."""

from contextlib import asynccontextmanager
import sys
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from advisor.api.routes import router as api_v1_router, twin_manager
from advisor.audit.logger import StructuredLogger

logger = StructuredLogger("advisor.api.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager that warms the digital twin on startup."""
    logger.info("Starting Crew Ops Advisor API service...")
    # twin_manager already pre-warms on import, but we log operational readiness
    logger.info(
        "Digital twin server ready",
        active_tails=len(twin_manager.warmed["baseline_twin"].tails),
        active_flights=len(twin_manager.warmed["baseline_twin"].active_flights),
        overlays_count=len(twin_manager.state.overlays),
    )
    yield
    logger.info("Shutting down Crew Ops Advisor API service.")


def create_app() -> FastAPI:
    """Factory creating configured FastAPI application."""
    app = FastAPI(
        title="Crew Ops Advisor | Airline Digital Twin API",
        description="RESTful service layer powering airline operations control, disruption recovery, and DGCA legality verification.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_v1_router)

    @app.get("/", tags=["Root"])
    def root():
        return {
            "name": "Crew Ops Advisor API",
            "version": "1.0.0",
            "documentation": "/docs",
            "endpoints": {
                "health": "/api/v1/health",
                "network_overview": "/api/v1/network/overview",
                "simulate_disruption": "/api/v1/disruptions/simulate",
                "finalize_recommendation": "/api/v1/recommendations/finalize",
                "reserves": "/api/v1/reserves",
                "fleet_rotations": "/api/v1/fleet/rotations",
                "twin_state": "/api/v1/twin/state",
                "twin_undo": "/api/v1/twin/undo",
                "twin_reset": "/api/v1/twin/reset",
            },
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    logger.info("Launching Uvicorn server on http://127.0.0.1:8000")
    uvicorn.run("advisor.api.server:app", host="0.0.0.0", port=8000, reload=False)
