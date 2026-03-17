"""
/tools routes — call individual MCP tools directly over HTTP.
Handy for testing tool logic without running the full agent.

  GET  /tools/             — list all available tools
  POST /tools/call         — invoke a specific tool with arguments
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from api.models.mission import ToolCallRequest, ToolCallResponse
import mcp_server.tools as T

router = APIRouter(prefix="/tools", tags=["tools"])

# Build the dispatch table from the tools package — no hardcoded names
_TOOL_MAP = {
    name: getattr(T, name)
    for name in dir(T)
    if callable(getattr(T, name)) and not name.startswith("_")
}


@router.get("/")
async def list_tools():
    """List all available MCP tool names."""
    return {"tools": sorted(_TOOL_MAP.keys())}


@router.post("/call", response_model=ToolCallResponse)
async def call_tool(req: ToolCallRequest):
    """
    Directly invoke an MCP tool by name with the given arguments.
    Useful for unit-testing tools or building custom workflows.
    """
    fn = _TOOL_MAP.get(req.tool_name)
    if not fn:
        raise HTTPException(
            status_code=404,
            detail=f"Tool {req.tool_name!r} not found. Available: {sorted(_TOOL_MAP.keys())}",
        )
    try:
        result = fn(**req.arguments)
        return ToolCallResponse(
            tool_name=req.tool_name,
            arguments=req.arguments,
            result=result,
        )
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid arguments: {exc}")
    except Exception as exc:
        return ToolCallResponse(
            tool_name=req.tool_name,
            arguments=req.arguments,
            result={},
            error=str(exc),
        )