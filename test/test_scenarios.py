"""
Integration tests — verify each scenarios prompt string exists and
the tool chains it relies on work end-to-end without the LLM.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from mcp_server.world_state import WorldState


@pytest.fixture(autouse=True)
def fresh_world(monkeypatch):
    import mcp_server.world_state as ws_mod
    new_world = WorldState()
    monkeypatch.setattr(ws_mod, "world", new_world)
    for mod_path in [
        "mcp_server.tools.movement_tools",
        "mcp_server.tools.battery_tools",
        "mcp_server.tools.scan_tools",
        "mcp_server.tools.supply_tools",
        "mcp_server.tools.rescue_tools",
        "mcp_server.tools.status_tools",
        "mcp_server.mesh_radio",
        "mcp_server.drone_registry",
    ]:
        import importlib
        mod = importlib.import_module(mod_path)
        if hasattr(mod, "world"):
            monkeypatch.setattr(mod, "world", new_world)
    return new_world


def test_supply_run_scenario_prompt_exists():
    from scenarios.supply_run import MISSION_PROMPT
    assert "medical_kit" in MISSION_PROMPT
    assert "CRITICAL" in MISSION_PROMPT


def test_survivor_detect_scenario_prompt_exists():
    from scenarios.survivor_detect import MISSION_PROMPT
    assert "thermal_scan" in MISSION_PROMPT
    assert "acoustic_scan" in MISSION_PROMPT


def test_battery_crisis_scenario_prompt_exists():
    from scenarios.battery_crisis import MISSION_PROMPT
    assert "BATTERY" in MISSION_PROMPT
    assert "return_to_charging_station" in MISSION_PROMPT


def test_offline_recovery_scenario_prompt_exists():
    from scenarios.offline_recovery import MISSION_PROMPT
    assert "offline" in MISSION_PROMPT.lower()
    assert "attempt_drone_recovery" in MISSION_PROMPT


def test_rescue_priority_scenario_prompt_exists():
    from scenarios.rescue_priority import MISSION_PROMPT
    assert "CRITICAL" in MISSION_PROMPT
    assert "get_rescue_priority_list" in MISSION_PROMPT


def test_swarm_status_scenario_prompt_exists():
    from scenarios.swarm_status import MISSION_PROMPT
    assert "get_swarm_summary" in MISSION_PROMPT
    assert "SWARM STATUS REPORT" in MISSION_PROMPT


def test_full_supply_chain(fresh_world):
    """End-to-end: detect survivor → collect supply → deliver → rescue."""
    from mcp_server.tools.movement_tools import move_to
    from mcp_server.tools.scan_tools     import thermal_scan
    from mcp_server.tools.supply_tools   import collect_supplies, deliver_supplies
    from mcp_server.tools.rescue_tools   import mark_survivor_rescued, get_rescue_priority_list

    # Move to S1 position and detect
    assert "error" not in move_to("DRONE_ALPHA", 3, 7)
    scan = thermal_scan("DRONE_ALPHA")
    assert any(s["survivor_id"] == "S1" for s in scan["survivors_detected"])

    # Confirm in priority list
    plist = get_rescue_priority_list()
    assert plist["priority_list"][0]["survivor_id"] == "S1"  # critical = rank 1

    # Collect from D1 at (0,0)
    assert "error" not in move_to("DRONE_ALPHA", 0, 0)
    assert "error" not in collect_supplies("DRONE_ALPHA", "medical_kit")

    # Deliver to S1
    assert "error" not in move_to("DRONE_ALPHA", 3, 7)
    result = deliver_supplies("DRONE_ALPHA", "S1")
    assert "error" not in result
    assert result["delivered"] == "medical_kit"

    # Mark rescued
    rescue = mark_survivor_rescued("S1", "DRONE_ALPHA")
    assert rescue["status"] == "rescued"
    assert rescue["remaining_survivors"] == 4


def test_battery_management_chain(fresh_world):
    """Drone runs low, returns to charger, recharges, resumes."""
    from mcp_server.tools.battery_tools import (
        get_battery_status, return_to_charging_station, charge_drone
    )

    drone = fresh_world.get_drone("DRONE_DELTA")
    drone.battery = 20

    status = get_battery_status("DRONE_DELTA")
    assert status["battery"] == 20
    assert "LOW" in status["recommendation"] or "CRITICAL" in status["recommendation"]

    result = return_to_charging_station("DRONE_DELTA")
    assert result["status"] == "charging"

    charged = charge_drone("DRONE_DELTA", 100)
    assert charged["battery"] == 100


def test_mission_planner_sector_assignment():
    from agent.mission_planner import plan_sectors, scan_waypoints

    drones = [
        {"drone_id": "DRONE_ALPHA",   "battery": 95, "position": {"x": 0, "y": 0}},
        {"drone_id": "DRONE_BRAVO",   "battery": 80, "position": {"x": 9, "y": 0}},
        {"drone_id": "DRONE_CHARLIE", "battery": 70, "position": {"x": 0, "y": 9}},
        {"drone_id": "DRONE_DELTA",   "battery": 60, "position": {"x": 9, "y": 9}},
    ]

    assignments = plan_sectors(drones)
    sectors_used = [a["sector"] for a in assignments]

    # No duplicate sectors
    assert len(sectors_used) == len(set(sectors_used))
    # All have reasoning
    assert all("reasoning" in a for a in assignments)

    # Scan waypoints for NW sector
    waypoints = scan_waypoints("NW")
    assert len(waypoints) > 0
    assert all(0 <= p["x"] <= 4 and 5 <= p["y"] <= 9 for p in waypoints)