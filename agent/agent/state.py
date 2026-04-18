import json
from typing import TypedDict, List, Dict, Any, Optional
from pydantic import BaseModel, Field

class ToolCall(BaseModel):
    """Defines the structure for a tool call, used for structured output from the LLM."""
    name: str = Field(description="The name of the tool to execute")
    parameters: Dict[str, Any] = Field(description="The parameters for the tool")

class AgentOutput(BaseModel):
    """Defines the complete structured output format for the SIREN Commander agent."""
    thought: str = Field(description="Chain-of-thought reasoning")
    tool_call: ToolCall = Field(description="The tool call to make")

class Drone(TypedDict):
    """Represents the live telemetry of an individual drone."""
    id: str
    battery: int
    x: int
    y: int
    status: str
    locked: bool
    payload: Optional[str]   # None when empty, supply name when carrying

class SwarmState(TypedDict):
    """
    The central global memory structure for the LangGraph agent execution.
    It passes through every node, tracking the live world state and the agent's history.
    """
    drones: List[Drone]                         # Live registry of all drones and their stats
    mission_log: List[str]                      # Historical log of executed tools and events
    search_grid: Dict[str, bool]                # Tracks which sectors have been scanned
    active_relays: Dict[str, str]               # See if current drone is having relay drone e.g. {"DRONE_ALPHA": "DRONE_BRAVO"}
    next_action: Optional[ToolCall]             # The raw structured tool command securely waiting to be executed
    mission_prompt: str                         # The dynamic scenario parameter instructions
    detected_survivors: List[Dict[str, Any]]    # Pending survivors: [{id, x, y, condition}, ...]
    rescued_survivors: List[str]                # List of survivor IDs confirmed rescued