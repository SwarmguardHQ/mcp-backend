"""
Scan tools — thermal_scan, acoustic_scan, rgb_scan.

thermal_scan : Infrared + blob detection.  Returns rich payload including
               thermal blobs and per-survivor heat signatures.
acoustic_scan: Vibration sensor through rubble (narrow radius).
rgb_scan     : Optical colour camera — detects survivors by spectral signature
               (clothing colour, skin tone).  Wider FOV than IR but blocked by
               smoke / rubble.  Returns RGB channel values and condition hint.
"""
from __future__ import annotations

import math
import random

from utils.config import (
    ACOUSTIC_BATTERY_COST,
    ACOUSTIC_SCAN_RADIUS,
    BLOB_HEAT_THRESHOLD,
    PRIORITY_SCORES,
    RGB_BASE_DETECTION_PROB,
    RGB_BATTERY_COST,
    RGB_NOISE_SIGMA,
    RGB_SCAN_RADIUS,
    SCAN_BATTERY_COST,
    THERMAL_SCAN_RADIUS,
)
from mcp_server.world_state import world
from mcp_server.drone_simulator import DroneStatus
from mcp_server.mesh_radio import broadcast_mesh_message

# ── Spectral signatures per condition (normalised RGB channels 0-1) ───────────
_CONDITION_SPECTRUM: dict[str, tuple[float, float, float]] = {
    "critical": (0.82, 0.18, 0.14),
    "moderate": (0.74, 0.55, 0.32),
    "stable":   (0.52, 0.68, 0.78),
}
_DEFAULT_SPECTRUM: tuple[float, float, float] = (0.60, 0.55, 0.45)


def _classify_condition_from_rgb(r: float, g: float, b: float) -> str:
    if r > 0.70 and b < 0.30:
        return "critical"
    if r > 0.60 and g > 0.45:
        return "moderate"
    return "stable"


# ── Blob detection (pure Python — no CV deps) ─────────────────────────────────

def _detect_thermal_blobs(
    drone_x: int,
    drone_y: int,
    radius: int = 7,
) -> list[dict]:
    """
    Identify contiguous clusters of survivors above BLOB_HEAT_THRESHOLD
    using a simple BFS on the exploration grid.

    In the MCP backend the "heatmap" is approximated from survivor positions
    (each survivor produces a Gaussian heat footprint on the grid).
    """
    grid_size = 20
    # Build a heat grid from world survivor positions
    heat: dict[tuple[int, int], float] = {}
    for s in world.survivors.values():
        sx, sy = int(s.x), int(s.y)
        # Gaussian spread over ±3 cells
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                cx, cy = sx + dx, sy + dy
                if not (0 <= cx < grid_size and 0 <= cy < grid_size):
                    continue
                dist = math.sqrt(dx * dx + dy * dy)
                h = 80.0 * math.exp(-(dist ** 2) / (2 * 2.0 ** 2))
                heat[(cx, cy)] = heat.get((cx, cy), 18.0) + h

    # BFS blob find
    visited: set[tuple[int, int]] = set()
    blobs: list[dict] = []

    x0, x1 = max(0, drone_x - radius), min(grid_size - 1, drone_x + radius)
    y0, y1 = max(0, drone_y - radius), min(grid_size - 1, drone_y + radius)

    for gy in range(y0, y1 + 1):
        for gx in range(x0, x1 + 1):
            if (gx, gy) in visited:
                continue
            cell_heat = heat.get((gx, gy), 18.0)
            if cell_heat < BLOB_HEAT_THRESHOLD:
                visited.add((gx, gy))
                continue

            blob_cells: list[tuple[int, int]] = []
            queue = [(gx, gy)]
            while queue:
                cx, cy = queue.pop(0)
                if (cx, cy) in visited:
                    continue
                if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                    continue
                ch = heat.get((cx, cy), 18.0)
                if ch < BLOB_HEAT_THRESHOLD:
                    visited.add((cx, cy))
                    continue
                visited.add((cx, cy))
                blob_cells.append((cx, cy))
                for nx, ny in [(cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)]:
                    if (nx, ny) not in visited:
                        queue.append((nx, ny))

            if blob_cells:
                heats = [heat.get(c, 18.0) for c in blob_cells]
                cx_f = sum(c[0] for c in blob_cells) / len(blob_cells)
                cy_f = sum(c[1] for c in blob_cells) / len(blob_cells)
                blobs.append({
                    "centroid": [round(cx_f, 1), round(cy_f, 1)],
                    "area": len(blob_cells),
                    "mean_heat": round(sum(heats) / len(heats), 1),
                    "peak_heat": round(max(heats), 1),
                })

    blobs.sort(key=lambda b: b["peak_heat"], reverse=True)
    return blobs


# ── thermal_scan ───────────────────────────────────────────────────────────────

