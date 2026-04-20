"""
SwarmState — the shared digital blackboard for the SIREN swarm.

Architecture:
  - Strategist LLM writes to: search_grid[sector].priority  (the "pheromone")
  - Drone Agents write to:    search_grid[sector].claimed_by / .scanned
  - Safety Governor writes to: phase, drones (battery overrides)

All fields touched by parallel drone_agent_node executions use LangGraph
Annotated reducers so concurrent state updates merge correctly.
"""
from typing import TypedDict, List, Dict, Any, Optional, Annotated
from pydantic import BaseModel, Field


# ── Structured LLM Output Models ──────────────────────────────────────────────

class RescueDirective(BaseModel):
    """High-level rescue assignment issued by the Strategist in rescue phase."""
    drone_id: str = Field(
        description="The ID of the drone to assign this rescue mission to"
    )
    survivor_id: str = Field(description="The survivor ID to rescue")
    supply_type: str = Field(
        description=(
            "Supply type matched to condition: "
            "medical_kit (critical), water (moderate/stable), food (stable)"
        )
    )


class StrategyOutput(BaseModel):
    """
    Complete structured output from the Strategist LLM.
    In SEARCH phase: outputs priority_updates only.
    In RESCUE phase: outputs rescue_directive only.
    """
    thought: str = Field(
        description=(
            "Concise strategic reasoning — what environmental signals led to this decision? "
            "Do NOT mention drone names in search phase."
        )
    )
    priority_updates: Dict[str, float] = Field(
        description=(
            "SEARCH PHASE ONLY. Map of sector_id → priority float (0.0–10.0). "
            "Higher = more urgent. 0.0 = deprioritize/ignore. "
            "Only update sectors that are NOT yet scanned."
        ),
        default_factory=dict,
    )
    rescue_directive: Optional[RescueDirective] = Field(
        description=(
            "RESCUE PHASE ONLY. Assign one available drone to rescue one pending survivor. "
            "Select the closest idle drone to the most critical survivor."
        ),
        default=None,
    )


# ── Grid Cell (Digital Pheromone) ─────────────────────────────────────────────

class GridCell(TypedDict):
    """
    One sector on the shared pheromone map.
    - priority:   Written by Strategist. Drones swarm toward high-priority unclaimed cells.
    - claimed_by: Written by the Drone Agent before it begins flying. Prevents two drones
                  racing to the same sector.
    - scanned:    Set to True after thermal_scan completes. Permanent — never unset.
    """
    priority: float
    claimed_by: Optional[str]
    scanned: bool


# ── LangGraph Reducers ────────────────────────────────────────────────────────
# These are called automatically by LangGraph to merge state updates from
# parallel drone_agent_node executions.

def _merge_search_grid(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """
    Smart merge for the pheromone grid.
    Rules:
      - scanned=True is permanent and can never be reverted.
      - A scanned cell can still have its claimed_by released (set to None).
      - Un-scanned cells use the new value entirely.
    """
    merged = dict(old)
    for sector, cell in new.items():
        if sector not in merged:
            merged[sector] = cell
            continue
        existing = merged[sector]
        if existing.get("scanned"):
            # Permanent scan — only allow releasing a stale claim
            if cell.get("claimed_by") is None and existing.get("claimed_by") is not None:
                merged[sector] = {**existing, "claimed_by": None}
            # Block any attempt to un-scan or reprioritize a completed sector
        else:
            merged[sector] = cell
    return merged


def _merge_mission_log(old: List[str], new: List[str]) -> List[str]:
    """Append logs from parallel drone executions — order is best-effort."""
    return old + new


def _merge_drones(old: List[Dict], new: List[Dict]) -> List[Dict]:
    """Latest telemetry per drone ID wins (each drone only writes its own entry)."""
    merged: Dict[str, Dict] = {d["id"]: d for d in old}
    for d in new:
        merged[d["id"]] = d
    return list(merged.values())


def _merge_active_relays(old: Dict[str, str], new: Dict[str, Optional[str]]) -> Dict[str, str]:
    """
    Merge relay maps — each drone only writes its own main-drone key,
    so conflicts are impossible. Deletions use None as a sentinel value.
    """
    merged = {**old}
    for k, v in new.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    return merged


def _merge_signal_map(old: Dict[str, float], new: Dict[str, float]) -> Dict[str, float]:
    """
    Merge signal-strength updates from parallel drone relay deployments.
    Multiple drones can update different relay keys in the same tick — dict
    merge is safe because each drone writes its own 'relay_for_<drone_id>' key.
    """
    return {**old, **new}


# ── Core State Types ───────────────────────────────────────────────────────────

class Drone(TypedDict):
    """Live telemetry snapshot for one physical drone."""
    id: str
    battery: int
    x: int
    y: int
    status: str
    locked: bool
    payload: Optional[str]


class SwarmState(TypedDict):
    """
    The shared digital environment — the 'pheromone blackboard'.

    Strategist writes pheromones (priority values).
    Drone Agents read pheromones and write claims + scanned flags.
    Safety Governor enforces hard rules and transitions the phase field.
    join_node provides authoritative ground-truth sync after each wave.
    """
    # ── Fleet telemetry (merged by drone ID) ──────────────────────────────────
    drones: Annotated[List[Drone], _merge_drones]

    # ── Event log (appended from all nodes) ───────────────────────────────────
    mission_log: Annotated[List[str], _merge_mission_log]

    # ── Pheromone map (smart merge — scanned is permanent) ────────────────────
    search_grid: Annotated[Dict[str, GridCell], _merge_search_grid]

    # ── Signal-strength heuristic (sector_id → 0–100 %) ──────────────────────
    # Drones update this as they fly; relay_node reads it to self-position.
    # Uses _merge_signal_map so parallel relay deployments never conflict.
    signal_map: Annotated[Dict[str, float], _merge_signal_map]

    # ── Relay mesh tracking ───────────────────────────────────────────────────
    active_relays: Annotated[Dict[str, str], _merge_active_relays]

    # ── Rescue phase ──────────────────────────────────────────────────────────
    rescue_directive: Optional[Dict[str, Any]]   # last RescueDirective from Strategist

    # ── Mission metadata ──────────────────────────────────────────────────────
    mission_prompt: str
    detected_survivors: List[Dict[str, Any]]     # pending survivors (ground-truth from MCP)
    rescued_survivors: List[str]                  # rescued survivor IDs (ground-truth from MCP)
    phase: str                                    # "search" | "rescue" | "complete"