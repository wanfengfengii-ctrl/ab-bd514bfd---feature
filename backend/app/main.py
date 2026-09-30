"""FastAPI application: beamline exposure scheduling service."""
from __future__ import annotations

import logging
from typing import List

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .scheduler import InputValidationError, SolverTimeoutError, solve
from .schemas import ScheduleRequest, SolutionPayload

logger = logging.getLogger("beamline")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Beamline Exposure Scheduler", version="1.0.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "api"}


def _null_payload(req: ScheduleRequest, reason: str,
                  field_errors: List[str],
                  cooling_obstruction: dict | None = None) -> dict:
    """All-null solution body.

    When shared cooling is disabled (the historical request shape) the body
    keeps its exact old key set and order; when enabled it additionally
    carries ``cooling_trace: null`` (and, when proven, a
    ``cooling_obstruction`` diagnosis) so an enabled request never reports a
    partial ledger on failure.
    """
    payload = {
        "feasible": False,
        "reason": reason,
        "field_errors": field_errors,
        "starts": None,
        "finishes": None,
        "makespan": None,
        "sum_starts": None,
        "slacks": None,
        "equipment_orders": None,
    }
    if req.shared_cooling is not None:
        payload["cooling_trace"] = None
        payload["cooling_obstruction"] = cooling_obstruction
    payload["solver_time_ms"] = None
    return payload


@app.post("/api/schedule", response_model=SolutionPayload)
def schedule(req: ScheduleRequest) -> JSONResponse:
    try:
        result = solve(req)
    except InputValidationError as exc:
        # 400: the input itself is wrong (references, bad windows, ...).
        return JSONResponse(
            status_code=400, content=_null_payload(req, "input_error", exc.errors)
        )
    except SolverTimeoutError as exc:
        # 503: neither feasibility nor infeasibility was proven in time.
        logger.warning("solver timeout: %s", exc)
        return JSONResponse(
            status_code=503, content=_null_payload(req, "solver_timeout", [])
        )
    if not result["feasible"]:
        # 200 with feasible=false: input is valid, but no executable timing
        # exists. Never return partial starts or a stale plan.
        return JSONResponse(
            status_code=200,
            content=_null_payload(
                req, "no_schedule", [],
                cooling_obstruction=result.get("cooling_obstruction"),
            ),
        )
    return JSONResponse(status_code=200, content=result)
