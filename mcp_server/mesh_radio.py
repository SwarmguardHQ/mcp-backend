"""
MeshRadio — simulates LoRa/mesh radio communication between drones.
Works entirely offline; no internet required.
"""
from __future__ import annotations
import random
from datetime import datetime, timezone
from utils.config import MESH_MESSAGE_MAX_LEN, DRONE_RECOVERY_SUCCESS_RATE
from mcp_server.world_state import world
from mcp_server.drone_simulator import DroneStatus


def broadcast_mesh_message(sender_id: str, message: str) -> dict:
    """
    Broadcast a short message to all active drones on the local mesh.
    Recipients are all non-offline drones (sender included for log parity).
    """
    msg = message[:MESH_MESSAGE_MAX_LEN]
    ts  = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = f"[MESH][{ts}] {sender_id}: {msg}"

    world.mesh_log.append(entry)
    recipients = []
    for drone in world.drones.values():
        if drone.status != DroneStatus.OFFLINE:
            drone.mission_log.append(entry)
            recipients.append(drone.drone_id)

    return {
        "broadcast":   entry,
        "recipients":  recipients,
        "note": "Message delivered to all active drones via mesh radio.",
    }


def attempt_drone_recovery(drone_id: str) -> dict:
    """
    Send a mesh wake signal to an offline drone.
    65 % success rate — simulates real-world mesh re-join uncertainty.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status != DroneStatus.OFFLINE:
        return {"message": "Drone is already online", "status": drone.status.value}

    success = random.random() < DRONE_RECOVERY_SUCCESS_RATE
    if success:
        recovered_battery = random.randint(15, 40)
        drone.recover(recovered_battery)
        broadcast_mesh_message(
            "COMMAND",
            f"{drone_id} recovered at ({drone.x},{drone.y}) bat={recovered_battery}%",
        )
        from mcp_server.mesa_bridge import notify_drone_changed

        notify_drone_changed(drone_id)
        return {
            "drone_id":  drone_id,
            "recovered": True,
            "battery":   recovered_battery,
            "position":  {"x": drone.x, "y": drone.y},
        }
    else:
        return {
            "drone_id":  drone_id,
            "recovered": False,
            "reason":    "No response to mesh wake signal. Try again or redistribute sectors.",
        }


def get_mesh_log() -> dict:
    """Return the full mesh broadcast history."""
    return {"mesh_log": world.mesh_log, "total_entries": len(world.mesh_log)}