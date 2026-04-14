"""
Supply tools — collect_supplies, deliver_supplies, list_supply_depots.
Scenario covered: supplies collection.
"""
from __future__ import annotations
from utils.config import ALL_SUPPLY_TYPES, SUPPLY_DEPOTS, PRIORITY_SCORES
from mcp_server.world_state import world
from mcp_server.drone_simulator import DroneStatus


def list_supply_depots() -> dict:
    """Return all supply depot locations and available items."""
    return {"depots": SUPPLY_DEPOTS}


def collect_supplies(drone_id: str, supply_type: str) -> dict:
    """
    Pick up a supply item from a depot at the drone's current position.
    The drone must be standing on a depot cell that stocks the requested item.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status == DroneStatus.OFFLINE:
        return {"error": "Drone is offline"}
    if drone.payload:
        return {
            "error":   f"Drone is already carrying '{drone.payload}'",
            "action":  "Deliver current payload first, then collect a new item.",
        }
    if supply_type not in ALL_SUPPLY_TYPES:
        return {"error": f"Unknown supply type '{supply_type}'", "valid": ALL_SUPPLY_TYPES}

    depot = world.depot_at(drone.x, drone.y, supply_type)
    if not depot:
        available_depots = [
            {"id": d["id"], "x": d["x"], "y": d["y"], "supplies": d["supplies"]}
            for d in SUPPLY_DEPOTS
        ]
        return {
            "error":           f"No depot with '{supply_type}' at ({drone.x},{drone.y})",
            "available_depots": available_depots,
            "action":          f"Move drone to a depot that stocks '{supply_type}' first.",
        }

    drone.pick_up(supply_type)
    from mcp_server.mesa_bridge import notify_drone_changed

    notify_drone_changed(drone_id)
    return {
        "drone_id":   drone_id,
        "collected":  supply_type,
        "from_depot": depot["id"],
        "position":   {"x": drone.x, "y": drone.y},
        "status":     drone.status.value,
    }


def deliver_supplies(drone_id: str, survivor_id: str) -> dict:
    """
    Deliver the drone's current payload to a survivor at its position.
    Drone must be within 1.5 cells of the survivor.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if not drone.payload:
        return {"error": "Drone is not carrying any supplies", "action": "Call collect_supplies first."}

    survivor = world.get_survivor(survivor_id)
    if not survivor:
        return {"error": f"Survivor {survivor_id} not found — has it been detected yet?"}
    if survivor.rescued:
        return {"error": f"Survivor {survivor_id} has already been rescued."}

    dist = drone.distance_to(survivor.x, survivor.y)
    if dist > 1.5:
        return {
            "error":       "Drone is too far from survivor to deliver",
            "drone_at":    {"x": drone.x, "y": drone.y},
            "survivor_at": {"x": survivor.x, "y": survivor.y},
            "distance":    round(dist, 2),
            "action":      f"Move drone to ({survivor.x},{survivor.y}) first.",
        }

    delivered = drone.drop_off()
    survivor.supplies_received.append(delivered)
    from mcp_server.mesa_bridge import notify_drone_changed

    notify_drone_changed(drone_id)

    return {
        "drone_id":    drone_id,
        "delivered":   delivered,
        "survivor_id": survivor_id,
        "condition":   survivor.condition,
        "priority":    PRIORITY_SCORES.get(survivor.condition, 0),
        "supplies_received_by_survivor": survivor.supplies_received,
    }