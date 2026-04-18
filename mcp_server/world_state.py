"""
WorldState — the single source of truth for the simulation.
All tools read/write through this object instead of module-level globals.

Note: sys.path bootstrap lives in mcp_server/__init__.py
"""
from __future__ import annotations
import math
from utils.config import (
    GRID_SIZE,
    INITIAL_FLEET,
    INITIAL_SURVIVORS,
    SUPPLY_DEPOTS,
    CHARGING_STATIONS,
)
from mcp_server.drone_simulator import Drone, Survivor


class WorldState:
    def __init__(self):
        self.grid_size = GRID_SIZE
        self.drones: dict[str, Drone] = {}
        self.survivors: dict[str, Survivor] = {}
        self.supply_depots = SUPPLY_DEPOTS
        self.charging_stations = CHARGING_STATIONS
        self.mesh_log: list[str] = []
        self.explored_cells: set[tuple[int, int]] = set()
        self._reset()

    def _reset(self) -> None:
        self.explored_cells = set()
        self.drones = {
            cfg["id"]: Drone(
                drone_id=cfg["id"],
                x=cfg["x"],
                y=cfg["y"],
                battery=cfg["battery"],
                offline=cfg.get("offline", False),
            )
            for cfg in INITIAL_FLEET
        }
        self.survivors = {
            cfg["id"]: Survivor(
                survivor_id=cfg["id"],
                x=cfg["x"],
                y=cfg["y"],
                condition=cfg["condition"],
            )
            for cfg in INITIAL_SURVIVORS
        }
        for cfg in INITIAL_FLEET:
            self._mark_cell(int(cfg["x"]), int(cfg["y"]))
        from mcp_server import mesa_bridge

        mesa_bridge.rebuild_mesa_after_world_reset()

    def _mark_cell(self, x: int, y: int) -> None:
        if 0 <= x < self.grid_size and 0 <= y < self.grid_size:
            self.explored_cells.add((x, y))

    def mark_exploration_disc(self, cx: int, cy: int, radius: float) -> None:
        """Mark all integer grid cells within Euclidean distance `radius` of (cx, cy)."""
        r = int(math.ceil(radius)) + 1
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if math.hypot(dx, dy) <= radius + 1e-9:
                    self._mark_cell(cx + dx, cy + dy)

    def exploration_coverage_pct(self) -> float:
        total = self.grid_size * self.grid_size
        if total <= 0:
            return 0.0
        return round(100.0 * len(self.explored_cells) / total, 1)

    def get_drone(self, drone_id: str):
        return self.drones.get(drone_id)

    def get_survivor(self, survivor_id: str):
        return self.survivors.get(survivor_id)

    def active_drones(self) -> list:
        from mcp_server.drone_simulator import DroneStatus
        return [d for d in self.drones.values() if d.status != DroneStatus.OFFLINE]

    def offline_drones(self) -> list:
        from mcp_server.drone_simulator import DroneStatus
        return [d for d in self.drones.values() if d.status == DroneStatus.OFFLINE]

    def nearest_charging_station(self, drone):
        return min(
            self.charging_stations,
            key=lambda cs: drone.distance_to(cs["x"], cs["y"]),
        )

    def depot_at(self, x: int, y: int, supply_type: str):
        return next(
            (d for d in self.supply_depots
             if d["x"] == x and d["y"] == y and supply_type in d["supplies"]),
            None,
        )

    def detected_survivors_ranked(self) -> list:
        from utils.config import PRIORITY_SCORES
        detected = [s for s in self.survivors.values() if s.detected and not s.rescued]
        return sorted(detected, key=lambda s: PRIORITY_SCORES.get(s.condition, 0), reverse=True)

    def render_map(self) -> str:
        from mcp_server.drone_simulator import DroneStatus
        grid = [["." for _ in range(self.grid_size)] for _ in range(self.grid_size)]
        for depot in self.supply_depots:
            grid[depot["y"]][depot["x"]] = "DS"
        for cs in self.charging_stations:
            grid[cs["y"]][cs["x"]] = "CS"
        for s in self.survivors.values():
            if not s.rescued:
                grid[s.y][s.x] = "!" if s.condition == "critical" else "?"
        for drone in self.drones.values():
            if drone.status != DroneStatus.OFFLINE:
                grid[drone.y][drone.x] = drone.drone_id[6]
        rows = ["  0123456789"]
        for i, row in enumerate(reversed(grid)):
            rows.append(str(self.grid_size - 1 - i) + " " + "".join(row))
        legend = (
            "Legend: A/B/C/D/E=Drones  !=Critical  ?=Survivor  "
            "DS=Supply depot  CS=Charging station  .=Empty"
        )
        return "\n".join(rows) + "\n" + legend

    def reinitialize(self, scenario_name: str) -> None:
        """
        Dynamically re-syncs the WorldState singleton to a specific scenario configuration.
        This is critical for ensuring the dashboard displays the correct INITIAL_FLEET 
        and INITIAL_SURVIVORS for each mission.
        """
        import importlib
        try:
            # 1. Load the specific scenario module
            mod = importlib.import_module(f"scenarios.{scenario_name}")
            fleet = getattr(mod, "INITIAL_FLEET", [])
            survivors = getattr(mod, "INITIAL_SURVIVORS", [])

            # 2. Reset the live state
            self.explored_cells = set()
            self.drones = {
                cfg["id"]: Drone(
                    drone_id=cfg["id"],
                    x=cfg["x"],
                    y=cfg["y"],
                    battery=cfg["battery"],
                    offline=cfg.get("offline", False),
                )
                for cfg in fleet
            }
            self.survivors = {
                cfg["id"]: Survivor(
                    survivor_id=cfg["id"],
                    x=cfg["x"],
                    y=cfg["y"],
                    condition=cfg["condition"],
                )
                for cfg in survivors
            }

            # 3. Mark starting positions as explored
            for cfg in fleet:
                self._mark_cell(int(cfg["x"]), int(cfg["y"]))
            
            # 4. Sync sub-systems if available
            try:
                from mcp_server import mesa_bridge
                mesa_bridge.rebuild_mesa_after_world_reset()
            except ImportError:
                pass

        except Exception as e:
            print(f"Warning: Failed to reinitialize world for '{scenario_name}', using default reset. Error: {e}")
            self._reset()


world = WorldState()