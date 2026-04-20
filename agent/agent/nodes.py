"""
SwarmGuard Agent Nodes — True Swarm Intelligence Architecture
=============================================================

Graph topology:
  START
    └── safety_governor_node          # Hard rules: battery, phase transitions
          ├── [route_after_governor → END]  (mission complete)
          └── strategist_node              # LLM: pheromone updates (search) or rescue directive
                ├── [dispatch_drones → Send × N]  drone_agent_node   (search phase)
                │         └── join_node            # reconverge + MCP ground-truth sync
                │               └── safety_governor_node  (loop)
                └── rescue_execution_node         (rescue phase)
                      └── safety_governor_node  (loop)

Key design decisions:
  - strategist_node:     LLM, writes to search_grid priorities OR issues RescueDirective.
                         NEVER mentions a drone name in search phase.
  - drone_agent_node:    Pure Python. No LLM. Reads grid, claims sector, moves, scans.
                         Relay logic lives HERE (not in the Commander prompt).
  - join_node:           Async reconverge. Does relay auto-release + MCP ground-truth sync.
  - rescue_execution_node: Executes full supply chain from a RescueDirective.
  - safety_governor_node:  Pre-flight battery check + phase state machine.
"""

import asyncio
import json
from typing import Union
from langgraph.graph import END
from langgraph.types import Send
from langchain_google_genai import ChatGoogleGenerativeAI

from .state import SwarmState, StrategyOutput
from .mcp.client import mcp_client
from .utils import (
    get_distance,
    PRIORITY_MAP,
    assign_sectors_to_drones,
    estimate_signal,
    build_strategist_context,
)
from utils.config import (
    BATTERY_COST_PER_CELL,
    BATTERY_RESERVE_MIN,
    BATTERY_LOW_THRESHOLD,
)

# Thread-safe in-memory pool to prevent parallel nodes from claiming the same relay
_TEMP_LOCKED_RELAYS = set()


# ── 1. Safety Governor ────────────────────────────────────────────────────────

async def safety_governor_node(state: SwarmState) -> dict:
    """
    Runs EVERY cycle before the Strategist. Enforces hard rules:
      1. Sync all drone telemetry from MCP ground truth.
      2. Offline drone alerts — logs anomalies (recovery delegated to recovery_node).
      3. Battery emergencies  — any drone below threshold → immediate return.
      4. Phase transitions    — search → rescue → complete.

    Returns only state diffs; does not touch the LLM.
    """
    updates: dict = {"mission_log": []}

    # ── 1. Sync all drone telemetry from MCP ground truth ─────────────────────
    try:
        fleet_res = await mcp_client.session.call_tool("get_all_drone_statuses", {})
        fleet_data = json.loads(fleet_res.content[0].text)
        drone_list = fleet_data.get("drones", [])
        updated_drones = []
        current_map = {d["id"]: d for d in state["drones"]}
        for d_data in drone_list:
            did = d_data.get("drone_id")
            existing = current_map.get(did, {})
            pos = d_data.get("position", {})
            updated_drones.append({
                "id":      did,
                "battery": d_data.get("battery", existing.get("battery", 100)),
                "x":       pos.get("x", existing.get("x", 0)),
                "y":       pos.get("y", existing.get("y", 0)),
                "status":  d_data.get("status", existing.get("status", "idle")),
                "locked":  d_data.get("locked", existing.get("locked", False)),
                "payload": d_data.get("payload", existing.get("payload")),
            })
        if updated_drones:
            updates["drones"] = updated_drones
    except Exception:
        pass  # Non-fatal — relay logic & drone_agent_node use cached state

    # Use the freshly synced list if available, otherwise existing
    live_drones = updates.get("drones", state["drones"])

    # ── 2. Anomaly Detection — detect drones that have gone offline ────────
    for drone in live_drones:
        if drone.get("status") == "offline":
            updates["mission_log"].append(
                f"[GOVERNOR] ⚠️  ANOMALY DETECTED: {drone['id']} is OFFLINE! triggering recovery."
            )

    # ── 3. Battery emergency — command return before LLM even sees the drone ──
    for drone in live_drones:
        if (
            drone["battery"] < BATTERY_LOW_THRESHOLD
            and drone.get("status") not in ("charging", "returning", "offline")
            and not drone.get("locked")
        ):
            try:
                await mcp_client.session.call_tool(
                    "return_to_charging_station", {"drone_id": drone["id"]}
                )
                updates["mission_log"].append(
                    f"[GOVERNOR] ⚡ BATTERY EMERGENCY: {drone['id']} at {drone['battery']}% — "
                    f"commanded to return to base."
                )
            except Exception as e:
                updates["mission_log"].append(f"[GOVERNOR] Return command failed for {drone['id']}: {e}")

    # ── 4. Phase state machine ─────────────────────────────────────────────────
    search_grid  = state["search_grid"]
    all_scanned  = all(cell.get("scanned") for cell in search_grid.values())
    detected     = state.get("detected_survivors", [])
    rescued_set  = set(state.get("rescued_survivors", []))
    pending      = [s for s in detected if s.get("id") not in rescued_set]

    current_phase = state.get("phase", "search")

    if current_phase == "search" and all_scanned:
        if pending:
            updates["phase"] = "rescue"
            updates["mission_log"].append(
                f"[GOVERNOR] 🔄 All {len(search_grid)} sectors scanned. "
                f"Transitioning → RESCUE phase. {len(pending)} survivor(s) pending."
            )
        else:
            updates["phase"] = "complete"
            updates["mission_log"].append(
                "[GOVERNOR] ✅ All sectors scanned. No survivors detected. MISSION COMPLETE."
            )
    elif current_phase == "rescue" and not pending:
        updates["phase"] = "complete"
        updates["mission_log"].append(
            f"[GOVERNOR] ✅ All {len(rescued_set)} survivor(s) rescued. MISSION COMPLETE."
        )
        updates["mission_log"].append("[SYSTEM] 🏁 MISSION SUCCESS — All objectives achieved.")

    return updates


