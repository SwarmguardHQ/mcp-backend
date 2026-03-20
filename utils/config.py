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
GRID_SIZE: int = 20                 # 20 x 20 cells
BATTERY_COST_PER_CELL: float = 3.0  # % per grid unit of distance
BATTERY_RESERVE_MIN: int = 15       # always keep this % before committing a move
BATTERY_LOW_THRESHOLD: int = 20     # trigger return-to-charge warning
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
    {"id": "CS2", "x": 19, "y": 0},
]

# ── Supply depots ─────────────────────────────────────────────────────────────
SUPPLY_DEPOTS: list[dict] = [
    {
        "id": "D1",
        "x": 0, "y": 0,
        "supplies": ["medical_kit", "water", "food"],
        "capacity": 50,
    },
    {
        "id": "D2",
        "x": 19, "y": 19,
        "supplies": ["rope", "tarp", "radio"],
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

# ── Initial drone fleet ───────────────────────────────────────────────────────
INITIAL_FLEET: list[dict] = [
    {"id": "DRONE_ALPHA",   "x": 0, "y": 0, "battery": 95},
    {"id": "DRONE_BRAVO",   "x": 19, "y": 0, "battery": 80},
    {"id": "DRONE_CHARLIE", "x": 0, "y": 19, "battery": 70},
    {"id": "DRONE_DELTA",   "x": 19, "y": 19, "battery": 60},
    {"id": "DRONE_ECHO",    "x": 5, "y": 0, "battery": 0,  "offline": True},
]

# ── Survivors (hidden from agent — discovered via scan) ───────────────────────
INITIAL_SURVIVORS: list[dict] = [
    {"id": "S1", "x": 6, "y": 14, "condition": "critical"},
    {"id": "S2", "x": 16, "y": 4, "condition": "stable"},
    {"id": "S3", "x": 10, "y": 10, "condition": "critical"},
    {"id": "S4", "x": 2, "y": 18, "condition": "moderate"},
    {"id": "S5", "x": 14, "y": 16, "condition": "stable"},
]
