"""
MissionPlanner — decomposes the 10×10 grid into sectors and assigns
them to drones based on battery level and current position.

The agent calls plan_sectors() to get a structured assignment before
issuing move_to / scan tool calls.
"""
from __future__ import annotations
import math
from utils.config import BATTERY_COST_PER_CELL, BATTERY_RESERVE_MIN


# ── Sector definitions ─────────────────────────────────────────────────────────
# Grid split into 4 quadrants + centre — labels used for assign_sector tool.

SECTORS: dict[str, dict] = {
    "NW": {"x_range": (0, 4), "y_range": (5, 9), "centre": (2, 7)},
    "NE": {"x_range": (5, 9), "y_range": (5, 9), "centre": (7, 7)},
    "SW": {"x_range": (0, 4), "y_range": (0, 4), "centre": (2, 2)},
    "SE": {"x_range": (5, 9), "y_range": (0, 4), "centre": (7, 2)},
    "CT": {"x_range": (3, 6), "y_range": (3, 6), "centre": (5, 5)},
}


def plan_sectors(drones: list[dict]) -> list[dict]:
    """
    Given a list of active drone dicts (from discover_drones),
    return an optimal sector assignment.

    Rules:
    - Skip drones with battery ≤ 20% (should be charging).
    - Assign closer sectors to lower-battery drones.
    - Each sector gets at most one drone.
    - Returns list of {drone_id, sector, centre, can_reach, battery_needed}.
    """
    eligible = [d for d in drones if d["battery"] > 25]
    assignments = []
    used_sectors: set[str] = set()

    # Sort by battery ascending — low battery gets first pick of nearby sectors
    eligible.sort(key=lambda d: d["battery"])

    for drone in eligible:
        dx, dy = drone["position"]["x"], drone["position"]["y"]
        best_sector = None
        best_dist   = float("inf")

        for label, sector in SECTORS.items():
            if label in used_sectors:
                continue
            cx, cy = sector["centre"]
            dist = math.hypot(cx - dx, cy - dy)
            if dist < best_dist:
                best_dist   = dist
                best_sector = label

        if not best_sector:
            break

        used_sectors.add(best_sector)
        sector      = SECTORS[best_sector]
        cx, cy      = sector["centre"]
        cost        = max(1, int(math.hypot(cx - dx, cy - dy) * BATTERY_COST_PER_CELL))
        can_reach   = drone["battery"] >= cost + BATTERY_RESERVE_MIN

        assignments.append({
            "drone_id":      drone["drone_id"],
            "sector":        best_sector,
            "centre":        {"x": cx, "y": cy},
            "distance":      round(best_dist, 2),
            "battery_needed": cost + BATTERY_RESERVE_MIN,
            "can_reach":     can_reach,
            "reasoning":     (
                f"{drone['drone_id']} has {drone['battery']}% battery at "
                f"({dx},{dy}). Nearest unassigned sector {best_sector} "
                f"centre=({cx},{cy}) costs ~{cost}% + {BATTERY_RESERVE_MIN}% reserve. "
                + ("ASSIGNABLE." if can_reach else "CANNOT REACH — send to charger first.")
            ),
        })

    return assignments


def scan_waypoints(sector_label: str) -> list[dict]:
    """
    Return a list of (x,y) waypoints that give full thermal coverage
    of a sector with 1.5-cell radius scans.
    Spacing = 2 cells (slight overlap for reliability).
    """
    sector = SECTORS.get(sector_label)
    if not sector:
        return []

    x0, x1 = sector["x_range"]
    y0, y1 = sector["y_range"]
    points  = []
    y       = y0
    while y <= y1:
        x = x0
        while x <= x1:
            points.append({"x": x, "y": y})
            x += 2
        y += 2
    return points