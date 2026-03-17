# SAR Swarm MCP Backend

**First Responder Swarm Intelligence — Earthquake SAR**  
Edge-deployed · Offline-capable · Model Context Protocol

---

## What this is

A simulated Search and Rescue command system for earthquake response in the ASEAN region. An AI agent (SIREN) orchestrates a fleet of rescue drones over a 10×10 grid disaster zone. The system is designed to operate **fully offline** — no cloud, no cell towers — using the [Model Context Protocol (MCP)](https://modelcontextprotocol.io) so the agent communicates with drone tools via a standardised interface rather than hard-coded function calls.

FastAPI sits in front as a convenient HTTP layer for triggering missions and watching live output. The MCP server itself always runs as a **stdio subprocess** — no port, no network — exactly how it would run on an edge device aboard a real drone.

---

## Architecture

```
You (curl / browser)
        │ HTTP
        ▼
FastAPI  :8000                                         
  ├── POST /mission/run   → start a scenario             
  ├── GET  /mission/{id}/stream  → live CoT via SSE      
  ├── GET  /world/map     → ASCII grid                   
  ├── POST /tools/call    → call any tool directly       
  └── GET  /scenarios/    → list available scenarios     
        │
        │ spawns automatically on POST /mission/run 
        ▼
  CommandAgent              ← Agent API → chain-of-thought reasoning
        │ stdio (subprocess)
        ▼
  MCP Server                ← 21 drone tools over stdin/stdout, fully offline
        │ Python calls
        ▼
  WorldState                ← in-memory simulation (drones, survivors, grid)
```

The only outbound network call is from `CommandAgent` to the AI Agent. Everything else — tool calls, drone simulation, mesh radio — is local.

---

## Project structure

```
mcp-backend/
├── api/                        # FastAPI HTTP layer
│   ├── app.py                  # app factory, lifespan, CORS
│   ├── mission_runner.py       # async task manager + SSE queue
│   ├── models/mission.py       # Pydantic request/response schemas
│   └── routers/
│       ├── missions.py         # POST /mission/run, GET /mission/{id}/stream
│       ├── world.py            # GET /world/map, /drones, /survivors
│       ├── tools.py            # POST /tools/call  (direct tool testing)
│       └── scenarios.py        # GET /scenarios/  (dynamic discovery)
│
├── agent/
│   ├── command_agent.py        # SIREN orchestrator — agentic loop
│   ├── mission_planner.py      # sector decomposition + scan waypoints
│   └── reasoning_log.py        # structured chain-of-thought logger
│
├── mcp_server/
│   ├── server.py               # MCP server — registers all tools
│   ├── world_state.py          # single source of truth (singleton)
│   ├── drone_simulator.py      # Drone + Survivor state machines
│   ├── drone_registry.py       # fleet discovery (discover_drones)
│   ├── mesh_radio.py           # offline mesh broadcast + recovery
│   └── tools/
│       ├── battery_tools.py    # get_battery_status, return_to_charge, charge
│       ├── movement_tools.py   # move_to, get_grid_map
│       ├── scan_tools.py       # thermal_scan, acoustic_scan
│       ├── supply_tools.py     # collect_supplies, deliver_supplies
│       ├── rescue_tools.py     # get_rescue_priority_list, mark_rescued
│       ├── status_tools.py     # get_drone_status, get_swarm_summary
│       └── mesh_tools.py       # broadcast_mesh_message, attempt_recovery
│
├── scenarios/                  # one file per mission scenario
│   ├── supply_run.py
│   ├── survivor_detect.py
│   ├── battery_crisis.py
│   ├── offline_recovery.py
│   ├── rescue_priority.py
│   └── swarm_status.py
│
├── tests/
│   ├── test_drone_tools.py     # unit tests for every MCP tool
│   └── test_scenarios.py       # integration tests + end-to-end chains
│
├── utils/
│   └── config.py               # all settings from .env
│
├── pyproject.toml
└── .env.example
```

### How to run it?
There are **two ways to run this project** depending on what you are testing:

| Goal | Command | Use when                                                                     |
|------|---------|------------------------------------------------------------------------------|
| Test MCP tools directly | `uv run sar-server` | Connecting AI IDE, `mcp dev`, or any MCP client to the drone tools           |
| Run the full system | `uv run sar-api` | Triggering missions via HTTP, streaming agent output, inspecting world state |

## Scenarios

Each scenario is a self-contained `MISSION_PROMPT` string.

| Scenario | Tests |
|----------|-------|
| `supply_run` | Route drones to depots, collect correct supplies, deliver in priority order |
| `survivor_detect` | Grid sweep with thermal + acoustic scans, build priority list |
| `battery_crisis` | Multi-drone low-battery emergency, safe recall + reassignment |
| `offline_recovery` | Drone goes offline mid-mission, mesh wake attempt, sector redistribution |
| `rescue_priority` | 5 survivors, 3 drones, constrained resources — critical first |
| `swarm_status` | Fleet health dashboard — all drones, all survivors, mesh log |

---

## Setup

### Prerequisites

- Python 3.14
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Install

```bash
git clone <your-repo>
cd mcp-backend

uv sync                  # installs all dependencies into .venv
uv sync --dev            # also installs pytest, httpx
```
---

## Running
 
### Option 1 — MCP server only (`sar-server`)
 
Use this when you want to test or inspect the drone tools directly, without the agent or FastAPI.
 
```bash
uv run sar-server
```
 
This starts the MCP stdio server. Connect to it with:
 
- **`mcp dev`** — interactive MCP inspector in the terminal
  ```bash
  uv run mcp dev mcp_server/server.py
  ```
  - **AI IDE** — add to your `claude_desktop_config.json`/ `mcp.json`:
    ```json
    {
      "mcpServers": {
        "sar-swarm": {
          "command": "uv",
          "args": [
            "--directory",
            "C:\\path\\to\\mcp-backend",
            "run",
            "sar-server"
          ]
        }
      }
    }
    ```
- **Any MCP client** — the server speaks stdio, so any compliant client works
 
The 21 tools are immediately available for direct calls.

---
 
### Option 2 — FastAPI (`sar-api`)
 
Use this when you want to run full missions with the AI agent, stream live output, or inspect world state over HTTP.
 
```bash
uv run sar-api
```
 
Open Swagger UI → http://localhost:8000/docs 

Open Redoc UI → http://localhost:8000/redoc
 
The MCP server is **not** started separately — `CommandAgent` spawns it automatically as a subprocess when you trigger a mission.

---
 
## API usage
 
### Start a mission
 
```bash
curl -X POST http://localhost:8000/mission/run \
     -H "Content-Type: application/json" \
     -d '{"scenario": "supply_run"}'
```
 
```json
{
  "mission_id": "a1b2c3d4",
  "scenario": "supply_run",
  "status": "running",
  "stream_url": "/mission/a1b2c3d4/stream"
}
```
 
### Stream live chain-of-thought (SSE)
 
```bash
curl -N http://localhost:8000/mission/a1b2c3d4/stream
```
 
Each SSE event is one of:
 
| Event | Payload |
|-------|---------|
| `log` | Raw agent text (reasoning, announcements) |
| `step` | Structured CoT entry — phase, tool name, reasoning, result summary |
| `complete` | Mission finished — full debrief included |
| `error` | Something went wrong |
| `ping` | Keepalive every 30s when idle |
 
### Poll status
 
```bash
curl http://localhost:8000/mission/a1b2c3d4/status
```
 
### Inspect world state
 
```bash
curl http://localhost:8000/world/map          # ASCII grid
curl http://localhost:8000/world/drones       # full fleet status
curl http://localhost:8000/world/survivors    # survivor states + priority list
curl http://localhost:8000/world/mesh-log     # offline mesh broadcast history
```
 
### Call a tool directly (no agent, no mission)
 
```bash
curl -X POST http://localhost:8000/tools/call \
     -H "Content-Type: application/json" \
     -d '{"tool_name": "discover_drones", "arguments": {}}'
 
curl -X POST http://localhost:8000/tools/call \
     -H "Content-Type: application/json" \
     -d '{"tool_name": "thermal_scan", "arguments": {"drone_id": "DRONE_ALPHA"}}'
```
 
### List available scenarios
 
```bash
curl http://localhost:8000/scenarios/
```
 
Scenarios are discovered dynamically from the `scenarios/` package — adding a new `.py` file with a `MISSION_PROMPT` variable makes it appear here automatically.
 
### Reset simulation between runs

```bash
curl -X POST http://localhost:8000/world/reset
```
---

## Tests

```bash
uv run pytest                              # all tests
uv run pytest tests/ -v                    # verbose output
uv run pytest tests/test_drone_tools.py    # MCP tool unit tests only
uv run pytest tests/test_scenarios.py      # scenario + integration tests
```

---
 
## Scenarios
 
| Scenario | What the agent does |
|----------|---------------------|
| `supply_run` | Routes drones to depots, collects correct supplies, delivers in priority order |
| `survivor_detect` | Sweeps the grid with thermal + acoustic scans, builds rescue priority list |
| `battery_crisis` | Handles multiple low-battery drones mid-mission — safe recall and reassignment |
| `offline_recovery` | Drone goes offline mid-mission, attempts mesh wake, redistributes sectors |
| `rescue_priority` | 5 survivors, 3 drones, constrained resources — critical cases served first |
| `swarm_status` | Full fleet health report — all drones, all survivors, mesh log, recommendations |
 
To add a new scenario, create `scenarios/my_scenario.py` with a `MISSION_PROMPT` string.

---

## MCP tools reference

| Tool | Category | Scenario |
|------|----------|----------|
| `discover_drones` | Discovery | Always first — never hard-code IDs |
| `get_all_drone_statuses` | Discovery | Fleet snapshot |
| `assign_sector` | Discovery | Coverage tracking |
| `move_to` | Movement | All |
| `get_grid_map` | Movement | Visualisation |
| `thermal_scan` | Scanning | Survivor detection |
| `acoustic_scan` | Scanning | Through-rubble detection |
| `get_battery_status` | Battery | All |
| `return_to_charging_station` | Battery | Battery crisis |
| `charge_drone` | Battery | Battery crisis |
| `list_supply_depots` | Supply | Supplies collection |
| `collect_supplies` | Supply | Supplies collection |
| `deliver_supplies` | Supply | Supplies collection |
| `get_rescue_priority_list` | Rescue | Prioritisation |
| `mark_survivor_rescued` | Rescue | Prioritisation |
| `get_drone_status` | Status | Swarm status view |
| `get_swarm_summary` | Status | Swarm status view |
| `get_mission_log` | Status | Debrief |
| `broadcast_mesh_message` | Mesh | Offline / recovery |
| `attempt_drone_recovery` | Mesh | Offline / recovery |
| `get_mesh_log` | Mesh | Offline / recovery |

---

## Grid world

```
10×10 cells. Origin (0,0) = bottom-left corner.

Charging stations : CS1=(0,0)   CS2=(9,0)
Supply depots     : D1=(0,0)  → medical_kit, water, food
                    D2=(9,9)  → rope, tarp, radio
Starting positions: ALPHA=(0,0)  BRAVO=(9,0)  CHARLIE=(0,9)
                    DELTA=(9,9)  ECHO=(5,0) — starts OFFLINE

Battery cost      : distance × 3% per grid cell
Safety reserve    : always keep ≥ 15% before committing a move
Scan radius       : thermal = 1.5 cells,  acoustic = 1.0 cell
```

Sectors used by `MissionPlanner`:

```
 NW (x 0-4, y 5-9) │ NE (x 5-9, y 5-9)
 ──────────────────┼──────────────────
      CT (x 3-6, y 3-6)
 ──────────────────┼──────────────────
 SW (x 0-4, y 0-4) │ SE (x 5-9, y 0-4)
```