def thermal_scan(drone_id: str) -> dict:
    """
    Infrared scan at drone's current position.
    Detects survivors within THERMAL_SCAN_RADIUS cells and returns:
      - survivors_detected : list with heat reading + priority
      - thermal_blobs      : connected hot-cell clusters (blob detection)
      - scan_position / radius / battery
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status == DroneStatus.OFFLINE:
        return {"error": "Drone is offline"}

    drone.start_scan(SCAN_BATTERY_COST)
    world.mark_exploration_disc(drone.x, drone.y, THERMAL_SCAN_RADIUS)

    found = []
    for s in world.survivors.values():
        dist = drone.distance_to(s.x, s.y)
        if dist <= THERMAL_SCAN_RADIUS and not s.rescued:
            s.detected = True
            # Simulate a heat reading: closer → hotter + small random noise
            base_heat = max(0.0, 80.0 - dist * 12.0) + random.gauss(0, 3.0)
            found.append({
                "survivor_id":  s.survivor_id,
                "position":     {"x": s.x, "y": s.y},
                "condition":    s.condition,
                "priority":     PRIORITY_SCORES.get(s.condition, 0),
                "heat_reading": round(float(max(0.0, min(100.0, base_heat))), 1),
                "distance":     round(dist, 2),
                "detection_method": "thermal_ir",
            })

    # Blob analysis
    blobs = _detect_thermal_blobs(drone.x, drone.y)

    if found:
        coords = ", ".join(
            f"{f['survivor_id']}@({f['position']['x']},{f['position']['y']})" for f in found
        )
        broadcast_mesh_message(drone_id, f"THERMAL DETECT: {coords}")

    from mcp_server.mesa_bridge import notify_drone_changed
    notify_drone_changed(drone_id)

    return {
        "drone_id":           drone_id,
        "scan_position":      {"x": drone.x, "y": drone.y},
        "scan_radius":        THERMAL_SCAN_RADIUS,
        "survivors_detected": found,
        "thermal_blobs":      blobs,
        "battery_remaining":  drone.battery,
    }


# ── acoustic_scan ──────────────────────────────────────────────────────────────

def acoustic_scan(drone_id: str) -> dict:
    """
    Vibration/acoustic scan — detects survivors through rubble.
    Narrower radius than thermal; useful as follow-up for critical cases.
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status == DroneStatus.OFFLINE:
        return {"error": "Drone is offline"}

    drone.start_scan(ACOUSTIC_BATTERY_COST)
    world.mark_exploration_disc(drone.x, drone.y, ACOUSTIC_SCAN_RADIUS)
    found = []

    for s in world.survivors.values():
        if drone.distance_to(s.x, s.y) <= ACOUSTIC_SCAN_RADIUS and not s.rescued:
            s.detected = True
            found.append({
                "survivor_id":      s.survivor_id,
                "position":         {"x": s.x, "y": s.y},
                "condition":        s.condition,
                "detection_method": "acoustic",
            })

    if found:
        coords = ", ".join(
            f"{f['survivor_id']}@({f['position']['x']},{f['position']['y']})" for f in found
        )
        broadcast_mesh_message(drone_id, f"ACOUSTIC DETECT: {coords}")

    from mcp_server.mesa_bridge import notify_drone_changed
    notify_drone_changed(drone_id)

    return {
        "drone_id":           drone_id,
        "scan_position":      {"x": drone.x, "y": drone.y},
        "scan_radius":        ACOUSTIC_SCAN_RADIUS,
        "survivors_detected": found,
        "battery_remaining":  drone.battery,
    }


# ── rgb_scan ───────────────────────────────────────────────────────────────────

def rgb_scan(drone_id: str) -> dict:
    """
    Optical colour-camera scan.

    Wider FOV than thermal (RGB_SCAN_RADIUS = 2.5 cells) but probability of
    detection falls off with distance² and degrades in low-light / smoke.

    Each detection includes:
      r / g / b       — normalised spectral channel readings (0-1)
      condition_hint  — estimated survivor condition from colour signature
      rgb_confidence  — 0-1 fused colour confidence
    """
    drone = world.get_drone(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status == DroneStatus.OFFLINE:
        return {"error": "Drone is offline"}

    drone.start_scan(RGB_BATTERY_COST)
    world.mark_exploration_disc(drone.x, drone.y, RGB_SCAN_RADIUS)

    hits = []
    for s in world.survivors.values():
        dist = drone.distance_to(s.x, s.y)
        if dist > RGB_SCAN_RADIUS or s.rescued:
            continue

        # Detection probability: inverse-square with distance
        inv_sq = 1.0 / (1.0 + (dist / RGB_SCAN_RADIUS) ** 2)
        if random.random() > RGB_BASE_DETECTION_PROB * inv_sq:
            continue  # occluded / missed

        condition = s.condition
        r_base, g_base, b_base = _CONDITION_SPECTRUM.get(condition, _DEFAULT_SPECTRUM)
        noise = lambda: random.gauss(0, RGB_NOISE_SIGMA)  # noqa: E731
        r = float(max(0.0, min(1.0, r_base + noise())))
        g = float(max(0.0, min(1.0, g_base + noise())))
        b = float(max(0.0, min(1.0, b_base + noise())))

        # Colour-channel confidence
        rgb_conf = float(max(0.0, min(1.0,
            0.5 + 0.35 * (r - 0.5) - 0.15 * b + random.gauss(0, 0.06)
        )))

        condition_hint = _classify_condition_from_rgb(r, g, b)

        # Mark detected if confidence is high enough
        if rgb_conf >= 0.55:
            s.detected = True

        hits.append({
            "survivor_id":    s.survivor_id,
            "position":       {"x": s.x, "y": s.y},
            "condition":      s.condition,
            "condition_hint": condition_hint,
            "r":              round(r, 3),
            "g":              round(g, 3),
            "b":              round(b, 3),
            "rgb_confidence": round(rgb_conf, 3),
            "distance":       round(dist, 2),
            "detection_method": "rgb_camera",
        })

    if hits:
        detected_ids = [h["survivor_id"] for h in hits if h["rgb_confidence"] >= 0.55]
        if detected_ids:
            broadcast_mesh_message(
                drone_id,
                f"RGB DETECT: {', '.join(detected_ids)}"
            )

    from mcp_server.mesa_bridge import notify_drone_changed
    notify_drone_changed(drone_id)

    return {
        "drone_id":        drone_id,
        "scan_position":   {"x": drone.x, "y": drone.y},
        "scan_radius":     RGB_SCAN_RADIUS,
        "rgb_hits":        hits,
        "battery_remaining": drone.battery,
    }
