# For testing purposes

import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

from agent.state import SwarmState
from agent.graph import create_graph

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

load_dotenv()

async def stream_mission(scenario_name: str):
    """Async generator that yields log lines from the mission execution."""
    app = create_graph()
    
    import sys
    import os
    
    # Define exact path to mcp-backend directory
    # If running from mcp-backend/agent, it's just '..'
    mcp_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if mcp_backend_dir not in sys.path:
        sys.path.append(mcp_backend_dir)
        
    # Load scenario configuration
    os.environ["SCENARIO"] = scenario_name
    from utils.config import INITIAL_FLEET, MISSION_PROMPT
    
    agent_drones = []
    for d in INITIAL_FLEET:
        status = "offline" if d.get("offline") else "idle"
        agent_drones.append({
            "id": d["id"], "battery": d["battery"], "x": d["x"], "y": d["y"], "status": status
        })
    
    initial_state = SwarmState(
        drones=agent_drones,
        mission_log=[],
        search_grid={"sector_1": True, "sector_2": True, "sector_3": True, "sector_4": False, "sector_5": True},
        relay_active=False,
        mission_prompt=MISSION_PROMPT
    )
    
    yield f"[SYSTEM] Starting AETHER Swarm Commander (Scenario: {scenario_name})..."
    
    # Boots up MCP server
    env = os.environ.copy()
    env["PYTHONPATH"] = mcp_backend_dir
    
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        env=env
    )
    
    # Initialize pipe between agent and mcp
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # Mount initialized session into the agent's MCP client
            from agent.mcp.client import mcp_client
            mcp_client.set_session(session)
            
            try:
                log_index = 0
                async for event in app.astream(initial_state, {"recursion_limit": 40}):
                    for node_name, state_update in event.items():
                        if "mission_log" in state_update:
                            new_logs = state_update["mission_log"][log_index:]
                            for log in new_logs:
                                yield f"[{node_name}] {log}"
                            log_index = len(state_update["mission_log"])
                                        
                yield "[SYSTEM] Mission accomplished."
                
            except Exception as e:
                yield f"[ERROR] Graph execution failed: {str(e)}"

async def run_mission():
    """CLI wrapper for the stream_mission generator."""
    scenario = os.environ.get("SCENARIO", "default")
    async for log in stream_mission(scenario):
        print(log)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run AETHER Swarm Commander")
    parser.add_argument("--scenario", type=str, default="default", help="Scenario name to run")
    args = parser.parse_args()
    os.environ["SCENARIO"] = args.scenario
    
    asyncio.run(run_mission())
