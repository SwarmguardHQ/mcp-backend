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

from agent.command_agent import CommandAgent
from agent.reasoning_log import ReasoningLog


@dataclass
class MissionState:
    mission_id:   str
    scenario:     str
    status:       str = "running"           # running | complete | failed
    log_queue:    asyncio.Queue = field(default_factory=asyncio.Queue)
    reasoning_log: Optional[ReasoningLog]  = None
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
            agent = _PatchedCommandAgent(prompt, state.log_queue)
            state.reasoning_log = agent.log
            debrief = await agent.run()

            state.status      = "complete"
            state.finished_at = datetime.now(timezone.utc).isoformat()
            await state.log_queue.put({"type": "complete", "debrief": debrief})

        except Exception as exc:
            state.status      = "failed"
            state.error       = str(exc)
            state.finished_at = datetime.now(timezone.utc).isoformat()
            await state.log_queue.put({"type": "error", "message": str(exc)})


# ── Patched agent that also writes to the SSE queue ───────────────────────────

class _PatchedCommandAgent(CommandAgent):
    """
    Thin subclass that intercepts log entries and pushes them
    into the SSE queue so HTTP clients see live chain-of-thought.
    """

    def __init__(self, mission_prompt: str, queue: asyncio.Queue):
        super().__init__(mission_prompt)
        self._queue = queue

    def _record(self, phase: str, reasoning: str, tool_name: str,
                tool_args: dict, result: dict, next_step: str) -> None:
        self.log.add(phase, reasoning, tool_name, tool_args, result, next_step)
        asyncio.create_task(
            self._queue.put({
                "type":      "step",
                "phase":     phase,
                "tool":      tool_name,
                "reasoning": reasoning,
                "result_summary": _short(result),
            })
        )

    async def _agentic_loop(self, session) -> str:  # type: ignore[override]
        """Override to hook into the record path."""
        import json
        from agent.command_agent import _extract_reasoning, _short as _s

        self.messages = [{"role": "user", "content": self.mission_prompt}]
        phase_counter = 1

        await self._queue.put({"type": "log", "message": "SIREN ONLINE — Initialising swarm discovery protocol..."})

        while True:
            # TODO: LOOP IN AGENT @JACK
            response = self.client.messages.create(
                model=self.client.models if False else __import__("config").AGENT_MODEL,
                max_tokens=__import__("config").AGENT_MAX_TOKENS,
                system=__import__("agent.command_agent", fromlist=["SYSTEM_PROMPT"]).SYSTEM_PROMPT,
                tools=self.tools,
                messages=self.messages,
            )

            for block in response.content:
                if hasattr(block, "text") and block.text:
                    await self._queue.put({"type": "log", "message": block.text})

            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                result_raw = await session.call_tool(block.name, block.input)
                result     = json.loads(result_raw.content[0].text) if result_raw.content else {}

                reasoning = _extract_reasoning(response.content)
                self._record(
                    phase     = f"Phase {phase_counter}: {block.name}",
                    reasoning = reasoning,
                    tool_name = block.name,
                    tool_args = block.input,
                    result    = result,
                    next_step = "(continued)",
                )
                phase_counter += 1

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     json.dumps(result),
                })

            if tool_results:
                self.messages.append({"role": "user", "content": tool_results})

        return self.log.render_full()


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