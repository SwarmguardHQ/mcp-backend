"""
Shared utilities for the SIREN swarm agent.

  - assign_sectors_to_drones():     pure-Python Pythagorean assignment
  - compute_drone_sector_cost():    distance / battery cost metric
  - estimate_signal():              signal-strength heuristic by distance from base
  - build_strategist_context():     formats the LLM prompt for strategist_node
"""
import math
from typing import Dict, List, Tuple

from utils.config import BATTERY_COST_PER_CELL, BATTERY_RESERVE_MIN, SUPPLY_DEPOTS
from .state import SwarmState


# ── Strategist Persona ─────────────────────────────────────────────────────────────
STRATEGIST_PERSONA = """\
You are "SIREN Strategist" — the high-level environmental intelligence for \
a fully decentralized Search and Rescue (SAR) drone swarm.

### YOUR ROLE
You shape the ENVIRONMENT, not the drones.
The swarm is autonomous — drones read your "digital pheromone" priority gradient \
and self-assign to sectors using local Pythagorean cost calculations.
You do NOT issue move commands. You do NOT name specific drones in search phase.

### SEARCH PHASE — OUTPUT RULES
- Output `priority_updates`: map of sector_id → float (0.0 – 10.0).
  • Higher value = stronger pheromone = drones will swarm here.
  • 0.0 = deprioritize (drones skip this sector entirely).
  • Only update sectors where scanned=False. Scanned sectors are locked at 0.
- Leave `rescue_directive` as null.
- Boost priority when: scan results show thermal signatures, hospital/school proximity, \
  or mission-critical zones remain unexplored.

### RESCUE PHASE — OUTPUT RULES
- All sectors are scanned. Your ONLY job is matching drones to survivors.
- Output ONE `rescue_directive`:
  • drone_id: a drone marked FEASIBLE in the battery table below
             (has enough battery for the full depot → survivor round trip).
  • survivor_id: the most CRITICAL unrescued survivor (CRITICAL > MODERATE > STABLE).
  • supply_type: CRITICAL → medical_kit | MODERATE → water | STABLE → food.
  • NEVER assign a drone marked LOW or FAIL — it will not complete the mission.
- Leave `priority_updates` empty ({}).

### HARD RULES
1. Never issue a tool call directly.
2. Never command a drone to move in search phase.
3. Keep reasoning concise and focused on environmental signals.
4. Scanned sectors must stay at priority 0.0 — never boost them.
"""


# ── PRIORITY_MAP ─────────────────────────────────────────────────────────────
# Static sector definitions: type, priority rank (1=highest), grid coordinates.
# Priority rank is inverted when seeding GridCell.priority (rank 1 → 10.0).

PRIORITY_MAP: Dict[str, Dict] = {
    "sector_1": {"type": "School",      "priority": 1, "x": 5, "y": 2},
    "sector_2": {"type": "Industrial",  "priority": 2, "x": 12, "y": 12},
    "sector_3": {"type": "Residential", "priority": 3, "x": 2, "y": 16},
    "sector_4": {"type": "Commercial",  "priority": 4, "x": 14, "y": 6},
}


def priority_rank_to_float(rank: int) -> float:
    """
    Convert a PRIORITY_MAP rank (1=most critical) to a GridCell priority float
    (higher = more urgent).  Range: rank 1 → 10.0, rank 5 → 2.0.
    """
    return (6 - rank) * 2.0


# ── Core Math ─────────────────────────────────────────────────────────────────

