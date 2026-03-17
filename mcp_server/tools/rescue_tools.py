"""
Rescue tools — get_rescue_priority_list, mark_survivor_rescued.
Scenario covered: rescue prioritisation (multiple survivors, earthquake context).
"""
from __future__ import annotations
from utils.config import PRIORITY_SCORES, SUPPLY_PRIORITY
from mcp_server.world_state import world
from mcp_server.mesh_radio import broadcast_mesh_message


def get_rescue_priority_list() -> dict:
    """
    Return all detected (not yet rescued) survivors ranked by medical urgency.
    'critical' > 'moderate' > 'stable'.
    Includes the recommended supply type for each survivor.
    """
    ranked = world.detected_survivors_ranked()
    undetected = sum(1 for s in world.survivors.values() if not s.detected)

    return {
        "priority_list": [
            {
                "rank":                i + 1,
                "survivor_id":        s.survivor_id,
                "condition":          s.condition,
                "position":           {"x": s.x, "y": s.y},
                "priority_score":     PRIORITY_SCORES.get(s.condition, 0),
                "recommended_supply": SUPPLY_PRIORITY.get(s.condition, "water"),
            }
            for i, s in enumerate(ranked)
        ],
        "undetected_survivors": undetected,
        "note": (
            f"There are still {undetected} undetected survivor(s). "
            "Continue scanning to locate them."
        ) if undetected else "All survivors have been located.",
    }


def mark_survivor_rescued(survivor_id: str, drone_id: str) -> dict:
    """
    Mark a survivor as successfully rescued and broadcast to the swarm.
    """
    survivor = world.get_survivor(survivor_id)
    if not survivor:
        return {"error": f"Survivor {survivor_id} not found"}
    if survivor.rescued:
        return {"error": f"Survivor {survivor_id} is already marked as rescued."}

    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}

    survivor.rescued = True
    drone.log(f"Marked {survivor_id} as RESCUED")

    remaining = [s for s in world.survivors.values() if not s.rescued]
    critical_remaining = [s for s in remaining if s.condition == "critical"]

    broadcast_mesh_message(
        drone_id,
        f"RESCUED {survivor_id} ({survivor.condition}). {len(remaining)} remain.",
    )

    return {
        "survivor_id":         survivor_id,
        "status":              "rescued",
        "supplies_received":   survivor.supplies_received,
        "remaining_survivors": len(remaining),
        "critical_remaining":  len(critical_remaining),
        "mission_complete":    len(remaining) == 0,
    }