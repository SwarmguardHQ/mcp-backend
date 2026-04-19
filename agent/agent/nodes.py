import ast
import json
from typing import Literal
import asyncio

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END

from .state import SwarmState, AgentOutput
from .mcp.client import mcp_client
from .utils import get_distance, SIREN_COMMANDER_PERSONA, PRIORITY_MAP
from utils.config import (
    BATTERY_COST_PER_CELL,
    BATTERY_RESERVE_MIN,
    BATTERY_LOW_THRESHOLD,
)

async def thinking_node(state: SwarmState) -> SwarmState:
    """Provides intermediate feedback for better streaming."""
    state["mission_log"].append("[LOG] SIREN commander is assessing telemetry...")
    return state

async def commander_node(state: SwarmState) -> SwarmState:    
    # 1. Get Unscanned Sectors with Priority Applied
    unscanned_sectors = [sector for sector, scanned in state.get("search_grid", {}).items() if not scanned]
    # Sort unscanned sectors by their embedded priority (1 is highest)
    unscanned_sectors.sort(key=lambda sector: PRIORITY_MAP.get(sector, {}).get("priority", 99))
    target_sector = unscanned_sectors[0] if unscanned_sectors else None
    
    # 2. Build Prompt Context
    tools_text = await mcp_client.get_available_tools()

    context = f"{SIREN_COMMANDER_PERSONA}\n\n"
    context += f"SCENARIO BRIEFING:\n{state.get('mission_prompt', '')}\n\n"
    context += f"CURRENT STATE:\n"
    context += f"Drones: {state['drones']}\n"
    context += f"Active Relays: {state.get('active_relays', {})}\n"
    
    if state.get("mission_log"):
        context += "\nRECENT ACTION MEMORY (Do not repeat the exact same tool calls if they just succeeded):\n"
        context += "\n".join(state["mission_log"][-12:]) + "\n"
        
    context += f"\nAVAILABLE TOOLS:\n{tools_text}\n"
    context += "You must format your tool_call strictly using the exact 'name' and matching 'parameters' keys specified in the schemas above.\n\n"
    
    # 3. Phase Handling & Target Priority
    if target_sector:
        data = PRIORITY_MAP.get(target_sector, {})
        zone_type = data.get("type", "Generic")
        target_x = data.get("x", 0)
        target_y = data.get("y", 0)
        context += f"PHASE: SEARCH & SCAN\n"
        context += f"TARGET PRIORITY: Sector '{target_sector}' ({zone_type} at X:{target_x}, Y:{target_y}) is the highest priority unscanned sector.\n"
    else:
        # Are there any detected survivors who haven't been rescued yet?
        rescue_pending = state.get("detected_survivors", [])
        
        if rescue_pending:
            context += f"PHASE: RESCUE & SUPPLY\n"
            context += f"STATUS: SEARCH COMPLETE. All sectors cleared, but {len(rescue_pending)} survivor(s) are PENDING assistance:\n"
            for s in rescue_pending:
                context += f"  - {s['id']} at ({s['x']}, {s['y']}) [{s['condition'].upper()}]\n"
            context += f"RESCUED SO FAR: {state.get('rescued_survivors', [])}\n"
            context += (
                f"OBJECTIVE: For each pending survivor — move the drone to their exact coordinates, "
                f"then call deliver_supplies(drone_id, survivor_id). This automatically marks them RESCUED. "
                f"Do NOT call mark_survivor_rescued separately.\n"
            )
        else:
            context += "PHASE: MISSION COMPLETE\n"
            context += "STATUS: All sectors scanned and all detected survivors rescued.\n"

    # 4. URGENT DELIVERY: steer the LLM to deliver immediately
    # If any drone is already carrying supplies AND is within delivery range of a
    # pending survivor, inject an urgent directive so the LLM acts NOW.
    rescue_pending_for_alert = state.get("detected_survivors", [])
    if rescue_pending_for_alert:
        urgent_deliveries = []
        for d in state["drones"]:
            if d.get("payload"):
                for s in rescue_pending_for_alert:
                    dist = get_distance(d["x"], d["y"], s["x"], s["y"])
                    if dist <= 1.5:
                        urgent_deliveries.append(
                            f"{d['id']} is at ({d['x']},{d['y']}) carrying '{d['payload']}' "
                            f"and is within delivery range of {s['id']} at ({s['x']},{s['y']}) [{s['condition'].upper()}]. "
                            f"Call deliver_supplies(drone_id='{d['id']}', survivor_id='{s['id']}') IMMEDIATELY."
                        )
        if urgent_deliveries:
            context += "\nURGENT — IMMEDIATE DELIVERY REQUIRED (do NOT plan, execute NOW):\n"
            context += "\n".join(urgent_deliveries) + "\n"
            context += "Your ONLY valid action is deliver_supplies for one of the above.\n"
    
    # 5. Call LLM (With structured output)
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0)
    # llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    structured_llm = llm.with_structured_output(AgentOutput)

    # Re-apply pacing to prevent 429 Too Many Requests hanging the terminal
    await asyncio.sleep(4.5)
    
    try:
        response = await structured_llm.ainvoke(context)
    except Exception as e:
        # This will show the exact error (e.g. 404 Model Not Found or Auth Error) in terminal
        print(f"❌ LLM ERROR: {str(e)}")
        state["mission_log"].append(f"[ERROR] LLM call failed: {str(e)}")
        raise e
    
    # 4. Update Mission Log
    state["mission_log"].append(f"[THOUGHT] {response.thought}")
    state["mission_log"].append(f"[INTENT] {response.tool_call.name}: {response.tool_call.parameters}")
    state["next_action"] = response.tool_call
    
    return state


