from typing import Optional
from mcp import ClientSession

class AetherMCPClient:
    """Dynamic Async MCP Client connected to mcp-backend."""
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.base_x, self.base_y = 0, 0
        
    def set_session(self, session: ClientSession):
        self.session = session
        
    async def get_available_tools(self) -> str:
        """Fetches all tools from the MCP backend and maps their JSON Schema."""
        if not self.session: 
            return "Error: No session"
            
        res = await self.session.list_tools()
        
        tools_str = ""
        for tool in res.tools:
            tools_str += f"- {tool.name}: {tool.description}\n  Schema: {tool.inputSchema}\n"
        return tools_str

mcp_client = AetherMCPClient()
