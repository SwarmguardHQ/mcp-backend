"""
/world routes — inspect the simulation state directly without running an agent.
Useful for debugging and live dashboards.

  GET /world/stream       — SSE: periodic JSON ticks (drones, metrics, survivors, mesh tail)
  GET /world/map          — ASCII grid map
  GET /world/metrics      — grid exploration coverage + swarm summary
  GET /world/mesa/snapshot — optional Mesa `DisasterZone.get_state()` (requires .[mesa])
  POST /world/mesa/reset   — clear cached Mesa model (shared with MCP world when ``USE_MESA_SIM=1``)
  GET /world/drones       — full fleet status
  GET /world/survivors    — all survivor states
  GET /world/mesh-log     — mesh broadcast history
  GET /world/reset        — reset the world to initial state (dev only)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from utils.config import USE_MESA_SIM

from mcp_server.world_state import world

router = APIRouter(prefix="/world", tags=["world"])


def _mesa_sync_read_path() -> None:
    """When ``USE_MESA_SIM`` is set, advance Mesa (optional) and pull into ``world``."""
    if not USE_MESA_SIM:
        return
    from mcp_server import mesa_bridge

    mesa_bridge.maybe_step_mesa_then_sync(world)


@router.get("/stream")
async def stream_world(
    interval_ms: int = Query(500, ge=100, le=10_000, description="Milliseconds between ticks"),
):
    """
    Server-Sent Events — periodic world snapshot for live dashboards.

    Connect from the browser with ``EventSource`` (use the real API origin, not the Next
    ``/api`` rewrite, to avoid buffering). Each ``tick`` event carries JSON with ``drones``,
    ``summary``, ``metrics``, ``survivors``, ``mesh_log`` (last 24 lines), ``sim_visual`` (Mesa heatmap
    when ``USE_MESA_SIM=1``), and ``ts``. Optional extra Mesa steps per tick: ``MESA_STEPS_PER_STREAM_TICK``.

    Example::

        curl -N "http://127.0.0.1:8000/world/stream?interval_ms=1000"
    """

    async def _generate():
        interval = interval_ms / 1000.0
        tick_index = 0
        while True:
            _mesa_sync_read_path()
            sim_visual = None
            if USE_MESA_SIM:
                from mcp_server import mesa_bridge

                mesa_bridge.apply_stream_bonus_steps(world)
                sim_visual = mesa_bridge.sim_visual_for_stream()

            from mcp_server.tools.rescue_tools import get_rescue_priority_list
            from mcp_server.tools.status_tools import get_swarm_summary

            summary = get_swarm_summary()

            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "drones": [d.to_dict() for d in world.drones.values()],
                "summary": summary,
                "metrics": {
                    "grid_size": world.grid_size,
                    "explored_cells": len(world.explored_cells),
                    "total_cells": world.grid_size * world.grid_size,
                    "coverage_pct": world.exploration_coverage_pct(),
                    "summary": summary,
                },
                "survivors": {
                    "survivors": [s.to_dict() for s in world.survivors.values()],
                    "priority_list": get_rescue_priority_list(),
                },
                "mesh_log": world.mesh_log[-24:] if world.mesh_log else [],
                "sim_visual": sim_visual,
            }
            yield {"event": "tick", "data": json.dumps(payload)}
            tick_index += 1
            if tick_index % 60 == 0:
                yield {"event": "ping", "data": "{}"}
            await asyncio.sleep(interval)

    return EventSourceResponse(_generate(), headers={"Cache-Control": "no-cache, no-transform"})


@router.get("/metrics")
async def get_metrics():
    """
    Grid exploration coverage derived from drone positions, moves, and scan footprints.
    """
    _mesa_sync_read_path()
    from mcp_server.tools.status_tools import get_swarm_summary
    return {
        "grid_size": world.grid_size,
        "explored_cells": len(world.explored_cells),
        "total_cells": world.grid_size * world.grid_size,
        "coverage_pct": world.exploration_coverage_pct(),
        "summary": get_swarm_summary(),
    }


@router.get("/map")
async def get_map():
    """Current ASCII grid map with all drone/survivor positions."""
    return {
        "map":    world.render_map(),
        "width":  world.grid_size,
        "height": world.grid_size,
    }


@router.get("/drones")
async def get_drones():
    """Full status of every drone in the fleet."""
    _mesa_sync_read_path()
    from mcp_server.tools.status_tools import get_swarm_summary
    return {
        "drones":  [d.to_dict() for d in world.drones.values()],
        "summary": get_swarm_summary(),
    }


@router.get("/drones/{drone_id}")
async def get_drone(drone_id: str):
    """Status of a specific drone."""
    _mesa_sync_read_path()
    drone = world.get_drone(drone_id)
    if not drone:
        raise HTTPException(
            status_code=404,
            detail=f"Drone {drone_id!r} not found. Active drones: {list(world.drones.keys())}",
        )
    return drone.to_dict()


@router.get("/survivors")
async def get_survivors():
    """All survivor states including detection and rescue status."""
    from mcp_server.tools.rescue_tools import get_rescue_priority_list
    return {
        "survivors":     [s.to_dict() for s in world.survivors.values()],
        "priority_list": get_rescue_priority_list(),
    }


@router.get("/mesh-log")
async def get_mesh_log():
    """Full mesh radio broadcast history."""
    return {
        "mesh_log":     world.mesh_log,
        "total_entries": len(world.mesh_log),
    }


@router.post("/reset")
async def reset_world():
    """
    Reset the simulation to its initial state.
    Use between scenarios runs to get a clean world.
    """
    world._reset()
    return {"status": "reset", "message": "World state restored to initial configuration."}