def route_after_governor(state: SwarmState) -> str:
    """Route to recovery if anomalies detected, else Strategist or END."""
    # ── 1. Anomaly Check (Self-Healing Trigger) ────────────────────────────────
    if any(d.get("status") == "offline" for d in state["drones"]):
        return "recovery_node"

    # ── 2. Mission Completion ──────────────────────────────────────────────────
    if state.get("phase") == "complete":
        return END

    # ── 3. Normal Flow ─────────────────────────────────────────────────────────
    return "strategist_node"


# ── 2. Strategist Node (LLM — Environment Intelligence) ───────────────────────

async def strategist_node(state: SwarmState) -> dict:
    """
    LLM Strategist — shapes the pheromone environment.

    SEARCH PHASE:
      Reads scan results → outputs priority_updates to search_grid.
      Does NOT name drones. Does NOT issue move commands.

    RESCUE PHASE:
      Matches closest drone to most critical survivor → outputs rescue_directive.
      The rescue_execution_node handles all MCP calls.
    """
    updates: dict = {"mission_log": []}

    context = build_strategist_context(state)

    # Rate-limit guard
    await asyncio.sleep(4.0)

    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0)
    # llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    structured_llm = llm.with_structured_output(StrategyOutput)

    try:
        response: StrategyOutput = await structured_llm.ainvoke(context)
    except Exception as e:
        updates["mission_log"].append(f"[STRATEGIST] ❌ LLM error: {e}")
        return updates

    updates["mission_log"].append(f"[STRATEGIST] 💭 {response.thought}")

    phase = state.get("phase", "search")

    if phase == "search":
        # Apply pheromone updates — locked to un-scanned sectors only
        grid_updates: dict = {}
        for sector_id, new_priority in response.priority_updates.items():
            existing = state["search_grid"].get(sector_id)
            if existing and not existing.get("scanned"):
                grid_updates[sector_id] = {
                    **existing,
                    "priority": max(0.0, float(new_priority)),
                }
        if grid_updates:
            updates["search_grid"] = grid_updates
            summary = {s: f"{c['priority']:.1f}" for s, c in grid_updates.items()}
            updates["mission_log"].append(f"[STRATEGIST] 📡 Pheromone update: {summary}")

    elif phase == "rescue":
        if response.rescue_directive:
            directive = response.rescue_directive
            updates["rescue_directive"] = directive.model_dump()
            updates["mission_log"].append(
                f"[STRATEGIST] 🆘 RESCUE DIRECTIVE: {directive.drone_id} → "
                f"{directive.survivor_id} ({directive.supply_type})"
            )
        else:
            updates["mission_log"].append("[STRATEGIST] ⚠️  No rescue directive issued — looping.")

    return updates


# ── 3. Dispatch Router (Swarm Fan-out) ────────────────────────────────────────

