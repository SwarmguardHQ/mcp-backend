# The Pheromone Protocol
## Swarm Intelligence in Drone Rescue Operations

### 1. The Hook: Bio-Inspired Intelligence
Nature has already solved the problem of large-scale coordination. Ants don't wait for a general's orders; they follow **Stigmergy**—environmental cues called pheromones. Today, I’m showcasing a Swarm Intelligence architecture for Drone Rescue that applies this biological efficiency to life-saving missions.

### 2. The Mechanics: Competitive Bidding & Decentralized Tasking
In our system, the Commander Agent doesn't micromanage. Instead, it 'scents' the environment with digital pheromones. Each drone acts as an autonomous decentralized bidder. It will performs a **Pre-flight Simulation** based on its current GPS, battery life, and distance, the drone calculates its 'cost' and submits a bid for the task. The drone will always prioritize on bidding the highest pheromones, break ties by lowest cost (if there is a tie in pheromones, the drone will submit bid for the sector that has the lowest cost).

Our **resolve_bids_node** then evaluates these submissions in real-time, rewarding the sector to the drone with the 'highest' bid—which, in our logistics, is the drone with the lowest operational cost (shortest route between: **Current Location → Supply Depot → Survivor → Charging Station**). Once rewarded, the drone locks and claims the task in our **Global Search Grid**.

Furthermore, if a drone is unable to submit a viable bid for any available task, it triggers an automatic **Readiness Recovery protocol**. We treat the inability to bid as a clear signal of insufficient battery; instead of idling and wasting remaining power, the drone is immediately redirected to a charging station to restore its mission capacity.

This competitive auction ensures that the most capable and efficient drone is always the one deployed. If a drone’s battery drops mid-mission, it releases the claim, allowing the `resolve_bids_node` to instantly re-auction the task to the next best candidate. We aren't just flying drones; we are managing a high-stakes logistics network where every second saved is a life potentially found.

### 3. The Backbone: Active Relay Meshing
Connectivity is the heartbeat of Search and Rescue. We’ve implemented a **Dynamic Relay Mechanism**. While the lead drone pushes into deep 'dead zones,' it deploys relay drones to maintain a continuous mesh network.

But these relays aren't just 'hanging there.' They act as stationary sentries, running continuous **thermal scans**. If a relay detects a survivor outside the defined sector, it feeds back to the Strategist. The Commander then updates the pheromone map, shifting the swarm’s priority to that new area. This is a self-evolving search pattern.

### 4. The Leash: Relocation & Auto-Release
Efficiency is key. The relay drones are tethered to the main drone's movement. As the lead drone advances, it signals the relay to relocate, ensuring the signal chain never breaks. 

To maximize our fleet's utility, we've implemented a **Midpoint Shared Relay** logic. Before a new relay is deployed, the system checks for any existing node already positioned at the required midpoint. If a match is found, that relay is shared between multiple drones. This 'shared infrastructure' prevents unnecessary deployments and keeps more drones in the active search pool.

Once the mission is complete and the main drone returns within a **10-grid radius**, we trigger an **Auto-Release**. The relay drone is unlocked, transforms back into an active agent, and is immediately available to claim new tasks.

### 5. The Hierarchy: Human-in-the-Loop
While the swarm is autonomous, it isn't 'uncontrolled.' We’ve built a **Human Override Mechanism** as the highest priority. If a ground team reports a camping event or a specific sighting at coordinates (1,15), the Commander Agent instantly reshapes the pheromone intensity. This overrides the swarm’s current local logic to focus available resources on human-verified intel.

### 6. The Fail-Safe: Deadlock Breaking & Recovery
But what happens when the mission is at its limit? We’ve accounted for the **Deadlock Scenario**. If the Commander releases a pheromone, but the entire swarm is at low battery, we hit a 'viability gap' where no drone can safely claim the task. 

Instead of the mission stalling in an endless loop, our **Deadlock Breaker** kicks in. If a high-intensity task remains unclaimed, the drones trigger a **Collective Recovery State**. They prioritize self-preservation by autonomously navigating to the nearest charging station. By forcing this strategic 'Pit Stop,' the swarm ensures it recovers the aggregate energy needed to successfully claim and complete the task in the next cycle.


# Local Development Setup

To run the SAR Swarm backend locally without using `uv`, you can manually create and use a standard Python virtual environment.

Make sure you are in the `mcp-backend` folder in your terminal, then run the following commands:

**1. Create the virtual environment (only needed once):**
```bash
python -m venv .venv
```

**2. Activate the virtual environment:**
```bash
.\.venv\Scripts\Activate
```

**3. Install the dependencies:**
First, install the specific agent requirements, then install the main backend project in editable mode so Python can find your local modules:
```bash
cd agent
pip install -r requirements.txt
cd ..
pip install -e .
```

**4. Start the FastAPI backend:**
```bash
python -m api.app
```

---

## How to Add New Agent Tools

Because the agent is built on the **Model Context Protocol (MCP)**, it automatically discovers and learns how to use new tools without requiring any changes to the agent code itself.

The agent queries the MCP server (`server.py`) for available tools and dynamically injects their JSON schemas into its context. To add a new capability to the agent, you only need to modify the server:

1. **Write the Functionality:** Create a standard Python function implementing the tool logic (e.g., in `mcp_server/tools.py` or `mcp_server/drone_registry.py`).
2. **Register the Schema:** Add a new `Tool(...)` entry inside the return list of the `@server.list_tools()` function in `mcp_server/server.py`. Ensure the description is clear and specifies the expected parameters in the `inputSchema`.
3. **Update the Tool Map:** Map the tool name from your schema to the actual Python function by adding an entry to the `TOOL_MAP` dictionary near the bottom of `mcp_server/server.py`.

The next time the agent runs, it will discover the new tool, seamlessly learn how to use it, and start executing it!