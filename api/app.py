"""
FastAPI application — SAR Swarm MCP backend.

The MCP server stays on stdio (subprocess). FastAPI is purely an HTTP
convenience layer for triggering missions, streaming CoT logs via SSE,
and inspecting world state — it does NOT replace the stdio transport.

Run with:
    uvicorn api.app:app --reload --port 8000
or:
    python -m api.app
"""
from __future__ import annotations

import importlib
import pkgutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import scenarios as _scenarios_pkg
from api.routers import missions, world, tools, scenarios as scenarios_router, mesa as mesa_router
from utils.config import INITIAL_FLEET


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eager-load all scenarios modules so discovery is instant on first request
    for info in pkgutil.iter_modules(_scenarios_pkg.__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"scenarios.{info.name}")
    yield


# ── App factory ────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title       = "Swarm MCP API",
        description = (
            "HTTP interface for the First Responder Swarm Intelligence system.\n\n"
            "The MCP server runs as a local **stdio subprocess** — no network "
            "transport is used for tool calls. FastAPI provides mission control, "
            "live SSE log streaming, and world-state inspection."
        ),
        version     = "0.1.0",
        lifespan    = lifespan,
        docs_url    = "/docs",
        redoc_url   = "/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins  = ["*"],
        allow_methods  = ["*"],
        allow_headers  = ["*"],
    )

    app.include_router(missions.router)
    app.include_router(world.router)
    app.include_router(tools.router)
    app.include_router(scenarios_router.router)
    app.include_router(mesa_router.router, prefix="/world")

    @app.get("/health", tags=["health"])
    async def health():
        scenario_names = [
            info.name
            for info in pkgutil.iter_modules(_scenarios_pkg.__path__)
            if not info.name.startswith("_")
        ]
        return {
            "status":      "ok",
            "mcp_server":  "stdio subprocess",
            "scenarios":   scenario_names,
            "drone_count": len(INITIAL_FLEET),
        }

    return app


app = create_app()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    import uvicorn
    from utils.config import API_HOST, API_PORT
    uvicorn.run(
        "api.app:app",
        host       = API_HOST,
        port       = API_PORT,
        reload     = True,
        log_level  = "info",
        access_log = False,
    )


if __name__ == "__main__":
    main()