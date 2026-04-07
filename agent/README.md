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