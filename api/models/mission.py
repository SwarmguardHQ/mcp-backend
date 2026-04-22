"""
Pydantic schemas for the FastAPI layer.
All scenarios names and field names come from config — nothing is hardcoded.
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, field_validator
from utils.config import INITIAL_FLEET


# Derive valid scenarios names at import time from the scenarios package
def _available_scenarios() -> list[str]:
    import pkgutil, scenarios as _pkg
    return [
        m.name
        for m in pkgutil.iter_modules(_pkg.__path__)
        if not m.name.startswith("_")
    ]

scenarios = _available_scenarios()
ScenarioName = Enum(
    'ScenarioName',
    {name.upper(): name for name in scenarios},
    type=str
)

# class ScenarioName(str, Enum):
#     """Populated dynamically — no hardcoded scenarios names."""
#     pass
#
#
# # Build enum members from discovered scenarios modules
# for _name in _available_scenarios():
#     ScenarioName._value2member_map_  # touch the map
#     ScenarioName.__members__[_name] = ScenarioName(_name)  # type: ignore


# Valid drone IDs come from config — not hardcoded here
VALID_DRONE_IDS: list[str] = [d["id"] for d in INITIAL_FLEET]


# ── Request models ─────────────────────────────────────────────────────────────

class MissionRequest(BaseModel):
    scenarios: str
    custom_prompt: Optional[str] = None
    online_mode: bool = True

    @field_validator("scenarios")
    @classmethod
    def scenario_must_exist(cls, v: str) -> str:
        available = _available_scenarios()
        if v not in available:
            raise ValueError(f"Unknown scenarios '{v}'. Available: {available}")
        return v
    
    
class OperatorOverrideRequest(BaseModel):
    insight: str


class ToolCallRequest(BaseModel):
    """Direct tool call — for testing individual MCP tools via HTTP."""
    tool_name: str
    arguments: dict[str, Any] = {}


# ── Response models ────────────────────────────────────────────────────────────

class MissionStarted(BaseModel):
    mission_id: str
    scenario:   str
    status:     str = "running"
    stream_url: str


class MissionStatus(BaseModel):
    mission_id:    str
    scenario:      str
    status:        str          # running | complete | failed
    steps_logged:  int
    mission_complete: bool
    summary:       Optional[dict] = None


class ToolCallResponse(BaseModel):
    tool_name:  str
    arguments:  dict[str, Any]
    result:     dict[str, Any]
    error:      Optional[str] = None


class WorldSnapshot(BaseModel):
    map:       str
    drones:    list[dict]
    survivors: list[dict]


class HealthResponse(BaseModel):
    status:       str = "ok"
    mcp_server:   str = "stdio subprocess"
    scenarios:    list[str]
    drone_count:  int