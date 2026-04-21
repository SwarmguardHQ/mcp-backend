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
                    "locked":  drone.get("locked", False),
                    "payload": drone.get("payload"),
                }
                for drone in fleet
            ]

            # ── Seed the pheromone grid from PRIORITY_MAP ──────────────────────
            # priority_rank_to_float converts rank (1=most urgent) → GridCell
            # priority float (higher = stronger pheromone).
            # Pre-scanned sectors (sector_2, sector_4 in default scenario) are
            # seeded at priority=0.0 with scanned=True so drones skip them.
            from agent.utils import PRIORITY_MAP, priority_rank_to_float

            # Sectors that are pre-scanned in this scenario
            PRESCANNED_SECTORS = {}

            initial_search_grid = {
                sid: {
                    "priority":   0.0 if sid in PRESCANNED_SECTORS
                                  else priority_rank_to_float(data["priority"]),
                    "claimed_by": None,
                    "scanned":    sid in PRESCANNED_SECTORS,
                }
                for sid, data in PRIORITY_MAP.items()
            }

            initial_state = SwarmState(
                drones=agent_drones,
                mission_log=[],
                search_grid=initial_search_grid,
                signal_map={},
                active_relays={},
                rescue_directive=None,
                mission_prompt=prompt,
                detected_survivors=[],
                rescued_survivors=[],
                phase="search",
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

                    # ── 3. Wire the per-step world sync callback ─────────────────
                    # Nodes call `await mcp_client.step_sync()` after every single
                    # MCP tool call (move, scan, collect, deliver).  This callback
                    # immediately pulls get_world_state, syncs local_world, emits a
                    # world_sync SSE event, then sleeps briefly so the frontend
                    # animation can render the position update before the next action.
                    async def _step_sync_callback() -> None:
                        try:
                            snap = await session.call_tool("get_world_state", {})
                            if not snap.isError:
                                _sync_local_world(snap.content[0].text)
                                await _broadcast(state, {"type": "world_sync"})
                        except Exception:
                            pass
                        # Pause so the frontend has time to animate the change
                        # before the next MCP call fires inside the same node.
                        await asyncio.sleep(0.5)

                    mcp_client._on_step_complete = _step_sync_callback

                    # ── 4. Run the LangGraph agent ──────────────────────────────
                    print(f"\n MISSION {state.mission_id} STARTED — Running LangGraph swarm controller...")
                    app = create_graph()

                    await _broadcast(state, {"type": "log", "message": "SIREN ONLINE — LangGraph swarm controller initialised."})


                    # Background poller — kept as a 3 s safety‑net in case step_sync
                    # is not called (e.g., during a long LLM call with no MCP action).
                    poller = asyncio.create_task(
                        _world_state_poller(session, state, interval=3.0)
                    )

                    try:
                        async for event in app.astream(initial_state, {"recursion_limit": 200}):
                            for node_name, state_update in event.items():

                                if "mission_log" not in state_update:
                                    # Node transition with no new log entries — emit a lightweight notice
                                    await _broadcast(state, {"type": "log", "message": f"Graph entered node: {node_name}"})
                                    continue

                                # Emit each new log entry as a SSE step event
                                for log_msg in state_update["mission_log"]:
                                    state.step_count += 1

                                    ui_tool = _classify_tool(log_msg, node_name)
                                    await _broadcast(state, {
                                        "type":           "step",
                                        "phase":          node_name,
                                        "reasoning":      log_msg,
                                        "tool":           ui_tool,
                                        "result_summary": "success",
                                    })
                                    await asyncio.sleep(0.4)  # Pacing for live-feed feel

                                # Sync world state after each node completes (belt-and-braces;
                                # the background poller already does this continuously).
                                try:
                                    snap = await session.call_tool("get_world_state", {})
                                    if not snap.isError:
                                        _sync_local_world(snap.content[0].text)
                                        await _broadcast(state, {"type": "world_sync"})
                                except Exception:
                                    pass

                    finally:

                        # Always cancel the poller when the graph finishes or errors.
                        poller.cancel()
                        try:
                            await poller
                        except asyncio.CancelledError:
                            pass

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

async def _world_state_poller(session, mission_state: MissionState, interval: float = 1.5) -> None:
    """
    Background task — polls MCP for live drone positions while LangGraph runs.

    WHY THIS IS NEEDED:
      During a parallel Send wave, all drone_agent_node coroutines execute their
      move_to / thermal_scan MCP calls concurrently INSIDE asyncio sub-tasks.
      LangGraph only emits an astream event AFTER the entire parallel batch
      completes. Without a poller, the frontend receives all position updates in
      one simultaneous burst (a "teleport" effect).

      This task runs every `interval` seconds independently of the graph,
      syncing local_world for the /world_state endpoint and emitting a
      lightweight `world_sync` SSE event so connected frontends can re-fetch
      smoothly. Result: drones appear to move progressively in ~1.5 s increments.

    CONCURRENCY SAFETY:
      MCP stdio transport is JSON-RPC with per-request IDs — concurrent calls
      from the poller and the astream loop are multiplexed safely by the protocol.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            snap = await session.call_tool("get_world_state", {})
            if not snap.isError:
                _sync_local_world(snap.content[0].text)
                await _broadcast(mission_state, {"type": "world_sync"})
        except asyncio.CancelledError:
            raise   # let the task end cleanly
        except Exception:
            pass    # non-fatal — next tick will retry


async def _broadcast(state: MissionState, event: dict) -> None:
    """Append an event to the persistent history and push it to all active SSE subscribers."""
    state.history.append(event)
    for q in list(state.subscribers):
        await q.put(event)


def _classify_tool(log_msg: str, node_name: str) -> str:
    """Derive a friendly UI tool label from the agent log message prefix."""
    # New swarm architecture prefixes
    if log_msg.startswith("[STRATEGIST]"):
        return "strategist"
    if log_msg.startswith("[DRONE"):
        drone_id = log_msg.split("[")[1].split("]")[0]
        return f"{drone_id.lower()}"
    if log_msg.startswith("[RESCUE]"):
        return "rescue_execution"
    if log_msg.startswith("[GOVERNOR]"):
        return "safety_governor"
    if log_msg.startswith("[DISPATCH]"):
        return "dispatch"
    if log_msg.startswith("[JOIN]"):
        return "join"
    if log_msg.startswith("[RELAY]"):
        return "relay"
    # Legacy prefixes (kept for backward compat)
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