def dispatch_drones(state: SwarmState) -> Union[str, list]:
    """
    Routing function called after strategist_node.

    SEARCH phase: Pre-assigns sectors to idle drones using Pythagorean cost,
    then fans out via LangGraph Send for true parallel execution.

    RESCUE phase: Routes to rescue_execution_node.

    If nothing to do, falls through to join_node.
    """
    phase = state.get("phase", "search")

    if phase != "search":
        directive = state.get("rescue_directive")
        if directive:
            return "rescue_execution_node"
        # No directive yet — loop back to governor
        return "join_node"

    # Pure-Python sector → drone assignment (Pythagorean cost)
    assignments, skipped_logs = assign_sectors_to_drones(
        state["drones"], state["search_grid"], PRIORITY_MAP
    )

    # Log relay-shortage bottlenecks so the LLM Strategist knows why missions are skipped
    for log_msg in skipped_logs:
        state["mission_log"].append(f"[DISPATCH] {log_msg}")

    if not assignments:
        # All drones busy or sectors all claimed — check back next cycle
        remaining = [
            sid for sid, cell in state["search_grid"].items()
            if not cell.get("scanned")
        ]
        if remaining:
            state["mission_log"].append(
                f"[DISPATCH] 🔄 No idle drones available. {len(remaining)} sector(s) pending: {remaining}"
            )
        return "join_node"

    # Fan-out: each idle drone gets its own parallel drone_agent_node execution
    sends = []
    assigned_team = list(assignments.keys())
    _TEMP_LOCKED_RELAYS.clear()  # Reset the concurrent lock pool

    for drone_id, target_sector in assignments.items():
        sends.append(
            Send(
                "drone_agent_node",
                {
                    # Inject task-specific keys alongside the full shared state
                    "drone_id":      drone_id,
                    "target_sector": target_sector,
                    "assigned_team": assigned_team,
                    # Spread full SwarmState for read-only access in the drone node
                    **state,
                },
            )
        )
    state["mission_log"].append(
        f"[DISPATCH] 🚁 Dispatching {len(sends)} drone(s): "
        + ", ".join(f"{did}→{sec}" for did, sec in assignments.items())
    )
    return sends


# ── 4. Drone Agent Node (Pure Python — The Local Brain) ───────────────────────

