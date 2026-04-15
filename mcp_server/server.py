"""
MCP Server — registers all drone tools and starts the stdio server.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

import mcp_server.tools as T
import mcp_server.drone_registry as D

server = Server("swarm-mcp")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Tool catalogue ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> ListToolsResult:
    return ListToolsResult(tools=[

        # Discovery
        Tool(name="discover_drones",
             description="List all drones on the mesh. ALWAYS call first — never hard-code IDs.",
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="get_all_drone_statuses",
             description="Fleet-wide snapshot: battery, position, status for every drone.",
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="assign_sector",
             description="Assign a sector label to a drone for coverage tracking.",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "drone_id":     {"type": "string"},
                     "sector_label": {"type": "string"},
                 },
                 "required": ["drone_id", "sector_label"],
             }),

        # Movement
        Tool(name="move_to",
             description="Fly drone to grid (x,y). Fails if battery is insufficient.",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "drone_id": {"type": "string"},
                     "x": {"type": "integer", "minimum": 0, "maximum": 9},
                     "y": {"type": "integer", "minimum": 0, "maximum": 9},
                 },
                 "required": ["drone_id", "x", "y"],
             }),
        Tool(name="get_grid_map",
             description="ASCII map of disaster zone: drones, survivors, depots, chargers.",
             inputSchema={"type": "object", "properties": {}, "required": []}),

        # Scanning
        Tool(name="thermal_scan",
             description="Thermal IR scan at drone position (radius 1.5 cells). Auto-broadcasts detections.",
             inputSchema={
                 "type": "object",
                 "properties": {"drone_id": {"type": "string"}},
                 "required": ["drone_id"],
             }),
        Tool(name="acoustic_scan",
             description="Acoustic vibration scan through rubble (radius 1.0 cells).",
             inputSchema={
                 "type": "object",
                 "properties": {"drone_id": {"type": "string"}},
                 "required": ["drone_id"],
             }),

        # Battery
        Tool(name="get_battery_status",
             description="Battery %, range estimate, and recommendation for a drone.",
             inputSchema={
                 "type": "object",
                 "properties": {"drone_id": {"type": "string"}},
                 "required": ["drone_id"],
             }),
        Tool(name="return_to_charging_station",
             description="Navigate drone to nearest charger. Use when battery ≤ 25%.",
             inputSchema={
                 "type": "object",
                 "properties": {"drone_id": {"type": "string"}},
                 "required": ["drone_id"],
             }),
        Tool(name="charge_drone",
             description="Restore battery (drone must be at a charging station).",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "drone_id":       {"type": "string"},
                     "charge_percent": {"type": "integer", "minimum": 1, "maximum": 100},
                 },
                 "required": ["drone_id"],
             }),

        # Supplies
        Tool(name="list_supply_depots",
             description="Show all depot locations and their available supply items.",
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="collect_supplies",
             description="Pick up a supply item from a depot at the drone's current cell.",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "drone_id":    {"type": "string"},
                     "supply_type": {
                         "type": "string",
                         "enum": ["medical_kit", "water", "food", "rope", "tarp", "radio"],
                     },
                 },
                 "required": ["drone_id", "supply_type"],
             }),
        Tool(name="deliver_supplies",
             description="Deliver current payload to a survivor (drone must be within 1.5 cells).",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "drone_id":    {"type": "string"},
                     "survivor_id": {"type": "string"},
                 },
                 "required": ["drone_id", "survivor_id"],
             }),

        # Rescue
        Tool(name="get_rescue_priority_list",
             description="Detected survivors ranked by urgency (critical > moderate > stable).",
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="mark_survivor_rescued",
             description="Mark a survivor as rescued. Broadcasts update to swarm.",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "survivor_id": {"type": "string"},
                     "drone_id":    {"type": "string"},
                 },
                 "required": ["survivor_id", "drone_id"],
             }),

        # Status
        Tool(name="get_drone_status",
             description="Detailed status for a single drone.",
             inputSchema={
                 "type": "object",
                 "properties": {"drone_id": {"type": "string"}},
                 "required": ["drone_id"],
             }),
        Tool(name="get_swarm_summary",
             description="High-level swarm health: drones active/offline, survivors rescued.",
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="get_mission_log",
             description="Full event log for a specific drone.",
             inputSchema={
                 "type": "object",
                 "properties": {"drone_id": {"type": "string"}},
                 "required": ["drone_id"],
             }),
        Tool(name="get_world_state",
             description="Full JSON snapshot of all drones and survivors for the dashboard.",
             inputSchema={"type": "object", "properties": {}, "required": []}),

        # Mesh / offline
        Tool(name="broadcast_mesh_message",
             description="Send a message to all active drones via local mesh radio (no internet).",
             inputSchema={
                 "type": "object",
                 "properties": {
                     "sender_id": {"type": "string"},
                     "message":   {"type": "string", "maxLength": 200},
                 },
                 "required": ["sender_id", "message"],
             }),
        Tool(name="attempt_drone_recovery",
             description="Send mesh wake signal to an offline drone. 65% success rate.",
             inputSchema={
                 "type": "object",
                 "properties": {"drone_id": {"type": "string"}},
                 "required": ["drone_id"],
             }),
        Tool(name="get_mesh_log",
             description="Full mesh broadcast history.",
             inputSchema={"type": "object", "properties": {}, "required": []}),
    ])


# ── Dispatch ───────────────────────────────────────────────────────────────────

TOOL_MAP: dict[str, Any] = {
    "discover_drones":            D.discover_drones,
    "get_all_drone_statuses":     D.get_all_drone_statuses,
    "assign_sector":              D.assign_sector,
    "move_to":                    T.move_to,
    "get_grid_map":               T.get_grid_map,
    "thermal_scan":               T.thermal_scan,
    "acoustic_scan":              T.acoustic_scan,
    "get_battery_status":         T.get_battery_status,
    "return_to_charging_station": T.return_to_charging_station,
    "charge_drone":               T.charge_drone,
    "list_supply_depots":         T.list_supply_depots,
    "collect_supplies":           T.collect_supplies,
    "deliver_supplies":           T.deliver_supplies,
    "get_rescue_priority_list":   T.get_rescue_priority_list,
    "mark_survivor_rescued":      T.mark_survivor_rescued,
    "get_drone_status":           T.get_drone_status,
    "get_swarm_summary":          T.get_swarm_summary,
    "get_mission_log":            T.get_mission_log,
    "broadcast_mesh_message":     T.broadcast_mesh_message,
    "attempt_drone_recovery":     T.attempt_drone_recovery,
    "get_mesh_log":               T.get_mesh_log,
    "get_world_state":            T.get_world_state,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    fn = TOOL_MAP.get(name)
    if not fn:
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))],
            isError=True,
        )
    try:
        result = fn(**arguments)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, indent=2))]
        )
    except Exception as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": str(exc)}))],
            isError=True,
        )


# ── Entry point ────────────────────────────────────────────────────────────────

async def _run():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


def main():
    logger.info("RUNNING MCP SERVER")
    asyncio.run(_run())


if __name__ == "__main__":
    main()