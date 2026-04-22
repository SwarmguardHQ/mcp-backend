# Fetch tools from mcp_server/server.py

from typing import Optional, Callable, Awaitable
from mcp import ClientSession

class SirenMCPClient:
    """Dynamic Async MCP Client connected to mcp-backend."""
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.base_x, self.base_y = 0, 0

        # ── Step-level world sync hook ─────────────────────────────────────────
        # Set by mission_runner before starting the graph.
        # Nodes call `await mcp_client.step_sync()` after each key MCP tool call
        # (move_to, thermal_scan, collect_supplies, deliver_supplies) so the
        # frontend sees each individual action as it completes, not as a batch.
        self._on_step_complete: Optional[Callable[[], Awaitable[None]]] = None

        # ── Human-in-the-loop override ─────────────────────────────────────────
        # Set by the API endpoint when an operator submits a real-time insight.
        # Consumed (once) by safety_governor_node on the next cycle, injected
        # into SwarmState.human_override, then cleared so it does not repeat.
        self.pending_override: Optional[str] = None

    def set_override(self, text: str) -> None:
        """Called by the API to inject an operator insight into the next swarm cycle."""
        self.pending_override = text.strip()

    def consume_override(self) -> Optional[str]:
        """Called by safety_governor_node — pops and returns the pending override (one-shot)."""
        text = self.pending_override
        self.pending_override = None
        return text

    def set_session(self, session: ClientSession):
        self.session = session

    async def step_sync(self) -> None:
        """
        Called by nodes after each key MCP action.
        Triggers an immediate get_world_state → _sync_local_world → SSE broadcast
        so the frontend reflects the action in real time.
        No-op if the callback has not been set (e.g. during unit tests).
        """
        if self._on_step_complete:
            await self._on_step_complete()

    async def get_available_tools(self) -> str:
        """Fetches all tools from the MCP backend and maps their JSON Schema."""
        if not self.session:
            return "Error: No session"
            
        res = await self.session.list_tools()
        
        tools_str = ""
        for tool in res.tools:
            tools_str += f"- {tool.name}: {tool.description}\n  Schema: {tool.inputSchema}\n"
        return tools_str

mcp_client = SirenMCPClient()