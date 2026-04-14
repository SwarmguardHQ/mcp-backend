"""
Optional Mesa / drone-sim bridge — read-only JSON snapshots of SIRENModel state.

Requires the `mesa` optional dependency group on mcp-backend and a sibling `drone-sim/` folder:

    uv sync --extra mesa
    # or: pip install -e ".[mesa]"

Routes are mounted under the same /world prefix as other world APIs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from utils.config import USE_MESA_SIM

router = APIRouter()


class MesaStepBody(BaseModel):
    """Advance the cached Mesa model by N discrete steps."""

    steps: int = Field(1, ge=1, le=200, description="Number of model.step() calls")


def _vhack_root() -> Path:
    """mcp-backend/api/routers/mesa.py → parents[3] = monorepo root (contains drone-sim/)."""
    return Path(__file__).resolve().parents[3]


def _ensure_drone_sim_on_path() -> Path:
    root = _vhack_root()
    drone_sim = root / "drone-sim"
    if not drone_sim.is_dir():
        raise HTTPException(
            status_code=503,
            detail="drone-sim not found next to mcp-backend (expected ../drone-sim from repo root).",
        )
    p = str(drone_sim.resolve())
    if p not in sys.path:
        sys.path.insert(0, p)
    return drone_sim


_mesa_model: Any = None


def _get_model():
    """Lazy singleton DisasterZone for HTTP inspection."""
    if USE_MESA_SIM:
        from mcp_server import mesa_bridge

        return mesa_bridge.get_mesa_model()

    global _mesa_model
    if _mesa_model is None:
        _ensure_drone_sim_on_path()
        try:
            import numpy as np
            from simulation.grid import DisasterZone
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not import drone-sim (mesa/numpy/scipy). "
                    "Install optional deps: pip install -e '../mcp-backend[mesa]' from repo or "
                    "`uv sync --extra mesa` in mcp-backend."
                ),
            ) from exc

        seed = int(os.environ.get("MESA_SIM_SEED", "42"))
        np.random.seed(seed)
        n = int(os.environ.get("MESA_N_DRONES", "5"))
        g = int(os.environ.get("MESA_GRID_SIZE", "20"))
        s = int(os.environ.get("MESA_N_SURVIVORS", "3"))
        _mesa_model = DisasterZone(n_drones=n, grid_size=g, n_survivors=s)
    return _mesa_model


def _discard_model() -> None:
    if USE_MESA_SIM:
        from mcp_server import mesa_bridge

        mesa_bridge.reset_mesa_model()
        return

    global _mesa_model
    _mesa_model = None


@router.get("/mesa/snapshot")
async def mesa_snapshot(steps: int = 0, reset: bool = False) -> dict[str, Any]:
    """
    Return `SIRENModel.get_state()` JSON after advancing the Mesa model `steps` times.

    - **reset=1**: discard cached model and create a fresh `DisasterZone`.
    - **steps**: number of `model.step()` calls before serialising (0 = current state only).
    """
    global _mesa_model
    if reset:
        _discard_model()
    model = _get_model()
    if steps < 0 or steps > 500:
        raise HTTPException(status_code=400, detail="steps must be between 0 and 500")
    for _ in range(steps):
        model.step()
    return model.get_state()


@router.post("/mesa/reset")
async def mesa_reset() -> dict[str, str]:
    """Clear cached Mesa model; next snapshot creates a new `DisasterZone`."""
    _discard_model()
    return {"status": "ok", "message": "Mesa simulation cache cleared."}


@router.post("/mesa/step")
async def mesa_step(body: MesaStepBody = MesaStepBody()) -> dict[str, Any]:
    """
    Advance Mesa by ``steps`` then, when ``USE_MESA_SIM=1``, pull drones + survivors into ``WorldState``.
    """
    req = body
    model = _get_model()
    for _ in range(req.steps):
        model.step()
    out: dict[str, Any] = {
        "mesa_step": int(model.step_count),
        "confirmed_survivors": len(getattr(model, "confirmed_survivors", []) or []),
        "coverage_pct": float(model.coverage_pct()),
    }
    if USE_MESA_SIM:
        from mcp_server import mesa_bridge
        from mcp_server.world_state import world

        mesa_bridge.sync_world_from_mesa(world)
        out["pulled_to_world"] = True
    return out


@router.get("/mesa/metrics")
async def mesa_metrics() -> dict[str, Any]:
    """Lightweight metrics from the cached Mesa model (same as drone-sim `get_metrics()`)."""
    model = _get_model()
    return model.get_metrics()
