"""
Scan tools — thermal_scan, acoustic_scan.
Survivors are only visible to the agent after being detected here.
"""
from __future__ import annotations
from utils.config import (
    THERMAL_SCAN_RADIUS,
    ACOUSTIC_SCAN_RADIUS,
    SCAN_BATTERY_COST,
    ACOUSTIC_BATTERY_COST,
    PRIORITY_SCORES,
)
from mcp_server.world_state import world
from mcp_server.drone_simulator import DroneStatus
from mcp_server.mesh_radio import broadcast_mesh_message


def thermal_scan(drone_id: str) -> dict:
    """
    Infrared scan at drone's current position.
    Detects survivors within THERMAL_SCAN_RADIUS cells.
    Automatically broadcasts detections via mesh radio.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status == DroneStatus.OFFLINE:
        return {"error": "Drone is offline"}

    drone.start_scan(SCAN_BATTERY_COST)
    found = []

    for s in world.survivors.values():
        if drone.distance_to(s.x, s.y) <= THERMAL_SCAN_RADIUS and not s.rescued:
            s.detected = True
            found.append({
                "survivor_id": s.survivor_id,
                "position":    {"x": s.x, "y": s.y},
                "condition":   s.condition,
                "priority":    PRIORITY_SCORES.get(s.condition, 0),
            })

    if found:
        coords = ", ".join(f"{f['survivor_id']}@({f['position']['x']},{f['position']['y']})" for f in found)
        broadcast_mesh_message(drone_id, f"THERMAL DETECT: {coords}")

    return {
        "drone_id":           drone_id,
        "scan_position":      {"x": drone.x, "y": drone.y},
        "scan_radius":        THERMAL_SCAN_RADIUS,
        "survivors_detected": found,
        "battery_remaining":  drone.battery,
    }


def acoustic_scan(drone_id: str) -> dict:
    """
    Vibration/acoustic scan — detects survivors through rubble.
    Narrower radius than thermal; useful as follow-up for critical cases.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status == DroneStatus.OFFLINE:
        return {"error": "Drone is offline"}

    drone.start_scan(ACOUSTIC_BATTERY_COST)
    found = []

    for s in world.survivors.values():
        if drone.distance_to(s.x, s.y) <= ACOUSTIC_SCAN_RADIUS and not s.rescued:
            s.detected = True
            found.append({
                "survivor_id":     s.survivor_id,
                "position":        {"x": s.x, "y": s.y},
                "condition":       s.condition,
                "detection_method": "acoustic",
            })

    if found:
        coords = ", ".join(f"{f['survivor_id']}@({f['position']['x']},{f['position']['y']})" for f in found)
        broadcast_mesh_message(drone_id, f"ACOUSTIC DETECT: {coords}")

    return {
        "drone_id":           drone_id,
        "scan_position":      {"x": drone.x, "y": drone.y},
        "scan_radius":        ACOUSTIC_SCAN_RADIUS,
        "survivors_detected": found,
        "battery_remaining":  drone.battery,
    }