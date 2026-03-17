"""
Status tools — get_drone_status, get_mission_log.
Scenario covered: view the status of other agents.
"""
from __future__ import annotations
from mcp_server.world_state import world
from mcp_server.drone_registry import discover_drones, get_all_drone_statuses, assign_sector


def get_drone_status(drone_id: str) -> dict:
    """Detailed status for a single drone."""
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found — call discover_drones to see active IDs"}
    return drone.to_dict()


def get_mission_log(drone_id: str) -> dict:
    """Full event log for a specific drone."""
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    return {
        "drone_id": drone_id,
        "log":      drone.mission_log,
        "entries":  len(drone.mission_log),
    }


def get_swarm_summary() -> dict:
    """High-level swarm health — useful for the agent's periodic check-ins."""
    from mcp_server.drone_simulator import DroneStatus
    drones = list(world.drones.values())
    survivors = list(world.survivors.values())

    return {
        "drones": {
            "total":    len(drones),
            "active":   sum(1 for d in drones if d.status != DroneStatus.OFFLINE),
            "offline":  sum(1 for d in drones if d.status == DroneStatus.OFFLINE),
            "charging": sum(1 for d in drones if d.status == DroneStatus.CHARGING),
            "low_battery": [
                {"id": d.drone_id, "battery": d.battery}
                for d in drones
                if d.battery <= 25 and d.status != DroneStatus.OFFLINE
            ],
        },
        "survivors": {
            "total":      len(survivors),
            "detected":   sum(1 for s in survivors if s.detected),
            "rescued":    sum(1 for s in survivors if s.rescued),
            "critical_unrescued": sum(
                1 for s in survivors if s.condition == "critical" and not s.rescued
            ),
        },
        "mission_complete": all(s.rescued for s in survivors),
    }