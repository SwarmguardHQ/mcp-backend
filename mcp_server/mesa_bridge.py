"""
WorldState ↔ drone-sim ``DisasterZone`` bridge.

Enable with ``USE_MESA_SIM=1`` (and install ``mcp-backend[mesa]``). The monorepo must
contain ``drone-sim/`` next to ``mcp-backend/``.

Behaviour (v1):
- On ``world._reset()``: create / rebuild Mesa model and align agents to ``INITIAL_FLEET``; seed thermal
  hotspots at ``INITIAL_SURVIVORS`` so Mesa confirmations line up with scenario cells.
- After MCP tools mutate drones: push position + battery + status into the Mesa agent.
- On ``GET /world/drones`` (and map/metrics): optionally advance Mesa ``MESA_STEPS_ON_SYNC`` steps,
  then pull drones + merge ``scanned_cells``; Mesa ``confirmed_survivors`` set ``Survivor.detected`` on matches.
- **Battery:** API moves use ``BATTERY_COST_PER_CELL`` (``utils.config``); Mesa idle drain uses
  ``BATTERY_PER_CELL`` / step constants in ``drone-sim``. With ``MESA_STEPS_ON_SYNC=0`` the API remains
  authoritative for drones; Mesa still drives thermal confirmations into survivors when stepping.
- ``GET /world/stream``: after the normal read-path sync, runs ``MESA_STEPS_PER_STREAM_TICK`` extra steps
  (optional live clock) then pulls again.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from utils.config import (
    GRID_SIZE,
    INITIAL_FLEET,
    INITIAL_SURVIVORS,
    USE_MESA_SIM,
    MESA_STEPS_ON_SYNC,
    MESA_STEPS_PER_STREAM_TICK,
)

if TYPE_CHECKING:
    from mcp_server.world_state import WorldState

_mesa_model: Any = None


def _repo_root() -> Path:
    # mcp-backend/mcp_server/mesa_bridge.py → parents[2] = monorepo root (contains drone-sim/)
    return Path(__file__).resolve().parents[2]


def _ensure_drone_sim_path() -> None:
    root = _repo_root()
    ds = root / "drone-sim"
    if not ds.is_dir():
        raise RuntimeError(f"drone-sim not found at {ds}")
    p = str(ds.resolve())
    if p not in sys.path:
        sys.path.insert(0, p)


def reset_mesa_model() -> None:
    global _mesa_model
    _mesa_model = None


def get_mesa_model() -> Any:
    """Singleton ``DisasterZone`` (lazy)."""
    global _mesa_model
    if not USE_MESA_SIM:
        raise RuntimeError("USE_MESA_SIM is disabled")
    if _mesa_model is None:
        _ensure_drone_sim_path()
        import numpy as np
        from simulation.grid import DisasterZone

        seed = int(os.environ.get("MESA_SIM_SEED", "42"))
        np.random.seed(seed)
        n = len(INITIAL_FLEET)
        n_survivors = int(os.environ.get("MESA_N_SURVIVORS", "3"))
        grid_sz = int(os.environ.get("MESA_GRID_SIZE", str(GRID_SIZE)))
        _mesa_model = DisasterZone(n_drones=n, grid_size=grid_sz, n_survivors=n_survivors)
        align_mesa_to_initial_fleet(_mesa_model)
        seed_mesa_thermal_from_world_survivors(_mesa_model)
    return _mesa_model


def seed_mesa_thermal_from_world_survivors(m: Any) -> None:
    """Boost thermal signal at each ``INITIAL_SURVIVORS`` cell so ABM confirmations map to API survivors."""
    for cfg in INITIAL_SURVIVORS:
        sx, sy = int(cfg["x"]), int(cfg["y"])
        m.thermal.add_survivor_hotspot(sx, sy)


def align_mesa_to_initial_fleet(m: Any) -> None:
    """Place Mesa drones on ``INITIAL_FLEET`` cells and match battery / offline flags."""
    for i, cfg in enumerate(INITIAL_FLEET):
        agent = m.get_drone(i)
        if agent is None:
            continue
        x, y = int(cfg["x"]), int(cfg["y"])
        m.grid.move_agent(agent, (x, y))
        agent.battery = float(cfg["battery"])
        if cfg.get("offline"):
            agent.status = "offline"
        else:
            agent.status = "idle"


def _fleet_index(drone_id: str) -> int | None:
    for i, cfg in enumerate(INITIAL_FLEET):
        if cfg["id"] == drone_id:
            return i
    return None


def notify_drone_changed(drone_id: str) -> None:
    """After any MCP tool mutates a drone, keep Mesa in sync (when ``USE_MESA_SIM``)."""
    if not USE_MESA_SIM:
        return
    from mcp_server.world_state import world as world_singleton

    push_drone_to_mesa(drone_id, world_singleton)


def push_drone_to_mesa(drone_id: str, world: "WorldState") -> None:
    """Mirror one ``Drone`` into the paired Mesa agent (grid + battery + offline)."""
    if not USE_MESA_SIM:
        return
    from mcp_server.drone_simulator import DroneStatus

    idx = _fleet_index(drone_id)
    if idx is None:
        return
    w = world.get_drone(drone_id)
    if w is None:
        return
    m = get_mesa_model()
    agent = m.get_drone(idx)
    if agent is None:
        return
    if w.status == DroneStatus.OFFLINE:
        agent.status = "offline"
        return
    m.grid.move_agent(agent, (int(w.x), int(w.y)))
    agent.battery = float(w.battery)
    agent.status = _mesa_status_from_world(w.status.value)


def _mesa_status_from_world(status: str) -> str:
    s = (status or "idle").lower()
    if s == "flying":
        return "active"
    if s == "relay":
        return "recalled"
    if s == "delivering":
        return "active"
    if s in ("idle", "scanning", "charging", "offline"):
        return s
    return "idle"


def _world_status_from_mesa(status: str) -> str:
    from mcp_server.drone_simulator import DroneStatus

    s = (status or "idle").lower()
    if s == "offline":
        return DroneStatus.OFFLINE.value
    if s == "charging":
        return DroneStatus.CHARGING.value
    if s in ("scanning", "active"):
        return DroneStatus.SCANNING.value
    if s == "recalled":
        return DroneStatus.RELAY.value
    if s == "critical":
        return DroneStatus.IDLE.value
    return DroneStatus.IDLE.value


def sync_world_from_mesa(world: "WorldState") -> None:
    """Overwrite ``world`` drone x/y/battery/status from Mesa; merge scanned cells."""
    if not USE_MESA_SIM:
        return
    from datetime import datetime, timezone
    from mcp_server.drone_simulator import DroneStatus

    m = get_mesa_model()
    for i, cfg in enumerate(INITIAL_FLEET):
        did = cfg["id"]
        w = world.get_drone(did)
        agent = m.get_drone(i)
        if w is None or agent is None:
            continue
        if agent.status == "offline":
            if w.status != DroneStatus.OFFLINE:
                w.go_offline("mesa sync")
            continue
        ax, ay = agent.pos
        w.x, w.y = int(ax), int(ay)
        w.battery = max(0, min(100, int(round(float(agent.battery)))))
        w.status = DroneStatus(_world_status_from_mesa(str(agent.status)))
        w.last_seen = datetime.now(timezone.utc).isoformat()

    world.explored_cells |= set(m.scanned_cells)
    sync_world_survivors_from_mesa(world)


def merge_mesa_exploration_into_world(world: "WorldState") -> None:
    """Add Mesa ``scanned_cells`` to ``world.explored_cells`` without moving drones."""
    if not USE_MESA_SIM:
        return
    m = get_mesa_model()
    world.explored_cells |= set(m.scanned_cells)
    sync_world_survivors_from_mesa(world)


def sync_world_survivors_from_mesa(world: "WorldState") -> None:
    """Set ``Survivor.detected`` when Mesa has a confirmed survivor at the same or adjacent cell."""
    if not USE_MESA_SIM:
        return
    m = get_mesa_model()
    confirmed = getattr(m, "confirmed_survivors", None) or []
    for entry in confirmed:
        x = int(entry.get("x", -999))
        y = int(entry.get("y", -999))
        if x < 0 or y < 0:
            continue
        candidates = [s for s in world.survivors.values() if not s.rescued]
        for s in candidates:
            if s.x == x and s.y == y:
                s.detected = True
                break
        else:
            for s in candidates:
                if abs(s.x - x) + abs(s.y - y) <= 1:
                    s.detected = True
                    break


def maybe_step_mesa_then_sync(world: "WorldState") -> None:
    """Advance Mesa by ``MESA_STEPS_ON_SYNC``; pull drones only when steps > 0."""
    if not USE_MESA_SIM:
        return
    m = get_mesa_model()
    for _ in range(max(0, MESA_STEPS_ON_SYNC)):
        m.step()
    if MESA_STEPS_ON_SYNC > 0:
        sync_world_from_mesa(world)
    else:
        merge_mesa_exploration_into_world(world)


def rebuild_mesa_after_world_reset() -> None:
    """Call after ``WorldState._reset()`` body (fleet rebuilt in memory)."""
    if not USE_MESA_SIM:
        reset_mesa_model()
        return
    reset_mesa_model()
    get_mesa_model()


def apply_stream_bonus_steps(world: "WorldState") -> None:
    """Advance Mesa by ``MESA_STEPS_PER_STREAM_TICK`` and pull drones + survivors (stream-only clock)."""
    if not USE_MESA_SIM or MESA_STEPS_PER_STREAM_TICK <= 0:
        return
    m = get_mesa_model()
    for _ in range(MESA_STEPS_PER_STREAM_TICK):
        m.step()
    sync_world_from_mesa(world)


def sim_visual_for_stream() -> dict[str, Any] | None:
    """Compact sim layer for dashboards: normalized heatmap + Mesa counters."""
    if not USE_MESA_SIM:
        return None
    import numpy as np

    m = get_mesa_model()
    g = np.asarray(m.thermal.grid, dtype=float)
    mx = float(np.max(g)) if g.size else 1.0
    if mx <= 0:
        mx = 1.0
    norm = np.clip(g / mx, 0.0, 1.0)
    return {
        "heatmap": norm.tolist(),
        "mesa_step": int(m.step_count),
        "mesa_coverage_pct": float(m.coverage_pct()),
        "confirmed_survivors": len(getattr(m, "confirmed_survivors", []) or []),
        "pending_detections": len(getattr(m, "pending_detections", {}) or {}),
    }
