"""
Central configuration for the SAR Swarm MCP backend.
All grid constants, thresholds, and scenarios parameters live here.
"""
from __future__ import annotations
import os
from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env_path)

# ── FastAPI server ─────────────────────────────────────────────────────────────
API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8000"))

# ── Grid world ────────────────────────────────────────────────────────────────
GRID_SIZE: int = 20                 # 20 × 20 cells
BATTERY_COST_PER_CELL: float = 3.0  # % per grid unit of distance
BATTERY_RESERVE_MIN: int = 25       # always keep this % before committing a move
BATTERY_LOW_THRESHOLD: int = 25     # trigger return-to-charge warning
BATTERY_CRITICAL_THRESHOLD: int = 10  # immediate recall

THERMAL_SCAN_RADIUS: float = 1.5    # cells
ACOUSTIC_SCAN_RADIUS: float = 1.0   # cells
SCAN_BATTERY_COST: int = 3          # % per thermal scan
ACOUSTIC_BATTERY_COST: int = 2      # % per acoustic scan

# ── Mesh / offline ────────────────────────────────────────────────────────────
MESH_MESSAGE_MAX_LEN: int = 200
DRONE_RECOVERY_SUCCESS_RATE: float = 0.65  # 65 % chance per attempt

# ── Charging stations ─────────────────────────────────────────────────────────
CHARGING_STATIONS: list[dict] = [
    {"id": "CS1", "x": 0, "y": 0},
    {"id": "CS2", "x": 9, "y": 0},
]

# ── Supply depots ─────────────────────────────────────────────────────────────
SUPPLY_DEPOTS: list[dict] = [
    {
        "id": "D1",
        "x": 0, "y": 0,
        "supplies": ["medical_kit", "water", "food", "rope", "tarp", "radio"],
        "capacity": 50,
    },
    {
        "id": "D2",
        "x": 3, "y": 9,
        "supplies": ["medical_kit", "water", "food"],
        "capacity": 30,
    },
]

ALL_SUPPLY_TYPES: list[str] = ["medical_kit", "water", "food", "rope", "tarp", "radio"]

# ── Survivor priorities ───────────────────────────────────────────────────────
PRIORITY_SCORES: dict[str, int] = {
    "critical": 100,
    "moderate": 60,
    "stable":   30,
}
SUPPLY_PRIORITY: dict[str, str] = {
    "critical": "medical_kit",
    "moderate": "water",
    "stable":   "food",
}

# ── Dynamic Scenario Loading ──────────────────────────────────────────────────
import importlib
scenario_name = os.getenv("SCENARIO", "default")

try:
    scenario_module = importlib.import_module(f"scenarios.{scenario_name}")
except ImportError:
    print(f"Warning: Scenario '{scenario_name}' not found. Falling back to default.")
    scenario_module = importlib.import_module("scenarios.default")

INITIAL_FLEET: list[dict] = getattr(scenario_module, "INITIAL_FLEET", [])
INITIAL_SURVIVORS: list[dict] = getattr(scenario_module, "INITIAL_SURVIVORS", [])
MISSION_PROMPT: str = getattr(scenario_module, "MISSION_PROMPT", "")

# ── Mesa / drone-sim bridge (optional; install mcp-backend[mesa]) ───────────
USE_MESA_SIM: bool = os.getenv("USE_MESA_SIM", "").lower() in ("1", "true", "yes")
# When > 0, each GET on /world/drones|map|metrics advances Mesa this many steps then pulls state.
MESA_STEPS_ON_SYNC: int = int(os.getenv("MESA_STEPS_ON_SYNC", "0"))
# Extra Mesa steps applied only on each ``GET /world/stream`` tick (live clock), then full pull sync.
MESA_STEPS_PER_STREAM_TICK: int = int(os.getenv("MESA_STEPS_PER_STREAM_TICK", "0"))
