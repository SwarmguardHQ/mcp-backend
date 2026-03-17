"""
/scenarios routes — discover available scenarios at runtime.
No scenarios names are hardcoded — they are read from the scenarios package.

  GET /scenarios/           — list all scenarios with their prompts
  GET /scenarios/{name}     — fetch a specific scenarios's prompt
"""
from __future__ import annotations
import importlib
import pkgutil
from fastapi import APIRouter, HTTPException
import scenarios as _scenarios_pkg

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _discover() -> dict[str, str]:
    """Return {scenario_name: MISSION_PROMPT} for all discovered modules."""
    result = {}
    for info in pkgutil.iter_modules(_scenarios_pkg.__path__):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"scenarios.{info.name}")
            result[info.name] = getattr(mod, "MISSION_PROMPT", "(no prompt)")
        except Exception:
            pass
    return result


@router.get("/")
async def list_scenarios():
    """List all available scenarios names and their mission prompts."""
    scenarios = _discover()
    return {
        "scenarios": [
            {"name": name, "prompt_preview": prompt[:200] + "…" if len(prompt) > 200 else prompt}
            for name, prompt in scenarios.items()
        ],
        "total": len(scenarios),
    }


@router.get("/{name}")
async def get_scenario(name: str):
    """Return the full mission prompt for a specific scenarios."""
    scenarios = _discover()
    if name not in scenarios:
        raise HTTPException(
            status_code=404,
            detail=f"Scenario {name!r} not found. Available: {list(scenarios.keys())}",
        )
    return {"name": name, "prompt": scenarios[name]}