async def drone_agent_node(state: dict) -> dict:
    """
    Autonomous drone execution — NO LLM.

    Receives: full SwarmState dict PLUS injected keys:
      - drone_id:      which physical drone we are
      - target_sector: which sector we have been pre-assigned

    Loop:
      1. CLAIM    — mark sector as claimed_by this drone (prevents racing)
      2. BATTERY  — verify we can afford the trip; abort to base if not
      3. RELAY    — if target > 10 cells away from base (0,0), deploy a relay drone first
      4. MOVE     — call move_to MCP tool (Pythagorean path already computed)
      5. SCAN     — call thermal_scan MCP tool
      6. COMPLETE — set priority=0, claimed_by=None, scanned=True

    Returns state diffs only — LangGraph reducers merge them back.
    """
    drone_id      = state["drone_id"]
    target_sector = state["target_sector"]

    updates: dict = {"mission_log": [], "drones": [], "search_grid": {}}

    # ── Locate our drone in the shared fleet ──────────────────────────────────
    drone = next((d for d in state["drones"] if d["id"] == drone_id), None)
    if not drone:
        updates["mission_log"].append(f"[{drone_id}] ❌ Not found in state — skipping.")
        return updates

    # ── Sector coordinates ────────────────────────────────────────────────────
    sector_data = PRIORITY_MAP.get(target_sector, {})
    tx, ty = sector_data.get("x", 0), sector_data.get("y", 0)
    distance = get_distance(drone["x"], drone["y"], tx, ty)

    # ── STEP 1: CLAIM ──────────────────────────────────────────────────────────
    updates["search_grid"][target_sector] = {
        "priority":   state["search_grid"].get(target_sector, {}).get("priority", 1.0),
        "claimed_by": drone_id,
        "scanned":    False,
    }
    updates["mission_log"].append(
        f"[{drone_id}] ✋ Claiming '{target_sector}' at ({tx},{ty}). "
        f"Distance: {distance:.1f} cells. Battery: {drone['battery']}%."
    )

    # ── STEP 2: BATTERY CHECK ─────────────────────────────────────────────────
    battery_needed = int(distance * BATTERY_COST_PER_CELL) + BATTERY_RESERVE_MIN
    if drone["battery"] < battery_needed:
        updates["mission_log"].append(
            f"[{drone_id}] ⚡ Insufficient battery ({drone['battery']}% < "
            f"{battery_needed}% needed for '{target_sector}'). Releasing claim, returning to base."
        )
        updates["search_grid"][target_sector] = {
            **state["search_grid"].get(target_sector, {}),
            "claimed_by": None,
        }
        try:
            await mcp_client.session.call_tool(
                "return_to_charging_station", {"drone_id": drone_id}
            )
        except Exception:
            pass
        return updates

    # ── STEP 3: RELAY CHECK ───────────────────────────────────────────────────
    active_relays = state.get("active_relays", {})
    dist_to_base = get_distance(tx, ty, mcp_client.base_x, mcp_client.base_y)
    if dist_to_base > 10:
        mid_x = int((mcp_client.base_x + tx) / 2)
        mid_y = int((mcp_client.base_y + ty) / 2)

        if drone_id in active_relays:
            # Dynamically reposition existing relay
            existing_relay = active_relays[drone_id]
            try:
                await mcp_client.session.call_tool(
                    "move_to", {"drone_id": existing_relay, "x": mid_x, "y": mid_y}
                )
                updates["mission_log"].append(
                    f"[{drone_id}] 📡 Relocated existing relay {existing_relay} to optimal midpoint ({mid_x},{mid_y})"
                )
            except Exception as e:
                updates["mission_log"].append(f"[{drone_id}] ⚠️ Relay relocation failed: {e}")
        else:
            # Already-committed relays in this snapshot: exclude them so two
            # parallel drone agents never try to lock the same physical drone.
            already_relaying = set(active_relays.values())

            available_relay_drones = [
                d for d in state["drones"]
                if d["id"] != drone_id
                and d["id"] not in already_relaying          # ← already locked in previous ticks
                and d["id"] not in state.get("assigned_team", []) # ← exact tick parallel search guard
                and d["id"] not in _TEMP_LOCKED_RELAYS       # ← exact tick parallel relay guard
                and not d.get("locked")
                and d.get("status") not in ("offline", "charging")
                and not d.get("payload")
                and d.get("battery", 0) >= (
                    int(get_distance(d["x"], d["y"], mid_x, mid_y) * BATTERY_COST_PER_CELL)
                    + BATTERY_RESERVE_MIN
                )
            ]
    
    
            # Check if a relay is already sitting at the midpoint (shared relay)
            existing_at_mid = next(
                (d for d in state["drones"]
                 if d["id"] != drone_id and d["x"] == mid_x and d["y"] == mid_y
                 and not d.get("payload")),
                None
            )
    
            if existing_at_mid:
                # Reuse it — lock it if not already locked
                updates["active_relays"] = {**active_relays, drone_id: existing_at_mid["id"]}
                updates["mission_log"].append(
                    f"[{drone_id}] 📡 Reusing shared relay {existing_at_mid['id']} at ({mid_x},{mid_y})."
                )
            elif available_relay_drones:
                # Deploy the drone with the lowest battery as relay (conserves high-battery drones)
                relay_drone = min(available_relay_drones, key=lambda d: d.get("battery", 100))
                _TEMP_LOCKED_RELAYS.add(relay_drone["id"])  # Reserves it from other concurrent runs
    
                try:
                    relay_res = await mcp_client.session.call_tool(
                        "move_to", {"drone_id": relay_drone["id"], "x": mid_x, "y": mid_y}
                    )
                    relay_text = relay_res.content[0].text
                    updates["mission_log"].append(
                        f"[{drone_id}] 📡 Relay {relay_drone['id']} deployed to ({mid_x},{mid_y}): {relay_text}"
                    )
    
                    if "error" in relay_text.lower():
                        raise RuntimeError(f"Relay move failed: {relay_text}")
    
                    await mcp_client.session.call_tool("lock_drone", {"drone_id": relay_drone["id"]})
    
                    # Update relay drone telemetry in state
                    updates["drones"].append({
                        **relay_drone,
                        "x": mid_x,
                        "y": mid_y,
                        "locked": True,
                        "status": "relay",
                    })
                    updates["active_relays"] = {**active_relays, drone_id: relay_drone["id"]}
    
                    # Signal map: record signal strength at this relay midpoint.
                    # Emit only the new key — _merge_signal_map reducer merges with old state.
                    updates["signal_map"] = {f"relay_for_{drone_id}": estimate_signal(mid_x, mid_y)}
    
                    # Sync frontend: relay drone just moved
                    await mcp_client.step_sync()
                except Exception as e:
                    updates["mission_log"].append(
                        f"[{drone_id}] ⚠️  Relay deployment failed: {e}. Releasing claim."
                    )
                    updates["search_grid"][target_sector] = {
                        **state["search_grid"].get(target_sector, {}),
                        "claimed_by": None,
                    }
                    return updates
            else:
                updates["mission_log"].append(
                    f"[{drone_id}] ⛔ No relay candidate available for {distance:.1f}-cell trip. "
                    f"Releasing claim on '{target_sector}'."
                )
                updates["search_grid"][target_sector] = {
                    **state["search_grid"].get(target_sector, {}),
                    "claimed_by": None,
                }
                return updates

    # ── STEP 4: MOVE ──────────────────────────────────────────────────────────
    try:
        move_res  = await mcp_client.session.call_tool(
            "move_to", {"drone_id": drone_id, "x": tx, "y": ty}
        )
        move_text = move_res.content[0].text
        updates["mission_log"].append(f"[{drone_id}] ✈  move_to({tx},{ty}): {move_text}")

        # Sync frontend: main drone just moved to target sector
        await mcp_client.step_sync()

        if "error" in move_text.lower():
            updates["search_grid"][target_sector] = {
                **state["search_grid"].get(target_sector, {}),
                "claimed_by": None,
            }
            return updates
    except Exception as e:
        updates["mission_log"].append(f"[{drone_id}] ❌ Move exception: {e}. Releasing claim.")
        updates["search_grid"][target_sector] = {
            **state["search_grid"].get(target_sector, {}),
            "claimed_by": None,
        }
        return updates

    # ── STEP 5: THERMAL SCAN ──────────────────────────────────────────────────
    survivor_detections: list = []
    try:
        scan_res  = await mcp_client.session.call_tool("thermal_scan", {"drone_id": drone_id})
        scan_text = scan_res.content[0].text
        updates["mission_log"].append(f"[{drone_id}] 🔍 thermal_scan: {scan_text[:200]}")

        # Sync frontend: scan completed (may have found survivors)
        await mcp_client.step_sync()

        # Parse survivor detections from scan result
        try:
            scan_data    = json.loads(scan_text)
            newly_found  = scan_data.get("survivors_detected", [])
            for s in newly_found:
                sid = s.get("survivor_id")
                if sid:
                    survivor_detections.append({
                        "id":        sid,
                        "x":         s["position"]["x"],
                        "y":         s["position"]["y"],
                        "condition": s.get("condition", "unknown"),
                    })
                    updates["mission_log"].append(
                        f"[{drone_id}] 🆘 DETECTED {sid} at "
                        f"({s['position']['x']},{s['position']['y']}) "
                        f"[{s.get('condition','?').upper()}]"
                    )
        except Exception:
            pass
    except Exception as e:
        updates["mission_log"].append(f"[{drone_id}] ⚠️  Scan exception: {e}")

    # ── STEP 6: MARK SECTOR COMPLETE ─────────────────────────────────────────
    updates["search_grid"][target_sector] = {
        "priority":   0.0,
        "claimed_by": None,
        "scanned":    True,
    }
    updates["mission_log"].append(
        f"[{drone_id}] ✅ Sector '{target_sector}' COMPLETE "
        f"({sector_data.get('type','?')} at ({tx},{ty}))."
    )

    # ── STEP 7: TELEMETRY SYNC ────────────────────────────────────────────────
    try:
        sync_res  = await mcp_client.session.call_tool("get_drone_status", {"drone_id": drone_id})
        sync_data = json.loads(sync_res.content[0].text)
        if "error" not in sync_data:
            pos = sync_data.get("position", {})
            updated_drone = {
                **drone,
                "battery": sync_data.get("battery", drone["battery"]),
                "x":       pos.get("x", tx),
                "y":       pos.get("y", ty),
                "status":  sync_data.get("status", drone["status"]),
                "payload": sync_data.get("payload", drone.get("payload")),
                "locked":  sync_data.get("locked", drone.get("locked", False)),
            }
            updates["drones"].append(updated_drone)
            updates["mission_log"].append(
                f"[{drone_id}] 📊 Synced — battery={updated_drone['battery']}%, "
                f"pos=({updated_drone['x']},{updated_drone['y']})"
            )
            # Sync frontend: drone telemetry updated after sector complete
            await mcp_client.step_sync()
    except Exception:
        pass

    # ── STEP 8: AUTO-RELEASE RELAY if back in comm range ─────────────────────
    if drone_id in active_relays:
        dist_to_base = get_distance(tx, ty, mcp_client.base_x, mcp_client.base_y)
        if dist_to_base <= 10:
            relay_id = active_relays[drone_id]
            try:
                await mcp_client.session.call_tool("unlock_drone", {"drone_id": relay_id})
                new_relays = {k: v for k, v in active_relays.items() if k != drone_id}
                updates["active_relays"] = new_relays
                updates["mission_log"].append(
                    f"[{drone_id}] 🔓 AUTO-RELEASE: Relay {relay_id} freed "
                    f"(main drone within 10 cells of base)."
                )
            except Exception as e:
                updates["mission_log"].append(f"[{drone_id}] Relay release failed: {e}")

    return updates


