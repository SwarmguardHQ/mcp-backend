"""
/mission routes
  POST /mission/run          — start a scenarios mission
  GET  /mission/{id}/status  — poll status + step count
  GET  /mission/{id}/stream  — SSE live chain-of-thought stream
  GET  /mission/             — list all missions
"""
from __future__ import annotations
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from api.models.mission import MissionRequest, MissionStarted, MissionStatus
from api.mission_runner import runner

router = APIRouter(prefix="/mission", tags=["mission"])


@router.post("/run", response_model=MissionStarted, status_code=202)
async def run_mission(req: MissionRequest):
    """
    Start a scenarios mission asynchronously.
    Returns immediately with a mission_id — use /stream or /status to follow along.
    """
    state = runner.start(scenario=req.scenarios, custom_prompt=req.custom_prompt)
    return MissionStarted(
        mission_id = state.mission_id,
        scenario   = state.scenario,
        stream_url = f"/mission/{state.mission_id}/stream",
    )


@router.get("/{mission_id}/status", response_model=MissionStatus)
async def get_status(mission_id: str):
    """Poll the current status of a mission."""
    state = runner.get(mission_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Mission {mission_id!r} not found")

    steps = state.step_count

    # Pull swarm summary if mission is complete
    summary = None
    if state.status == "complete":
        try:
            from mcp_server.tools.status_tools import get_swarm_summary
            summary = get_swarm_summary()
        except Exception:
            pass

    return MissionStatus(
        mission_id       = state.mission_id,
        scenario         = state.scenario,
        status           = state.status,
        steps_logged     = steps,
        mission_complete = summary.get("mission_complete", False) if summary else False,
        summary          = summary,
    )


@router.get("/{mission_id}/stream")
async def stream_mission(mission_id: str):
    """
    Server-Sent Events stream — delivers live chain-of-thought log entries
    as the agent makes tool calls.  Connect with:
        curl -N http://localhost:8000/mission/{id}/stream
    """
    state = runner.get(mission_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Mission {mission_id!r} not found")

    async def _generate():
        # 1. Immediate Replay of existing history
        has_completed = False
        for past_event in state.history:
            yield {"event": past_event["type"], "data": json.dumps(past_event)}
            if past_event["type"] in ("complete", "error"):
                has_completed = True

        if has_completed:
            return

        # 2. Subscribe to new updates
        subscriber_queue = asyncio.Queue()
        state.subscribers.append(subscriber_queue)
        
        try:
            while True:
                try:
                    # Wait for next live event
                    event = await asyncio.wait_for(subscriber_queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    if state.status in ("complete", "failed"):
                        break
                    continue

                yield {"event": event["type"], "data": json.dumps(event)}

                if event["type"] in ("complete", "error"):
                    break
        finally:
            # Cleanup subscriber on disconnect
            if subscriber_queue in state.subscribers:
                state.subscribers.remove(subscriber_queue)

    return EventSourceResponse(_generate(), headers={"Cache-Control": "no-cache, no-transform"})


@router.get("/", summary="List all missions")
async def list_missions():
    return {"missions": runner.list_all()}