async def tool_execution_node(state: SwarmState) -> SwarmState:
    try:
        if not state.get("next_action"):
            return state
            
        # Securely grab intent directly from the state object
        tool_name = state["next_action"].name
        params = state["next_action"].parameters
        
        # Action routing

        # 1. Unlock Drone Guard: Block unlock_drone on active relay drones
        # The LLM can reach unlock_drone as a raw MCP tool. Unlocking a relay
        # drone while its main drone is still > 5 cells from base would silently
        # break the mesh link. We intercept here and demand the main drone flies
        # back within range first (the auto-release will then fire automatically).
        if tool_name == "unlock_drone":
            relay_target = params.get("drone_id")
            active_relays = state.get("active_relays", {})
            relaying_for = [main_id for main_id, rid in active_relays.items() if rid == relay_target]
            if relaying_for:
                main_id = relaying_for[0]
                main_drone = next((d for d in state["drones"] if d["id"] == main_id), None)
                hint = ""
                if main_drone:
                    hint = (
                        f" '{main_id}' is at ({main_drone['x']},{main_drone['y']}) — "
                        f"move it within 5 cells of base (0,0) to trigger auto-release, "
                        f"or deploy a handover relay at ({main_drone.get('x',0)//2},{main_drone.get('y',0)//2})."
                    )
                state["mission_log"].append(
                    f"[SYSTEM ERROR] RELAY SHIELD: Cannot unlock '{relay_target}' — "
                    f"it is the active relay for '{main_id}' and the mesh link is still live.{hint}"
                )
                return state

        # 2. Phase Guard: Block rescue tools during SEARCH phase
        # This is a hard, code-level enforcement — not just a prompt hint.
        # If any sector is still unscanned, supply/rescue operations are forbidden.
        unscanned_count = sum(1 for scanned in state.get("search_grid", {}).values() if not scanned)
        if unscanned_count > 0 and tool_name in ("list_supply_depots", "collect_supplies", "deliver_supplies"):
            remaining = [s for s, done in state.get("search_grid", {}).items() if not done]
            state["mission_log"].append(
                f"[SYSTEM] SEARCH PHASE LOCK: '{tool_name}' is forbidden while sectors remain unscanned: {remaining}. "
                f"Complete all scans before starting rescue operations."
            )
            return state

        # 3. Move Guard: Block move_to on active relay drones
        if tool_name == "move_to":
            drone_id = params.get("drone_id")
            target_x = params.get("x", 0)
            target_y = params.get("y", 0)
            
            # Check if drone exists
            drone = next((drone for drone in state["drones"] if drone["id"] == drone_id), None)
            
            if drone:
                # 3a. Persistent Relay Rule
                # If this drone is currently serving as a relay for another drone, 
                # it is locked in place unless a "Handover" drone is available.
                if "active_relays" in state and drone_id in state["active_relays"].values():
                    # See if any available drones are at the same location for handing over
                    handover_partner = next((d for d in state["drones"] if d["id"] != drone_id and d["x"] == drone["x"] and d["y"] == drone["y"] and d.get("status") == "idle" and not d.get("payload")), None)
                    
                    if handover_partner:
                        state["mission_log"].append(f"[SYSTEM] RELAY HANDOVER: {handover_partner['id']} taking over for {drone_id}.")
                        
                        # 1. Lock the new relay on the server
                        await mcp_client.session.call_tool("lock_drone", {"drone_id": handover_partner["id"]})
                        handover_partner["locked"] = True
                        
                        # 2. Unlock the original drone on the server
                        await mcp_client.session.call_tool("unlock_drone", {"drone_id": drone_id})
                        drone["locked"] = False

                        # Update the active relay map
                        for main_id, relay_id in state["active_relays"].items():
                            if relay_id == drone_id:
                                state["active_relays"][main_id] = handover_partner["id"]
                                break
                        # The original drone is now free to move
                    else:
                        state["mission_log"].append(f"[SYSTEM ERROR] PERSISTENT RELAY: {drone_id} is locked to maintain mesh connectivity.")
                        return state  # Block the move entirely
                
                # 3b. Battery Rule Override
                battery_override_active = False
                if drone["battery"] < BATTERY_LOW_THRESHOLD:
                    state["mission_log"].append(f"[SYSTEM] BATTERY RULE: {drone_id} battery < {BATTERY_LOW_THRESHOLD}%. Switching to return_to_charging_station.")
                    # Override name and params to use specialized safety tool
                    tool_name = "return_to_charging_station"
                    params = {"drone_id": drone_id}
                    battery_override_active = True
                
                # 3c. Relay Rule Override
                # Skip if the battery override is active — no relay needed for a return-to-base trip.
                if not battery_override_active:
                    distance = get_distance(drone["x"], drone["y"], target_x, target_y)
                    
                    if "active_relays" not in state:
                        state["active_relays"] = {}
                    
                    if distance > 5 and drone_id not in state["active_relays"]:
                        mid_x = int((drone["x"] + target_x) / 2)
                        mid_y = int((drone["y"] + target_y) / 2)
                        
                        # ── Shared Relay Check ─────────────
                        # Is there ANY drone already at this exact midpoint?
                        existing_relay = next((d for d in state["drones"] if d["id"] != drone_id and d["x"] == mid_x and d["y"] == mid_y and not d.get("payload")), None)
                        
                        if existing_relay:
                            state["mission_log"].append(f"[SYSTEM] RELAY RULE: Utilizing existing drone {existing_relay['id']} at ({mid_x}, {mid_y}) as shared relay.")
                            state["active_relays"][drone_id] = existing_relay["id"]
                            existing_relay["status"] = "relay"
                            # Skip move_to since it's already there
                        else:
                            state["mission_log"].append(f"[SYSTEM] RELAY RULE: Target > 5 cells. Deploying relay drone at midpoint ({mid_x}, {mid_y}).")
                            
                            # 3c1. Lowest Battery Heuristic
                            # Do NOT require status=="idle" — state is stale (only the
                            # commanded drone gets synced). Drones auto-transition to idle
                            # on the server after 5s, so "flying" in state is safe to use.
                            # Only exclude truly unavailable states: offline / charging.
                            # Also verify the candidate can actually afford the midpoint trip
                            # (same cost model as the simulator: 3% per cell + 25% reserve).
                            idle_drones = [
                                d for d in state["drones"] 
                                if d["id"] != drone_id
                                and not d.get("locked", False)
                                and d.get("status") not in ("offline", "charging")
                                and not d.get("payload")
                                and d.get("battery", 0) >= int(
                                    get_distance(d["x"], d["y"], mid_x, mid_y) * BATTERY_COST_PER_CELL
                                ) + BATTERY_RESERVE_MIN
                            ]
                            
                            relay_drone = min(idle_drones, key=lambda d: d.get("battery", 100)) if idle_drones else None
                            
                            if relay_drone:
                                try:
                                    # Note: We must await this tool call BEFORE the main move_to to ensure the link is established
                                    relay_res = await mcp_client.session.call_tool("move_to", {"drone_id": relay_drone["id"], "x": mid_x, "y": mid_y})
                                    relay_text = relay_res.content[0].text
                                    state["mission_log"].append(f"[MCP] {relay_text}")
                                    
                                    if "error" in relay_text.lower():
                                        state["mission_log"].append("[SYSTEM ERROR] Relay deployment failed. Aborting main move for safety.")
                                        return state

                                    # LOCK THE RELAY ON THE SERVER
                                    await mcp_client.session.call_tool("lock_drone", {"drone_id": relay_drone["id"]})
                                    relay_drone["x"] = mid_x
                                    relay_drone["y"] = mid_y
                                    relay_drone["locked"] = True
                                    state["active_relays"][drone_id] = relay_drone["id"]
                                except Exception as e:
                                    state["mission_log"].append(f"[MCP ERROR] Relay Exception: {str(e)}")
                                    return state
                            else:
                                state["mission_log"].append(f"[SYSTEM ERROR] NO IDLE DRONES: Required relay could not be deployed. Aborting move.")
                                return state
                            
        # Universal Dynamic Dispatcher
        res = await mcp_client.session.call_tool(tool_name, params)
        res_text = res.content[0].text
        state["mission_log"].append(f"[MCP] {res_text}")
        
        # Authoritative Base-Station Synchronization:
        # We secretly pull fresh telemetry from the MCP Physics Simulator in the background here.
        # This keeps the AI's SwarmState perfectly accurate without forcing the AI to waste 
        # API tokens/turns explicitly asking for battery updates.
        drone_id = params.get("drone_id")
        if drone_id and "error" not in res_text.lower():
            try:
                import json
                sync_res = await mcp_client.session.call_tool("get_drone_status", {"drone_id": drone_id})
                sync_data = json.loads(sync_res.content[0].text)
                
                drone = next((drone for drone in state["drones"] if drone["id"] == drone_id), None)
                if drone and "error" not in sync_data:
                    drone["battery"] = sync_data.get("battery", drone["battery"])
                    
                    # Do NOT overwrite status if this drone is acting as a relay.
                    # The MCP server doesn't know about "relay" status — it would return
                    # "idle" or "flying", which would silently break the persistent relay lock.
                    is_relay = drone["id"] in state.get("active_relays", {}).values()
                    if not is_relay:
                        drone["status"] = sync_data.get("status", drone["status"])
                    
                    position = sync_data.get("position", {})
                    drone["x"] = position.get("x", drone["x"])
                    drone["y"] = position.get("y", drone["y"])
                    drone["locked"] = sync_data.get("locked", drone["locked"])
            except Exception:
                pass

        # Post-Tool application heuristics to advance the LangGraph loops
        if tool_name in ["thermal_scan"]:
            drone = next((drone for drone in state["drones"] if drone["id"] == params.get("drone_id")), None)
            matched = False
            if drone:
                for section, data in PRIORITY_MAP.items():
                    # Check if drone coordinates match the sector grid and it's unscanned
                    if data.get("x") == drone["x"] and data.get("y") == drone["y"] and not state.get("search_grid", {}).get(section, True):
                        state["search_grid"][section] = True
                        state["mission_log"].append(f"[SYSTEM] Sector '{section}' marked as scanned.")
                        matched = True
                        break
            
            # Fallback heuristic if somehow scanned off-center, to prevent infinite loops
            if not matched:
                target_sectors = [sector for sector, scanned in state.get("search_grid", {}).items() if not scanned]
                if target_sectors:
                    state["search_grid"][target_sectors[0]] = True
                    state["mission_log"].append(f"[SYSTEM] Fallback heuristic: marked '{target_sectors[0]}' as scanned.")

        # ── 3. Auto-Release Logic ─────────────────────────────────────────────
        # If a drone that was using a relay flies back into the safe communication
        # range (distance <= 5 from base), the relay drone is automatically released.
        if "active_relays" in state:
            released_ids = []
            for main_id, relay_id in state["active_relays"].items():
                main_drone = next((d for d in state["drones"] if d["id"] == main_id), None)
                if main_drone:
                    dist_to_base = get_distance(main_drone["x"], main_drone["y"], mcp_client.base_x, mcp_client.base_y)
                    if dist_to_base <= 5:
                        relay_drone = next((d for d in state["drones"] if d["id"] == relay_id), None)
                        if relay_drone:
                            # UNLOCK THE RELAY ON THE SERVER
                            await mcp_client.session.call_tool("unlock_drone", {"drone_id": relay_id})
                            relay_drone["status"] = "idle"
                            state["mission_log"].append(f"[SYSTEM] AUTO-RELEASE: Relay {relay_id} released (Signal link no longer needed).")
                        released_ids.append(main_id)
            
            for lid in released_ids:
                del state["active_relays"][lid]
            
    except Exception as e:
         state["mission_log"].append(f"[ERROR] Tool execution failed: {str(e)}")
         
    return state


def route_after_execution(state: SwarmState) -> str:
    if not state["mission_log"]:
         return "commander_node"
         
    last_log = state["mission_log"][-1]
    
    # 1. Self Healing Rule
    if "[ERROR]" in last_log or "Error" in last_log or "Jitter" in last_log:
        return "recovery_node"
        
    # 2. Check if all tasks complete
    if all(scanned for scanned in state.get("search_grid", {}).values()):
        return END
        
    return "commander_node"


# UNDER DEVELOPMENT
async def recovery_node(state: SwarmState) -> SwarmState:
    state["mission_log"].append("[SYSTEM] Initiating self-healing protocol.")
    
    # Extract errored drone if possible
    drone_id = "DRONE_ALPHA"
    if state.get("next_action") and "drone_id" in state["next_action"].parameters:
        drone_id = state["next_action"].parameters["drone_id"]
                
    res = await mcp_client.session.call_tool("attempt_drone_recovery", {"drone_id": drone_id})
    state["mission_log"].append(f"[RECOVERY] {res.content[0].text}")
    return state
