"""
Unit tests for MCP drone tools.
Run with: pytest tests/test_drone_tools.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from mcp_server.world_state import WorldState


# ── Fixture: fresh world per test ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_world(monkeypatch):
    """Reset world state before every test."""
    import mcp_server.world_state as ws_mod
    new_world = WorldState()
    monkeypatch.setattr(ws_mod, "world", new_world)

    # Patch all tool modules to use the new world
    import mcp_server.tools.movement_tools as mt
    import mcp_server.tools.battery_tools  as bt
    import mcp_server.tools.scan_tools     as st
    import mcp_server.tools.supply_tools   as spt
    import mcp_server.tools.rescue_tools   as rt
    import mcp_server.tools.status_tools   as stat
    import mcp_server.mesh_radio           as mr
    import mcp_server.drone_registry       as dr

    for mod in (mt, bt, st, spt, rt, stat, mr, dr):
        if hasattr(mod, "world"):
            monkeypatch.setattr(mod, "world", new_world)

    return new_world


# ── Discovery ─────────────────────────────────────────────────────────────────

def test_discover_drones_returns_active_and_offline(fresh_world):
    from mcp_server.drone_registry import discover_drones
    result = discover_drones()
    assert "active_drones" in result
    assert "offline_drones" in result
    assert result["total_active"] == 4          # ECHO starts offline
    assert len(result["offline_drones"]) == 1


def test_discover_drones_no_hardcoded_ids(fresh_world):
    from mcp_server.drone_registry import discover_drones
    result = discover_drones()
    ids = [d["drone_id"] for d in result["active_drones"]]
    assert "DRONE_ALPHA" in ids
    assert len(ids) == result["total_active"]


# ── Movement ──────────────────────────────────────────────────────────────────

def test_move_to_success(fresh_world):
    from mcp_server.tools.movement_tools import move_to
    result = move_to("DRONE_ALPHA", 3, 3)
    assert "error" not in result
    assert result["new_position"] == {"x": 3, "y": 3}
    assert result["battery"] < 95


def test_move_to_outside_grid(fresh_world):
    from mcp_server.tools.movement_tools import move_to
    result = move_to("DRONE_ALPHA", 15, 0)
    assert "error" in result


def test_move_to_offline_drone(fresh_world):
    from mcp_server.tools.movement_tools import move_to
    result = move_to("DRONE_ECHO", 1, 1)
    assert "error" in result
    assert "offline" in result["error"].lower()


def test_move_to_insufficient_battery(fresh_world):
    from mcp_server.tools.movement_tools import move_to
    drone = fresh_world.get_drone("DRONE_ALPHA")
    drone.battery = 5
    result = move_to("DRONE_ALPHA", 9, 9)
    assert "error" in result
    assert "battery" in result["error"].lower()


# ── Battery ───────────────────────────────────────────────────────────────────

def test_get_battery_status_ok(fresh_world):
    from mcp_server.tools.battery_tools import get_battery_status
    result = get_battery_status("DRONE_ALPHA")
    assert result["battery"] == 95
    assert result["recommendation"] == "OK"


def test_get_battery_status_low(fresh_world):
    from mcp_server.tools.battery_tools import get_battery_status
    fresh_world.get_drone("DRONE_ALPHA").battery = 20
    result = get_battery_status("DRONE_ALPHA")
    assert "LOW" in result["recommendation"] or "CRITICAL" in result["recommendation"]


def test_return_to_charging_station(fresh_world):
    from mcp_server.tools.battery_tools import return_to_charging_station
    result = return_to_charging_station("DRONE_BRAVO")
    assert "error" not in result
    assert result["status"] == "charging"


def test_charge_drone_restores_battery(fresh_world):
    from mcp_server.tools.battery_tools import return_to_charging_station, charge_drone
    return_to_charging_station("DRONE_BRAVO")
    result = charge_drone("DRONE_BRAVO", 100)
    assert result["battery"] == 100
    assert result["status"] == "idle"


def test_charge_drone_not_at_station(fresh_world):
    from mcp_server.tools.movement_tools import move_to
    from mcp_server.tools.battery_tools import charge_drone
    move_to("DRONE_ALPHA", 5, 5)
    result = charge_drone("DRONE_ALPHA")
    assert "error" in result


# ── Scanning ──────────────────────────────────────────────────────────────────

def test_thermal_scan_detects_nearby_survivor(fresh_world):
    from mcp_server.tools.movement_tools import move_to
    from mcp_server.tools.scan_tools import thermal_scan
    # S1 is at (3,7) — move ALPHA to (3,7) exactly
    move_to("DRONE_ALPHA", 3, 7)
    result = thermal_scan("DRONE_ALPHA")
    ids = [s["survivor_id"] for s in result["survivors_detected"]]
    assert "S1" in ids


def test_thermal_scan_detects_nothing_far_away(fresh_world):
    from mcp_server.tools.scan_tools import thermal_scan
    # ALPHA starts at (0,0) — no survivor within 1.5 cells
    result = thermal_scan("DRONE_ALPHA")
    assert result["survivors_detected"] == []


def test_acoustic_scan_narrower_radius(fresh_world):
    from mcp_server.tools.movement_tools import move_to
    from mcp_server.tools.scan_tools import acoustic_scan
    # S1 at (3,7) — place drone at (3,8) — distance = 1.0 → within acoustic radius
    move_to("DRONE_ALPHA", 3, 8)
    result = acoustic_scan("DRONE_ALPHA")
    ids = [s["survivor_id"] for s in result["survivors_detected"]]
    assert "S1" in ids


# ── Supplies ──────────────────────────────────────────────────────────────────

def test_collect_supplies_at_depot(fresh_world):
    from mcp_server.tools.supply_tools import collect_supplies
    # ALPHA starts at (0,0) which is D1
    result = collect_supplies("DRONE_ALPHA", "medical_kit")
    assert "error" not in result
    assert result["collected"] == "medical_kit"


def test_collect_supplies_wrong_depot(fresh_world):
    from mcp_server.tools.movement_tools import move_to
    from mcp_server.tools.supply_tools import collect_supplies
    move_to("DRONE_ALPHA", 5, 5)
    result = collect_supplies("DRONE_ALPHA", "medical_kit")
    assert "error" in result


def test_collect_duplicate_payload(fresh_world):
    from mcp_server.tools.supply_tools import collect_supplies
    collect_supplies("DRONE_ALPHA", "medical_kit")
    result = collect_supplies("DRONE_ALPHA", "water")
    assert "error" in result
    assert "carrying" in result["error"]


def test_deliver_supplies_success(fresh_world):
    from mcp_server.tools.supply_tools import collect_supplies, deliver_supplies
    from mcp_server.tools.movement_tools import move_to
    from mcp_server.tools.scan_tools import thermal_scan

    # Detect survivor first
    move_to("DRONE_ALPHA", 3, 7)
    thermal_scan("DRONE_ALPHA")

    # Return to depot, collect, deliver
    move_to("DRONE_ALPHA", 0, 0)
    collect_supplies("DRONE_ALPHA", "medical_kit")
    move_to("DRONE_ALPHA", 3, 7)
    result = deliver_supplies("DRONE_ALPHA", "S1")
    assert "error" not in result
    assert result["delivered"] == "medical_kit"


def test_deliver_too_far(fresh_world):
    from mcp_server.tools.supply_tools import collect_supplies, deliver_supplies
    from mcp_server.tools.scan_tools import thermal_scan
    from mcp_server.tools.movement_tools import move_to

    move_to("DRONE_ALPHA", 3, 7)
    thermal_scan("DRONE_ALPHA")
    move_to("DRONE_ALPHA", 0, 0)
    collect_supplies("DRONE_ALPHA", "medical_kit")
    # Drone is at (0,0), S1 is at (3,7) — too far
    result = deliver_supplies("DRONE_ALPHA", "S1")
    assert "error" in result
    assert "far" in result["error"].lower()


# ── Rescue ────────────────────────────────────────────────────────────────────

def test_rescue_priority_list_ordering(fresh_world):
    from mcp_server.tools.scan_tools import thermal_scan
    from mcp_server.tools.movement_tools import move_to
    from mcp_server.tools.rescue_tools import get_rescue_priority_list

    # Detect S1 (critical) and S2 (stable)
    move_to("DRONE_ALPHA", 3, 7)
    thermal_scan("DRONE_ALPHA")
    move_to("DRONE_BRAVO", 8, 2)
    thermal_scan("DRONE_BRAVO")

    result = get_rescue_priority_list()
    ids = [s["survivor_id"] for s in result["priority_list"]]
    # S1 (critical) must come before S2 (stable)
    assert ids.index("S1") < ids.index("S2")


def test_mark_survivor_rescued(fresh_world):
    from mcp_server.tools.scan_tools import thermal_scan
    from mcp_server.tools.movement_tools import move_to
    from mcp_server.tools.rescue_tools import mark_survivor_rescued

    move_to("DRONE_ALPHA", 3, 7)
    thermal_scan("DRONE_ALPHA")
    result = mark_survivor_rescued("S1", "DRONE_ALPHA")
    assert result["status"] == "rescued"
    assert result["remaining_survivors"] == 4


# ── Mesh / offline ────────────────────────────────────────────────────────────

def test_broadcast_mesh_message(fresh_world):
    from mcp_server.mesh_radio import broadcast_mesh_message
    result = broadcast_mesh_message("DRONE_ALPHA", "Test message")
    assert "DRONE_ALPHA" in result["broadcast"]
    assert "DRONE_ALPHA" in result["recipients"]
    assert "DRONE_ECHO" not in result["recipients"]  # ECHO is offline


def test_attempt_drone_recovery(fresh_world, monkeypatch):
    from mcp_server.mesh_radio import attempt_drone_recovery
    # Force success
    monkeypatch.setattr("mcp_server.mesh_radio.random.random", lambda: 0.1)
    result = attempt_drone_recovery("DRONE_ECHO")
    assert result["recovered"] is True
    assert result["battery"] > 0


def test_attempt_drone_recovery_failure(fresh_world, monkeypatch):
    from mcp_server.mesh_radio import attempt_drone_recovery
    # Force failure
    monkeypatch.setattr("mcp_server.mesh_radio.random.random", lambda: 0.9)
    result = attempt_drone_recovery("DRONE_ECHO")
    assert result["recovered"] is False


# ── Status ────────────────────────────────────────────────────────────────────

def test_get_swarm_summary(fresh_world):
    from mcp_server.tools.status_tools import get_swarm_summary
    result = get_swarm_summary()
    assert result["drones"]["total"] == 5
    assert result["drones"]["active"] == 4
    assert result["drones"]["offline"] == 1
    assert result["survivors"]["total"] == 5
    assert result["mission_complete"] is False


def test_get_grid_map_renders(fresh_world):
    from mcp_server.tools.movement_tools import get_grid_map
    result = get_grid_map()
    assert "map" in result
    assert "A" in result["map"]   # DRONE_ALPHA on grid