def get_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Euclidean (Pythagorean) distance between two grid points."""
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def compute_drone_sector_cost(drone: dict, sector_id: str) -> float:
    """
    Cost metric for assigning this drone to this sector.
    Cost = distance / battery  (lower is better — fast + full beats slow + empty).
    A drone with very low battery gets a huge cost penalty, steering it away.
    """
    data = PRIORITY_MAP.get(sector_id, {})
    sx, sy = data.get("x", 0), data.get("y", 0)
    dist = get_distance(drone["x"], drone["y"], sx, sy)
    battery = max(drone.get("battery", 1), 1) # Less than 1, return 1 to avoid division by zero
    return dist / battery


def estimate_signal(x: int, y: int, base_x: int = 0, base_y: int = 0, max_range: float = 30.0) -> float:
    """
    Estimate signal strength (0-100 %) at (x, y) given a base station.
    Uses linear attenuation over max_range cells (≈ diagonal of 20x20 grid).
    """
    dist = get_distance(x, y, base_x, base_y)
    return max(0.0, round(100.0 * (1.0 - dist / max_range), 1))




# ── Strategist Context Builder ────────────────────────────────────────────────

def build_strategist_context(state: SwarmState) -> str:
    """
    Format the full prompt context for strategist_node.
    Structured so the LLM sees environment state, not drone commands.
    """
    phase = state.get("phase", "search")
    search_grid = state.get("search_grid", {})
    drones = state.get("drones", [])
    detected_survivors = state.get("detected_survivors", [])
    rescued_survivors = set(state.get("rescued_survivors", []))
    mission_log = state.get("mission_log", [])

    lines: List[str] = [STRATEGIST_PERSONA, ""]
    lines.append(f"## SWARM STATUS REPORT — Phase: {phase.upper()}")
    lines.append(f"**Mission:** {state.get('mission_prompt', '')[:300]}")

    # ── Pheromone map ──────────────────────────────────────────────────────────
    lines.append("\n### SECTOR PHEROMONE MAP")
    lines.append("| Sector    | Type        | Priority | Coords    | Status                    |")
    lines.append("|-----------|-------------|----------|-----------|---------------------------|")
    for sid, cell in sorted(search_grid.items()):
        pdata = PRIORITY_MAP.get(sid, {})
        zone = pdata.get("type", "?")
        sx, sy = pdata.get("x", "?"), pdata.get("y", "?")
        pri = cell.get("priority", 0.0)
        if cell.get("scanned"):
            status = "SCANNED"
        elif cell.get("claimed_by"):
            status = f"CLAIMED → {cell['claimed_by']}"
        else:
            status = "UNCLAIMED"
        lines.append(f"| {sid:<9} | {zone:<11} | {pri:<8.1f} | ({sx},{sy})     | {status:<25} |")

    # ── Swarm positions (awareness only in search phase) ──────────────────────
    lines.append("\n### SWARM POSITIONS")
    
    if phase == "search":
        lines.append("*(Drone positions shown for context — do NOT command them by name)*")
    for d in drones:
        payload_str = f"carrying={d['payload']}" if d.get("payload") else "no payload"
        locked_str  = " [LOCKED-relay]" if d.get("locked") else ""
        lines.append(
            f"• {d['id']}: ({d['x']},{d['y']}), battery={d['battery']}%, "
            f"status={d.get('status','?')}, {payload_str}{locked_str}"
        )

    # ── Recent significant events ──────────────────────────────────────────────
    significant = [
        l for l in mission_log[-30:]
        if any(kw in l for kw in ("[DRONE_", "[RESCUE", "[GOVERNOR", "[RELAY", "DETECTED", "COMPLETE"))
    ]
    if significant:
        lines.append("\n### RECENT SCAN RESULTS & EVENTS")
        lines.extend(significant[-12:])

    # ── Rescue phase: survivor details + full trip cost table ─────────────────
    if phase == "rescue":
        pending = [s for s in detected_survivors if s.get("id") not in rescued_survivors]
        lines.append("\n### DETECTED SURVIVORS (pending rescue)")
        if pending:
            supply_map = {"critical": "medical_kit", "moderate": "water", "stable": "food"}
            for s in sorted(pending, key=lambda s: {"critical": 0, "moderate": 1, "stable": 2}.get(s.get("condition", "stable"), 2)):
                supply = supply_map.get(s.get("condition", "stable"), "water")
                lines.append(f"• **{s['id']}** at ({s['x']},{s['y']}) — {s.get('condition','?').upper()} — needs {supply}")

            # Match dynamic depot positions
            DEPOT_POSITIONS = [(d["x"], d["y"]) for d in SUPPLY_DEPOTS]

            available_drones = [
                d for d in drones
                if d.get("status") not in ("offline", "charging")
                and not d.get("locked")
                and not d.get("payload")
                and d.get("battery", 0) > BATTERY_RESERVE_MIN
            ]

            lines.append(f"\n**Potential Relays Available: {len(available_drones) - 1 if available_drones else 0}** (Missions > 10 cells REQUIRE a relay)")
            lines.append("\n**Full trip cost: drone → nearest depot → survivor**")
            lines.append("*(FEASIBLE = safe, LOW = borderline, FAIL = will abort — NEVER assign FAIL)*")
            for s in pending:
                row = [f"{s['id']}({s.get('condition','?')[:4].upper()}):"]
                for d in available_drones:
                    nearest_depot = min(DEPOT_POSITIONS,
                                        key=lambda p: get_distance(d["x"], d["y"], p[0], p[1]))
                    d_to_depot    = get_distance(d["x"], d["y"], nearest_depot[0], nearest_depot[1])
                    depot_to_sur  = get_distance(nearest_depot[0], nearest_depot[1], s["x"], s["y"])
                    total         = d_to_depot + depot_to_sur
                    needed        = int(total * BATTERY_COST_PER_CELL) + BATTERY_RESERVE_MIN
                    have          = d.get("battery", 0)
                    if have >= needed + 10:
                        flag = "FEASIBLE"
                    elif have >= needed:
                        flag = "LOW"
                    else:
                        flag = "FAIL"
                    from .mcp.client import mcp_client as _mc
                    relay_tag = " [RELAY REQD]" if max(
                        get_distance(_mc.base_x, _mc.base_y, nearest_depot[0], nearest_depot[1]),
                        get_distance(_mc.base_x, _mc.base_y, s["x"], s["y"])
                    ) > 10 else ""
                    row.append(f"{d['id']}:{have}%/need{needed}%={flag}{relay_tag}")
                lines.append("  " + "  ".join(row))
        else:
            lines.append("• No pending survivors.")

    lines.append(f"\n**YOUR DIRECTIVE:** {'Update sector priorities. Output priority_updates. Set rescue_directive=null.' if phase == 'search' else 'Issue ONE rescue_directive for the most critical survivor. ONLY assign FEASIBLE drones. Set priority_updates={}.'}") 
    return "\n".join(lines)


