"""
Battery tools — get_battery_status, return_to_charging_station, charge_drone.
"""
from __future__ import annotations
from utils.config import (
    BATTERY_COST_PER_CELL,
    BATTERY_LOW_THRESHOLD,
    BATTERY_CRITICAL_THRESHOLD,
)
from mcp_server.world_state import world
from mcp_server.drone_simulator import DroneStatus


def get_battery_status(drone_id: str) -> dict:
    """Battery % + estimated range + recommendation."""
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}

    est_range = int(drone.battery / BATTERY_COST_PER_CELL)

    if drone.battery <= BATTERY_CRITICAL_THRESHOLD:
        rec = "CRITICAL — RECALL IMMEDIATELY"
    elif drone.battery <= BATTERY_LOW_THRESHOLD:
        rec = "LOW — return to charging station soon"
    else:
        rec = "OK"

    return {
        "drone_id":        drone_id,
        "battery":         drone.battery,
        "estimated_range": est_range,
        "status":          drone.status.value,
        "recommendation":  rec,
    }


def return_to_charging_station(drone_id: str) -> dict:
    """
    Navigate drone to the nearest charging station and begin charging.
    If battery runs out en route, drone goes OFFLINE.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status == DroneStatus.OFFLINE:
        return {"error": "Drone is offline — cannot self-navigate"}

    station = world.nearest_charging_station(drone)
    cost    = drone.battery_cost_to(station["x"], station["y"], BATTERY_COST_PER_CELL)

    if drone.battery < cost:
        drone.go_offline(reason="Battery depleted before reaching charging station")
        return {
            "error":   "Drone ran out of battery before reaching station",
            "status":  "offline",
            "station": station["id"],
            "action":  "Use attempt_drone_recovery to try to bring it back online",
        }

    drone.battery -= cost
    drone.x, drone.y = station["x"], station["y"]
    drone.start_charging(station["id"])

    return {
        "drone_id":           drone_id,
        "charging_station":   station["id"],
        "position":           {"x": drone.x, "y": drone.y},
        "battery_on_arrival": drone.battery,
        "status":             "charging",
        "note":               "Call charge_drone to restore battery, then drone returns to idle.",
    }


def charge_drone(drone_id: str, charge_percent: int = 100) -> dict:
    """
    Restore battery (drone must already be at a charging station).
    charge_percent defaults to full charge.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status not in (DroneStatus.CHARGING, DroneStatus.IDLE):
        return {
            "error":   f"Drone must be charging or idle (current: {drone.status.value})",
            "action":  "Call return_to_charging_station first",
        }

    drone.finish_charging(min(100, max(1, charge_percent)))
    return {
        "drone_id": drone_id,
        "battery":  drone.battery,
        "status":   drone.status.value,
        "note":     "Drone is idle and ready for next mission.",
    }