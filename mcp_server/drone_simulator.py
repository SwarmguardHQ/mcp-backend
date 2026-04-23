"""
Drone state machine — simulates physical drone behaviour.
Each Drone instance owns its own state; the registry keeps them all.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import threading


class DroneStatus(str, Enum):
    IDLE       = "idle"
    FLYING     = "flying"
    SCANNING   = "scanning"
    RELAY      = "relaying"
    CHARGING   = "charging"
    DELIVERING = "delivering"
    OFFLINE    = "offline"


class Drone:
    def __init__(
        self,
        drone_id: str,
        x: int = 0,
        y: int = 0,
        battery: int = 100,
        offline: bool = False,
    ):
        self.drone_id    = drone_id
        self.x           = x
        self.y           = y
        self.battery     = battery
        self.locked      = False
        self.status      = DroneStatus.OFFLINE if offline else DroneStatus.IDLE
        self.payload: Optional[str] = None
        self.last_seen   = _now()
        self.mission_log: list[str] = []
        self.assigned_sector: Optional[str] = None
        self._idle_timer: Optional[threading.Timer] = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        entry = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {self.drone_id}: {msg}"
        self.mission_log.append(entry)

    def distance_to(self, x: int, y: int) -> float:
        return math.hypot(x - self.x, y - self.y)

    def battery_cost_to(self, x: int, y: int, cost_per_cell: float = 3.0) -> int:
        return max(1, int(self.distance_to(x, y) * cost_per_cell))

    def can_reach(self, x: int, y: int, reserve: int = 15, cost_per_cell: float = 3.0) -> bool:
        return self.battery >= self.battery_cost_to(x, y, cost_per_cell) + reserve

    def _schedule_idle(self, delay: float = 5.0) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            
        def _to_idle():
            # Don't switch if offline, charging, relaying, or already idle
            if self.status not in (DroneStatus.OFFLINE, DroneStatus.CHARGING, DroneStatus.RELAY, DroneStatus.IDLE):
                self.status = DroneStatus.IDLE
                self.log("Task completed — auto-switched to IDLE")
                try:
                    from mcp_server.mesa_bridge import notify_drone_changed
                    notify_drone_changed(self.drone_id)
                except ImportError:
                    pass

        self._idle_timer = threading.Timer(delay, _to_idle)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    # ── state transitions ─────────────────────────────────────────────────────

    def move(self, x: int, y: int, cost_per_cell: float = 3.0) -> dict:
        if self.locked:
            return {"error": f"Drone {self.drone_id} is operationally locked."}
        cost = self.battery_cost_to(x, y, cost_per_cell)
        self.battery = max(0, self.battery - cost)
        self.x, self.y = x, y
        self.status = DroneStatus.FLYING
        self.last_seen = _now()
        self.log(f"Moved to ({x},{y}), battery={self.battery}%")
        self._schedule_idle(5.0)
        return {"new_position": {"x": x, "y": y}, "battery": self.battery}

    def start_scan(self, cost: int) -> None:
        self.battery = max(0, self.battery - cost)
        self.status = DroneStatus.SCANNING
        self.log(f"Scan at ({self.x},{self.y}), battery={self.battery}%")
        self._schedule_idle(5.0)

    def start_charging(self, station_id: str) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None
        self.status = DroneStatus.CHARGING
        self.log(f"Charging at {station_id}")

    def finish_charging(self, amount: int = 100) -> None:
        self.battery = min(100, self.battery + amount)
        self.status = DroneStatus.IDLE
        self.log(f"Charged to {self.battery}%")

    def pick_up(self, supply: str) -> None:
        self.payload = supply
        self.status = DroneStatus.DELIVERING
        self.log(f"Picked up {supply}")
        self._schedule_idle(5.0)

    def drop_off(self) -> Optional[str]:
        delivered = self.payload
        self.payload = None
        self.status = DroneStatus.FLYING
        self.log(f"Delivered {delivered}")
        self._schedule_idle(5.0)
        return delivered

    def go_offline(self, reason: str = "unknown") -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None
        self.status = DroneStatus.OFFLINE
        self.log(f"OFFLINE — {reason}")

    def recover(self, battery: int) -> None:
        self.status = DroneStatus.IDLE
        self.battery = battery
        self.log(f"Recovered — battery={battery}%")

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "drone_id":        self.drone_id,
            "position":        {"x": self.x, "y": self.y},
            "battery":         self.battery,
            "status":          self.status.value,
            "locked":          self.locked,
            "payload":         self.payload,
            "assigned_sector": self.assigned_sector,
            "last_seen":       self.last_seen,
        }


# ── Survivor ──────────────────────────────────────────────────────────────────

class Survivor:
    def __init__(self, survivor_id: str, x: int, y: int, condition: str):
        self.survivor_id = survivor_id
        self.x           = x
        self.y           = y
        self.condition   = condition
        self.detected    = False
        self.rescued     = False
        self.supplies_received: list[str] = []

    def to_dict(self) -> dict:
        return {
            "survivor_id":       self.survivor_id,
            "position":          {"x": self.x, "y": self.y},
            "condition":         self.condition,
            "detected":          self.detected,
            "rescued":           self.rescued,
            "supplies_received": self.supplies_received,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()