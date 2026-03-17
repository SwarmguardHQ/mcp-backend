"""
/world routes — inspect the simulation state directly without running an agent.
Useful for debugging and live dashboards.

  GET /world/map          — ASCII grid map
  GET /world/drones       — full fleet status
  GET /world/survivors    — all survivor states
  GET /world/mesh-log     — mesh broadcast history
  GET /world/reset        — reset the world to initial state (dev only)
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from mcp_server.world_state import world

router = APIRouter(prefix="/world", tags=["world"])


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
    from mcp_server.tools.status_tools import get_swarm_summary
    return {
        "drones":  [d.to_dict() for d in world.drones.values()],
        "summary": get_swarm_summary(),
    }


@router.get("/drones/{drone_id}")
async def get_drone(drone_id: str):
    """Status of a specific drone."""
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