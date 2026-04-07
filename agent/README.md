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