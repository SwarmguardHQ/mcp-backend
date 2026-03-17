"""
DroneRegistry — MCP tool-discovery layer.
The agent calls discover_drones() and gets back live fleet data.
Hard-coded IDs in the agent are forbidden; this is the only source of truth.
"""
from __future__ import annotations
from mcp_server.world_state import world
from mcp_server.drone_simulator import DroneStatus
from datetime import datetime, timezone


def discover_drones() -> dict:
    """Return all drones currently visible on the mesh network."""
    active  = [d.to_dict() for d in world.active_drones()]
    offline = [d.to_dict() for d in world.offline_drones()]
    return {
        "active_drones":  active,
        "offline_drones": offline,
        "total_active":   len(active),
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "note": (
            "Always use these drone_ids for tool calls. "
            "Never hard-code IDs."
        ),
    }


def get_all_drone_statuses() -> dict:
    """Fleet-wide snapshot for situational awareness."""
    return {
        "drones": [d.to_dict() for d in world.drones.values()],
        "summary": {
            "total":    len(world.drones),
            "active":   len(world.active_drones()),
            "offline":  len(world.offline_drones()),
            "charging": sum(1 for d in world.drones.values() if d.status == DroneStatus.CHARGING),
            "low_battery": [
                d.drone_id for d in world.drones.values()
                if d.battery <= 25 and d.status != DroneStatus.OFFLINE
            ],
        },
    }


def assign_sector(drone_id: str, sector_label: str) -> dict:
    """Assign a named sector to a drone for tracking coverage."""
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    drone.assigned_sector = sector_label
    drone.log(f"Assigned sector: {sector_label}")
    return {"drone_id": drone_id, "assigned_sector": sector_label}