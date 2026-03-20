# AETHER: Technical Specification & Architecture

## 1. System Architecture
- **Orchestrator:** LangGraph (State Machine).
- **Inference:** Local LLM via Ollama qwen3.5:9b.
- **Interface:** Model Context Protocol (MCP) for drone hardware simulation.
- **Validation:** Pydantic v2 for structured JSON enforcement.

## 2. State Management (SwarmState)
The agent must maintain a centralized state using a TypedDict:
- `drones`: List[{id: str, battery: int, x: int, y: int, status: str}]
- `mission_log`: List[str] (Stores the Chain-of-Thought reasoning).
- `search_grid`: Dict (Tracks which sectors are scanned/logged).
- `relay_active`: bool (True if a communications relay drone is positioned).

## 3. Tool Definition (MCP Tools)
The agent interacts with the fleet via these standardized functions:
- `get_fleet_telemetry()`: Returns battery and GPS for all 5 drones.
- `move_drone(drone_id, x, y)`: Commands a drone to a target coordinate.
- `execute_thermal_scan(drone_id)`: Triggers the human detection AI.
- `log_to_sui(sector_id, drone_id)`: Signs a "Proof of Search" transaction on local ledger.
- `stabilize_hover(drone_id)`: Emergency hover for seismic jitter/noise.

## 4. Operational Logic (Hard Rules)
- **Battery:** If `battery < 20`, the agent MUST immediately trigger `move_drone(base_x, base_y)`.
- **Relay:** If distance to target > 1000m, the agent MUST deploy a relay drone at midpoint before the search drone proceeds.
- **Priority:** Cross-reference sector ID with `priority_map.json`. Hospitals and Schools must be scanned before generic zones.
- **Self-Healing:** If an MCP tool returns a "Timeout" or "Jitter Error," the agent must transition to a 'Recovery' node to retry or hover.