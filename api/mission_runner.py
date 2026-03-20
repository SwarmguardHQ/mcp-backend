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
    log_queue:    asyncio.Queue = field(default_factory=asyncio.Queue)
    step_count:   int = 0
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
                    
                    await state.log_queue.put({"type": "log", "message": "SIREN ONLINE — LangGraph swarm controller initialised."})

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
                                    await state.log_queue.put({
                                        "type":      "step",
                                        "phase":     node_name,
                                        "reasoning": log_msg,
                                        "tool":      "graph_node",
                                        "result_summary": "(update)"
                                    })
                                    # Artificial pacing for "line-by-line" feel
                                    await asyncio.sleep(0.4) 
                                log_index = len(state_update["mission_log"])
                            else:
                                # For other updates, still log the node transition
                                await state.log_queue.put({
                                    "type": "log",
                                    "message": f"Graph entered node: {node_name}"
                                })

            state.status      = "complete"
            state.finished_at = datetime.now(timezone.utc).isoformat()
            await state.log_queue.put({"type": "complete", "debrief": "Mission finished via LangGraph controller."})

        except Exception as exc:
            state.status      = "failed"
            state.error       = str(exc)
            state.finished_at = datetime.now(timezone.utc).isoformat()
            await state.log_queue.put({"type": "error", "message": str(exc)})


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


# Module-level singleton
runner = MissionRunner()