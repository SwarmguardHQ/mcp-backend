"""
Movement tools — move_to, get_grid_map, assign_sector.
"""
from __future__ import annotations
from utils.config import GRID_SIZE, BATTERY_COST_PER_CELL, BATTERY_RESERVE_MIN
from mcp_server.world_state import world
from mcp_server.drone_simulator import DroneStatus


def move_to(drone_id: str, x: int, y: int) -> dict:
    """
    Fly a drone to grid coordinates (x, y).
    Fails if battery is insufficient to reach destination + keep reserve.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status == DroneStatus.OFFLINE:
        return {"error": "Drone is offline"}
    if drone.status == DroneStatus.CHARGING:
        return {"error": "Drone is charging — call charge_drone first to release it"}
    if drone.locked:
        return {"error": f"Drone {drone_id} is operationally LOCKED. Reason: Serving as critical relay or under system maintenance."}

    if not (0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE):
        return {"error": f"Coordinates ({x},{y}) are outside the grid (0–{GRID_SIZE-1})"}

    cost = drone.battery_cost_to(x, y, BATTERY_COST_PER_CELL)
    if drone.battery < cost + BATTERY_RESERVE_MIN:
        return {
            "error":      "Insufficient battery",
            "battery":    drone.battery,
            "required":   cost + BATTERY_RESERVE_MIN,
            "cost":       cost,
            "reserve":    BATTERY_RESERVE_MIN,
            "action":     "return_to_charging_station before proceeding",
        }

    result = drone.move(x, y, BATTERY_COST_PER_CELL)
    world._mark_cell(drone.x, drone.y)
    from mcp_server.mesa_bridge import notify_drone_changed

    notify_drone_changed(drone_id)
    return {
        "drone_id":     drone_id,
        **result,
        "status":       drone.status.value,
    }


def get_grid_map() -> dict:
    """Return a 2D ASCII map of the disaster zone."""
    return {
        "map":    world.render_map(),
        "width":  GRID_SIZE,
        "height": GRID_SIZE,
    }