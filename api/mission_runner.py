"""
MissionRunner — manages in-flight missions as asyncio Tasks.
Each mission gets a UUID, runs the CommandAgent in the background,
and streams log lines into an asyncio.Queue that SSE clients consume.
"""
from __future__ import annotations
import asyncio
import importlib
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ── Data model ─────────────────────────────────────────────────────────────────

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
            # Ensure agent package is importable
            agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent"))
            if agent_dir not in sys.path:
                sys.path.append(agent_dir)

            from dotenv import load_dotenv
            load_dotenv(os.path.join(agent_dir, ".env"))

            from agent.graph import create_graph
            from agent.state import SwarmState
            from agent.mcp.client import mcp_client
            # ── 1. Resolve Scenario Configuration ──────────────────────────────
            # We dynamically load the scenario module to ensure the AI and the simulation 
            # use the EXACT SAME drone fleet and survivor locations.
            try:
                sc_mod = importlib.import_module(f"scenarios.{state.scenario}")
                fleet = getattr(sc_mod, "INITIAL_FLEET", [])
            except Exception:
                # Fallback to the global default if the scenario module is missing
                from utils.config import INITIAL_FLEET as fleet

            agent_drones = [
                {
                    "id":      drone["id"],
                    "battery": drone["battery"],
                    "x":       drone["x"],
                    "y":       drone["y"],
                    "status":  "offline" if drone.get("offline") else "idle",
                }
                for drone in fleet
            ]
            initial_state = SwarmState(
                drones=agent_drones,
                mission_log=[],
                search_grid={
                    "sector_1": False,
                    "sector_2": True,
                    "sector_3": False,
                    "sector_4": True,
                    "sector_5": False,
                },
                active_relays={},
                next_action=None,
                mission_prompt=prompt,
            )

            # ── 2. Sync Local World Tracking ────────────────────────────────────
            # The API process maintains its own tracking state for the dashboard.
            # We must force it to reset its survivors/drones to match the chosen scenario.
            from mcp_server.world_state import world as local_world
            local_world.reinitialize(state.scenario)

            # ── 3. Start the MCP server side-car ────────────────────────────────
            server_path = Path(__file__).parent.parent / "mcp_server" / "server.py"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).parent.parent)
            env["SCENARIO"]   = state.scenario  # Synchronize simulation with chosen mission
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(server_path)],
                env=env,
            )

            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    mcp_client.set_session(session)

                    # ── 3. Run the LangGraph agent ──────────────────────────────
                    print(f"\n🚀 MISSION {state.mission_id} STARTED — Running LangGraph swarm controller...")
                    app = create_graph()
                    log_index = 0

                    await _broadcast(state, {"type": "log", "message": "SIREN ONLINE — LangGraph swarm controller initialised."})

                    async for event in app.astream(initial_state, {"recursion_limit": 50}):
                        for node_name, state_update in event.items():

                            if "mission_log" not in state_update:
                                # Node transition with no new log entries — emit a lightweight notice
                                await _broadcast(state, {"type": "log", "message": f"Graph entered node: {node_name}"})
                                continue

                            # Emit each new log entry as a SSE step event
                            new_logs = state_update["mission_log"][log_index:]
                            for log_msg in new_logs:
                                state.step_count += 1
                                # print(f"  🤖 AGENT [{node_name}]: {log_msg}")

                                ui_tool = _classify_tool(log_msg, node_name)
                                await _broadcast(state, {
                                    "type":           "step",
                                    "phase":          node_name,
                                    "reasoning":      log_msg,
                                    "tool":           ui_tool,
                                    "result_summary": "success",
                                })
                                await asyncio.sleep(0.4)  # Pacing for live-feed feel

                            # Sync world state to the dashboard after each agent step
                            try:
                                snap = await session.call_tool("get_world_state", {})
                                if not snap.isError:
                                    _sync_local_world(snap.content[0].text)
                            except Exception:
                                pass

                            log_index = len(state_update["mission_log"])

            # ── 4. Mission complete ─────────────────────────────────────────────
            state.status = "complete"
            state.finished_at = datetime.now(timezone.utc).isoformat()
            print(f"🏁 MISSION {state.mission_id} FINISHED\n")
            await _broadcast(state, {"type": "complete", "debrief": "Mission finished via LangGraph controller."})

        except Exception as exc:
            state.status = "failed"
            state.error = str(exc)
            state.finished_at = datetime.now(timezone.utc).isoformat()
            print(f"💥 MISSION {state.mission_id} FAILED: {exc}")
            await _broadcast(state, {"type": "error", "message": str(exc)})


# ── Helpers ─────────────────────────────────────────────────────────────────────

async def _broadcast(state: MissionState, event: dict) -> None:
    """Append an event to the persistent history and push it to all active SSE subscribers."""
    state.history.append(event)
    for q in list(state.subscribers):
        await q.put(event)


def _classify_tool(log_msg: str, node_name: str) -> str:
    """Derive a friendly UI tool label from the agent log message prefix."""
    if log_msg.startswith("[INTENT] "):
        return log_msg.split("[INTENT] ")[1].split(":")[0]
    if log_msg.startswith("[THOUGHT] "):
        return "thinking"
    if log_msg.startswith("[MCP] "):
        return "mcp_result"
    return node_name


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
            d.assigned_sector = d_data.get("assigned_sector")
            d.payload = d_data.get("payload")

    for s_data in snap.get("survivors", []):
        if s := local_world.get_survivor(s_data["survivor_id"]):
            s.x, s.y, s.detected, s.rescued = s_data["position"]["x"], s_data["position"]["y"], s_data["detected"], s_data["rescued"]

    local_world.mesh_log = snap.get("mesh_log", local_world.mesh_log)


# Module-level singleton
runner = MissionRunner()