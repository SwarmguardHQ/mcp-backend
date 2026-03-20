import json
from typing import TypedDict, List, Dict, Any, Optional
from pydantic import BaseModel, Field

class Drone(TypedDict):
    id: str
    battery: int
    x: int
    y: int
    status: str

class SwarmState(TypedDict):
    drones: List[Drone]
    mission_log: List[str]
    search_grid: Dict[str, bool]
    relay_active: bool
    mission_prompt: str

class ToolCall(BaseModel):
    name: str = Field(description="The name of the tool to execute")
    parameters: Dict[str, Any] = Field(description="The parameters for the tool")

class AgentOutput(BaseModel):
    thought: str = Field(description="Chain-of-thought reasoning")
    tool_call: ToolCall = Field(description="The tool call to make")