# ── 5. Join Node (Reconverge + Ground-Truth Sync) ─────────────────────────────

async def join_node(state: SwarmState) -> dict:
    """
    Reconverge point after all parallel drone_agent_node executions complete.

    Responsibilities:
      1. Relay auto-release: unlock any relay drones whose main drone is ≤ 10 cells from base.
         (Garbage Collector - if drone_agent_node hits an exception (Drone Offline) during its mission,
         it might never reach its final AUTO-RELEASE code)
      2. Ground-truth sync: pull detected_survivors + rescued_survivors from MCP get_swarm_summary.
         This is the SINGLE authoritative write for these two fields — avoids reducer conflicts.
    """
    updates: dict = {"mission_log": []}

    # ── Relay auto-release check ───────────────────────────────────────────────
    active_relays = state.get("active_relays", {})
    released_relays: dict = dict(active_relays)
    for main_id, relay_id in active_relays.items():
        main_drone = next((d for d in state["drones"] if d["id"] == main_id), None)
        if main_drone:
            dist_to_base = get_distance(
                main_drone["x"], main_drone["y"],
                mcp_client.base_x, mcp_client.base_y
            )
            if dist_to_base <= 10:
                try:
                    await mcp_client.session.call_tool("unlock_drone", {"drone_id": relay_id})
                    updates["mission_log"].append(
                        f"[JOIN] 🔓 Relay {relay_id} auto-released — "
                        f"{main_id} is within 10 cells of base."
                    )
                    del released_relays[main_id]
                except Exception as e:
                    updates["mission_log"].append(f"[JOIN] Relay release error: {e}")

    if released_relays != active_relays:
        updates["active_relays"] = released_relays

    # ── Authoritative survivor sync from MCP ──────────────────────────────────
    try:
        summary_res  = await mcp_client.session.call_tool("get_swarm_summary", {})
        summary_data = json.loads(summary_res.content[0].text)
        if "error" not in summary_data:
            survivor_info = summary_data.get("survivors", {})
            updates["detected_survivors"] = survivor_info.get("pending", [])
            updates["rescued_survivors"]  = survivor_info.get("rescued_ids", [])
    except Exception:
        pass

    # ── Deadlock Breaker ──────────────────────────────────────────────────────
    if state.get("phase") == "rescue" and not state.get("rescue_directive"):
        pending_list = updates.get("detected_survivors", state.get("detected_survivors", []))
        rescued_set  = set(updates.get("rescued_survivors", state.get("rescued_survivors", [])))
        if any(s.get("id") not in rescued_set for s in pending_list):
            # The Strategist issued no directive because all feasible drones were rejected
            # due to lacking battery for the full round trip. Call them back to charge.
            for d in state["drones"]:
                # Only force idle, unlocked drones with less than 95% battery to return.
                # To prevent every drone have battery like 60% but not enough for the task,
                # end up infinity loops (no drone achievable for the mission)
                if d.get("status") == "idle" and not d.get("locked") and d.get("battery", 100) < 95:
                    try:
                        await mcp_client.session.call_tool("return_to_charging_station", {"drone_id": d["id"]})
                        updates["mission_log"].append(
                            f"[JOIN] 🔋 Auto-recharge triggered for {d['id']} ({d.get('battery')}%). "
                            "Breaking rescue deadlock."
                        )
                    except Exception:
                        pass

    return updates


