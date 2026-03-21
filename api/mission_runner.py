"""
MissionRunner — manages in-flight missions as asyncio Tasks.
Each mission gets a UUID, runs the CommandAgent in the background,
and streams log lines into an asyncio.Queue that SSE clients consume.
"""
from __future__ import annotations
import asyncio
import importlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional




@dataclass
class MissionState:
    mission_id:   str
    scenario:     str
    status:       str = "running"           # running | complete | failed
    subscribers:   list[asyncio.Queue] = field(default_factory=list) # Active SSE log listeners (allows multi-tab support)
    history:       list[dict] = field(default_factory=list) # Persistent log history (replayed on page refresh)
    step_count:    int = 0
    task:         Optional[asyncio.Task]   = None
    started_at:   str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at:  Optional[str] = None
    error:        Optional[str] = None


class MissionRunner:
    """
    Singleton that holds all active and recently completed missions.
    Access via `runner` at module level.
    """

    def __init__(self):
        self._missions: dict[str, MissionState] = {}

    # ── public API ─────────────────────────────────────────────────────────────

    def start(self, scenario: str, custom_prompt: Optional[str] = None) -> MissionState:
        """
        Resolve the scenarios prompt, create a MissionState, and launch
        the CommandAgent as a background asyncio Task.
        """
        prompt = custom_prompt or _load_prompt(scenario)
        mid    = str(uuid.uuid4())[:8]
        state  = MissionState(mission_id=mid, scenario=scenario)
        self._missions[mid] = state

        state.task = asyncio.create_task(
            self._run(state, prompt),
            name=f"mission-{mid}",
        )
        return state

    def get(self, mission_id: str) -> Optional[MissionState]:
        return self._missions.get(mission_id)

    def list_all(self) -> list[dict]:
        return [
            {
                "mission_id":  m.mission_id,
                "scenarios":    m.scenario,
                "status":      m.status,
                "started_at":  m.started_at,
                "finished_at": m.finished_at,
            }
            for m in self._missions.values()
        ]

    # ── internal ───────────────────────────────────────────────────────────────

    async def _run(self, state: MissionState, prompt: str) -> None:
        try:
            import sys
            import os
            from pathlib import Path
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            # 1. Ensure the new agent logic is in sys.path
            # The agent project root is at mcp-backend/agent or cmd-agent
            agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent"))
            if agent_dir not in sys.path:
                sys.path.append(agent_dir)

            from dotenv import load_dotenv
            load_dotenv(os.path.join(agent_dir, ".env"))

            from agent.graph import create_graph
            from agent.state import SwarmState
            from agent.mcp.client import mcp_client
            from utils.config import INITIAL_FLEET

            # 2. Setup initial state
            agent_drones = []
            for d in INITIAL_FLEET:
                status = "offline" if d.get("offline") else "idle"
                agent_drones.append({
                    "id": d["id"], "battery": d["battery"], "x": d["x"], "y": d["y"], "status": status
                })
            
            initial_state = SwarmState(
                drones=agent_drones,
                mission_log=[],
                search_grid={"sector_1": False, "sector_2": True, "sector_3": False, "sector_4": True, "sector_5": False},
                relay_active=False,
                mission_prompt=prompt
            )

            # 3. Setup MCP connection
            server_path = Path(__file__).parent.parent / "mcp_server" / "server.py"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).parent.parent) 
            
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(server_path)],
                env=env
            )

            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    mcp_client.set_session(session)

                    # 4. Run the graph
                    app = create_graph()
                    log_index = 0
                    
                    initial_log = {"type": "log", "message": "SIREN ONLINE — LangGraph swarm controller initialised."}
                    state.history.append(initial_log)
                    for subscriber_queue in list(state.subscribers):
                        await subscriber_queue.put(initial_log)

                    async for event in app.astream(initial_state, {"recursion_limit": 50}):
                        # Log the whole event for debugging to terminal
                        print(f"DEBUG Mission {state.mission_id} event: {list(event.keys())}")
                        
                        for node_name, state_update in event.items():
                            # LangGraph astream yields {node_name: {state_delta}}
                            if "mission_log" in state_update:
                                new_logs = state_update["mission_log"][log_index:]
                                for log_msg in new_logs:
                                    state.step_count += 1
                                    print(f"[{node_name}] {log_msg}") # Echo to terminal
                                    
                                    # Determine a friendly tool name for the UI
                                    ui_tool = node_name
                                    if log_msg.startswith("[INTENT] "):
                                        ui_tool = log_msg.split("[INTENT] ")[1].split(":")[0] # Extract actual tool name e.g. move_to / thermal_scan
                                    elif log_msg.startswith("[THOUGHT] "):
                                        ui_tool = "thinking" # Set label to thinking
                                    elif log_msg.startswith("[MCP] "):
                                        ui_tool = "mcp_result"

                                    event_data = {
                                        "type":      "step",
                                        "phase":     node_name,
                                        "reasoning": log_msg,
                                        "tool":      ui_tool,
                                        "result_summary": "(update)"
                                    }
                                    state.history.append(event_data)
                                    for subscriber_queue in list(state.subscribers):
                                        await subscriber_queue.put(event_data)
                                    # Artificial pacing for "line-by-line" feel
                                    await asyncio.sleep(0.4) 

                                # Proactively update the global world state for the dashboard
                                try:
                                    snap_res = await session.call_tool("get_world_state", {})
                                    if not snap_res.isError:
                                        _sync_local_world(snap_res.content[0].text)
                                except:
                                    pass

                                log_index = len(state_update["mission_log"])
                            else:
                                # For other updates, still log the node transition
                                node_log = {
                                    "type": "log",
                                    "message": f"Graph entered node: {node_name}"
                                }
                                state.history.append(node_log)
                                for subscriber_queue in list(state.subscribers):
                                    await subscriber_queue.put(node_log)

            state.status      = "complete"
            state.finished_at = datetime.now(timezone.utc).isoformat()
            completion_log = {"type": "complete", "debrief": "Mission finished via LangGraph controller."}
            state.history.append(completion_log)
            for subscriber_queue in list(state.subscribers):
                await subscriber_queue.put(completion_log)

        except Exception as exc:
            state.status      = "failed"
            state.error       = str(exc)
            state.finished_at = datetime.now(timezone.utc).isoformat()
            error_log = {"type": "error", "message": str(exc)}
            state.history.append(error_log)
            for subscriber_queue in list(state.subscribers):
                await subscriber_queue.put(error_log)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_prompt(scenario: str) -> str:
    """Load MISSION_PROMPT from the scenarios package without hardcoding names."""
    try:
        mod = importlib.import_module(f"scenarios.{scenario}")
        return mod.MISSION_PROMPT
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Scenario '{scenario}' not found or has no MISSION_PROMPT: {e}")


def _short(result: dict, max_len: int = 120) -> str:
    import json
    s = json.dumps(result, separators=(",", ":"))
    return s[:max_len] + "…" if len(s) > max_len else s


def _sync_local_world(snapshot_text: str):
    """Parses MCP snapshot and updates the global WorldState singleton."""
    import json
    from mcp_server.world_state import world as local_world
    from mcp_server.drone_simulator import DroneStatus
    
    snap = json.loads(snapshot_text)
    
    for d_data in snap.get("drones", []):
        if d := local_world.get_drone(d_data["drone_id"]):
            d.x, d.y = d_data["position"]["x"], d_data["position"]["y"]
            d.battery = d_data["battery"]
            d.status = DroneStatus(d_data["status"])

    for s_data in snap.get("survivors", []):
        if s := local_world.get_survivor(s_data["survivor_id"]):
            s.x, s.y, s.detected, s.rescued = s_data["position"]["x"], s_data["position"]["y"], s_data["detected"], s_data["rescued"]

    local_world.mesh_log = snap.get("mesh_log", local_world.mesh_log)


# Module-level singleton
runner = MissionRunner()