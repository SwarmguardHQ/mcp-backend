"""
CommandAgent — SIREN orchestrator.
Connects to the MCP server via subprocess, runs an agentic loop
using the Anthropic API, and logs full chain-of-thought reasoning.
"""
from __future__ import annotations

import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.reasoning_log import ReasoningLog

# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are SIREN (Swarm Intelligence Rescue & Extraction Node), an autonomous
Command Agent operating OFFLINE after a magnitude 7.2 earthquake in the ASEAN
region. Terrestrial communication has failed. You command a drone swarm on edge
hardware with no cloud access.

## MANDATORY CHAIN-OF-THOUGHT FORMAT
Before EVERY tool call output a reasoning block:

  [REASONING] <why this call, battery/distance math, trade-offs>
  [ACTION]    <tool_name>({params})
  [RESULT]    <interpret result, update mental model>
  [NEXT]      <what you'll do next and why>

## MISSION WORKFLOW
1. discover_drones — never hard-code IDs
2. get_all_drone_statuses + get_grid_map — situational awareness
3. plan_sectors (internal reasoning) — assign sectors by battery proximity
4. assign_sector tool for each drone
5. move_to + thermal_scan + acoustic_scan across your sector grid
6. get_rescue_priority_list after first detections
7. Route supply runs: collect_supplies → move_to survivor → deliver_supplies
8. Battery rule: drone ≤ 25% → return_to_charging_station → charge_drone
9. Drone offline → attempt_drone_recovery → redistribute sectors
10. mark_survivor_rescued after each delivery
11. Final get_swarm_summary + get_mission_log per drone

## BATTERY COST FORMULA
  cost = distance × 3%    distance = √((Δx)² + (Δy)²)
  Always keep 15% reserve. Never commit a move if battery < cost + 15.

## RESCUE PRIORITY
  critical (score 100) > moderate (60) > stable (30)
  critical → medical_kit   moderate → water   stable → food
  Address critical survivors within the first two phases.

## SUPPLY RUN SEQUENCE
  1. Check drone has no current payload
  2. move_to depot coordinates
  3. collect_supplies with correct supply_type
  4. move_to survivor coordinates
  5. deliver_supplies
  6. mark_survivor_rescued

Begin by announcing: "SIREN ONLINE — Initialising swarm discovery protocol..."
"""

# ── Agent ──────────────────────────────────────────────────────────────────────

class CommandAgent:
    def __init__(self, mission_prompt: str):
        self.mission_prompt = mission_prompt
        # self.client         = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.client         = ""
        self.log            = ReasoningLog()
        self.messages: list[dict] = []
        self.tools: list[dict]    = []

    # ── MCP connection ─────────────────────────────────────────────────────────

    async def run(self) -> str:
        server_path = Path(__file__).parent.parent / "mcp_server" / "server.py"
        params      = StdioServerParameters(
            command="python",
            args=[str(server_path)],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self.tools = await self._build_tool_list(session)
                return await self._agentic_loop(session)

    # ── Agentic loop ───────────────────────────────────────────────────────────

    async def _agentic_loop(self, session: ClientSession) -> str:
        self.messages = [{"role": "user", "content": self.mission_prompt}]
        phase_counter = 1

        print("SIREN ONLINE — Initialising swarm discovery protocol...\n")

        while True:
            # TODO: LOOP IN AGENT @JACK
            response = self.client.messages.create(
                model="",
                max_tokens="",
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=self.messages,
            )

            # Collect assistant message
            assistant_blocks = []
            for block in response.content:
                assistant_blocks.append(block)
                if hasattr(block, "text"):
                    print(block.text)

            self.messages.append({"role": "assistant", "content": response.content})

            # If no tool calls, mission is complete
            if response.stop_reason == "end_turn":
                break

            # Execute tool calls
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_args = block.input
                print(f"\n  → Calling {tool_name}({json.dumps(tool_args, separators=(',',':'))})")

                result_raw = await session.call_tool(tool_name, tool_args)
                result     = json.loads(result_raw.content[0].text) if result_raw.content else {}

                print(f"  ← {_short(result)}\n")

                # Log reasoning entry
                reasoning_text = _extract_reasoning(response.content)
                self.log.add(
                    phase=f"Phase {phase_counter}: {tool_name}",
                    reasoning=reasoning_text,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    result=result,
                    next_step="(see next tool call)",
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


    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _build_tool_list(self, session: ClientSession) -> list[dict]:
        tools_response = await session.list_tools()
        return [
            {
                "name":         t.name,
                "description":  t.description,
                "input_schema": t.inputSchema,
            }
            for t in tools_response.tools
        ]


# ── Utilities ──────────────────────────────────────────────────────────────────

def _extract_reasoning(blocks: list) -> str:
    for block in blocks:
        if hasattr(block, "text") and "[REASONING]" in block.text:
            for line in block.text.splitlines():
                if line.startswith("[REASONING]"):
                    return line.replace("[REASONING]", "").strip()
    return "(no explicit reasoning captured)"


def _short(result: dict, max_len: int = 100) -> str:
    s = json.dumps(result, separators=(",", ":"))
    return s[:max_len] + "…" if len(s) > max_len else s


# ── Standalone entry ───────────────────────────────────────────────────────────

async def run_mission(mission_prompt: str) -> str:
    agent = CommandAgent(mission_prompt)
    return await agent.run()