# ── 6. Rescue Execution Node (LLM-Directed Supply Chain) ──────────────────────

async def rescue_execution_node(state: SwarmState) -> dict:
    """
    Executes the Strategist's rescue_directive.

    Full supply chain without any LLM involvement:
      1. list_supply_depots → find nearest depot with required supply_type.
      2. move_to depot.
      3. collect_supplies.
      4. move_to survivor.
      5. deliver_supplies  (MCP marks survivor as RESCUED automatically).
      6. Sync survivor state from get_swarm_summary.
      7. Clear rescue_directive so Strategist issues a fresh one next cycle.
    """
    directive = state.get("rescue_directive")
    if not directive:
        return {"mission_log": ["[RESCUE] No directive in state — skipping."]}

    drone_id    = directive["drone_id"]
    survivor_id = directive["survivor_id"]
    supply_type = directive["supply_type"]

    updates: dict = {"mission_log": [], "rescue_directive": None}

    drone    = next((d for d in state["drones"] if d["id"] == drone_id), None)
    survivor = next((s for s in state.get("detected_survivors", []) if s["id"] == survivor_id), None)

    if not drone:
        updates["mission_log"].append(f"[RESCUE] ❌ Drone '{drone_id}' not found.")
        return updates
    if not survivor:
        updates["mission_log"].append(f"[RESCUE] ❌ Survivor '{survivor_id}' not found.")
        return updates

    updates["mission_log"].append(
        f"[RESCUE] 🆘 {drone_id} → rescuing {survivor_id} "
        f"at ({survivor['x']},{survivor['y']}) [{survivor.get('condition','?').upper()}] "
        f"with {supply_type}"
    )

    try:
        # ── 1. Find nearest depot ──────────────────────────────────────────────
        depots_res  = await mcp_client.session.call_tool("list_supply_depots", {})
        depots_data = json.loads(depots_res.content[0].text)
        valid_depots = [
            d for d in depots_data.get("depots", [])
            if supply_type in d.get("supplies", [])
        ]
        if not valid_depots:
            updates["mission_log"].append(f"[RESCUE] ❌ No depot carries '{supply_type}'.")
            return updates

        nearest_depot = min(
            valid_depots,
            key=lambda d: get_distance(drone["x"], drone["y"], d["x"], d["y"])
        )

        # ── Pre-flight battery check ───────────────────────────────────────────
        # Full trip cost = dist(drone→depot) + dist(depot→survivor)
        # This is what the MCP server checks internally — we verify it upfront
        # so a failed directive is dropped cleanly rather than wasting a trip.
        dist_to_depot       = get_distance(drone["x"], drone["y"], nearest_depot["x"], nearest_depot["y"])
        dist_depot_survivor = get_distance(nearest_depot["x"], nearest_depot["y"], survivor["x"], survivor["y"])
        total_dist          = dist_to_depot + dist_depot_survivor
        battery_needed      = int(total_dist * BATTERY_COST_PER_CELL) + BATTERY_RESERVE_MIN

        if drone["battery"] < battery_needed:
            updates["mission_log"].append(
                f"[RESCUE] ⚡ BATTERY PREFLIGHT FAIL: {drone_id} has {drone['battery']}% but needs "
                f"{battery_needed}% for {total_dist:.1f}-cell round trip "
                f"(→depot {dist_to_depot:.1f} + →survivor {dist_depot_survivor:.1f} + {BATTERY_RESERVE_MIN}% reserve). "
                f"Directive dropped — Strategist will reassign."
            )
            return updates

        updates["mission_log"].append(
            f"[RESCUE] 🏭 Nearest depot '{nearest_depot['id']}' at "
            f"({nearest_depot['x']},{nearest_depot['y']}) — "
            f"full trip {total_dist:.1f} cells, needs {battery_needed}%, have {drone['battery']}%"
        )

        # ── 1.5. Deploy Mesh Relay if required ─────────────────────────────────
        active_relays = state.get("active_relays", {})
        # Check if the longest leg of the journey exceeds base signal range
        max_dist_from_base = max(
            get_distance(mcp_client.base_x, mcp_client.base_y, nearest_depot["x"], nearest_depot["y"]),
            get_distance(mcp_client.base_x, mcp_client.base_y, survivor["x"], survivor["y"])
        )

        if max_dist_from_base > 10:
            # Midpoint: halfway between the base station and the furthest destination.
            # This places the relay where it can bridge the signal gap from home.
            far_dest = max(
                [(nearest_depot["x"], nearest_depot["y"]), (survivor["x"], survivor["y"])],
                key=lambda p: get_distance(mcp_client.base_x, mcp_client.base_y, p[0], p[1])
            )
            mid_x = int((mcp_client.base_x + far_dest[0]) / 2)
            mid_y = int((mcp_client.base_y + far_dest[1]) / 2)
            
            if drone_id in active_relays:
                existing_relay = active_relays[drone_id]
                try:
                    await mcp_client.session.call_tool(
                        "move_to", {"drone_id": existing_relay, "x": mid_x, "y": mid_y}
                    )
                    updates["mission_log"].append(
                        f"[RESCUE] 📡 Relocated existing Mesh Relay {existing_relay} to optimal midpoint ({mid_x},{mid_y})"
                    )
                except Exception as e:
                    pass
            else:
                already_relaying = set(active_relays.values())

                available_relay_drones = [
                    d for d in state["drones"]
                    if d["id"] != drone_id
                    and d["id"] not in already_relaying
                    and not d.get("locked")
                    and d.get("status") == "idle"
                    and not d.get("payload")
                    and d.get("battery", 0) >= (
                        int(get_distance(d["x"], d["y"], mid_x, mid_y) * BATTERY_COST_PER_CELL)
                        + BATTERY_RESERVE_MIN
                    )
                ]
                if available_relay_drones:
                    relay_drone = min(available_relay_drones, key=lambda d: d.get("battery", 100))
                    try:
                        relay_res = await mcp_client.session.call_tool(
                            "move_to", {"drone_id": relay_drone["id"], "x": mid_x, "y": mid_y}
                        )
                        await mcp_client.session.call_tool("lock_drone", {"drone_id": relay_drone["id"]})
                        updates["mission_log"].append(
                            f"[RESCUE] 📡 Mesh Relay {relay_drone['id']} deployed to ({mid_x},{mid_y}): {relay_res.content[0].text}"
                        )
                        updates["active_relays"] = {**active_relays, drone_id: relay_drone["id"]}
                        await mcp_client.step_sync()
                    except Exception as e:
                        updates["mission_log"].append(f"[RESCUE] ⚠️ Relay deploy failed: {e}")
                else:
                    updates["mission_log"].append(
                        f"[RESCUE] ⚠️ No relay available for {max_dist_from_base:.1f}-cell trip to "
                        f"{survivor_id}! All idle drones either lack battery or are already deployed. "
                        f"Directive dropped — Strategist should try a different drone or wait for recharge."
                    )
                    return updates

        # ── 2. Move to depot ───────────────────────────────────────────────────

        res = await mcp_client.session.call_tool(
            "move_to", {"drone_id": drone_id, "x": nearest_depot["x"], "y": nearest_depot["y"]}
        )
        updates["mission_log"].append(f"[RESCUE] ✈  To depot: {res.content[0].text[:120]}")
        # Sync frontend: rescue drone arrived at depot
        await mcp_client.step_sync()

        # ── 3. Collect supplies ────────────────────────────────────────────────
        res = await mcp_client.session.call_tool(
            "collect_supplies", {"drone_id": drone_id, "supply_type": supply_type}
        )
        updates["mission_log"].append(f"[RESCUE] 📦 Collect: {res.content[0].text[:120]}")
        # Sync frontend: supplies loaded
        await mcp_client.step_sync()

        # ── 4. Move to survivor ────────────────────────────────────────────────
        res = await mcp_client.session.call_tool(
            "move_to", {"drone_id": drone_id, "x": survivor["x"], "y": survivor["y"]}
        )
        updates["mission_log"].append(f"[RESCUE] ✈  To survivor: {res.content[0].text[:120]}")
        # Sync frontend: rescue drone arrived at survivor location
        await mcp_client.step_sync()

        # ── 5. Deliver ────────────────────────────────────────────────────────
        res = await mcp_client.session.call_tool(
            "deliver_supplies", {"drone_id": drone_id, "survivor_id": survivor_id}
        )
        updates["mission_log"].append(f"[RESCUE] 🏥 Deliver: {res.content[0].text[:120]}")
        # Sync frontend: delivery confirmed — survivor rescued
        await mcp_client.step_sync()

    except Exception as e:
        updates["mission_log"].append(f"[RESCUE] ❌ Supply chain exception: {e}")
        return updates

    # ── 6. Ground-truth survivor sync ─────────────────────────────────────────
    try:
        summary_res  = await mcp_client.session.call_tool("get_swarm_summary", {})
        summary_data = json.loads(summary_res.content[0].text)
        if "error" not in summary_data:
            survivor_info = summary_data.get("survivors", {})
            updates["detected_survivors"] = survivor_info.get("pending", [])
            updates["rescued_survivors"]  = survivor_info.get("rescued_ids", [])
            updates["mission_log"].append(
                f"[RESCUE] 📊 Survivors remaining: {len(updates['detected_survivors'])} | "
                f"Rescued: {updates['rescued_survivors']}"
            )
    except Exception:
        pass

    return updates


# ── 7. Recovery Node (Self-Healing) ───────────────────────────────────────────

async def recovery_node(state: SwarmState) -> dict:
    """
    Lightweight self-healing node for offline drone recovery.
    Triggered by the safety governor route on error conditions.
    """
    updates: dict = {"mission_log": ["[RECOVERY] 🛠  Initiating self-healing protocol."]}

    # Find the first offline drone to attempt recovery on
    offline_drone = next(
        (d for d in state["drones"] if d.get("status") == "offline"), None
    )
    if offline_drone:
        try:
            res = await mcp_client.session.call_tool(
                "attempt_drone_recovery", {"drone_id": offline_drone["id"]}
            )
            updates["mission_log"].append(f"[RECOVERY] {res.content[0].text}")
        except Exception as e:
            updates["mission_log"].append(f"[RECOVERY] ❌ Recovery attempt failed: {e}")
    else:
        updates["mission_log"].append("[RECOVERY] No offline drones found.")

    return updates
