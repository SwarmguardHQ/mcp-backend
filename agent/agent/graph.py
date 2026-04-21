"""
SwarmGuard LangGraph — True Swarm Intelligence Topology
========================================================

Flow:
  START
    └── safety_governor_node
          ├── [complete] → END
          └── [normal]   → strategist_node
                                ├── [search] → broadcast_tasks → Send[drone_bidding_node] → resolve_bids_node
                                │                                                               └── dispatch_winners → Send[drone_agent_node] → join_node → safety_governor_node (loop)
                                ├── [rescue  + directive]  → rescue_execution_node
                                                                    └── safety_governor_node  (loop)

Key LangGraph features used:
  - Annotated reducers on SwarmState fields (search_grid, drones, mission_log, active_relays, bids, signal_map)
  - langgraph.types.Send for true parallel fan-out (drone_bidding_node & drone_agent_node)
  - Conditional edges for phase routing and emergency branching
"""

from langgraph.graph import StateGraph, START, END

from .state import SwarmState
from .nodes import (
    safety_governor_node,
    route_after_governor,
    strategist_node,
    broadcast_tasks,
    drone_bidding_node,
    resolve_bids_node,
    dispatch_winners,
    drone_agent_node,
    join_node,
    rescue_execution_node,
    recovery_node,
)


def create_graph():
    workflow = StateGraph(SwarmState)

    # ── Register nodes ────────────────────────────────────────────────────────
    workflow.add_node("safety_governor_node",  safety_governor_node)
    workflow.add_node("strategist_node",       strategist_node)
    workflow.add_node("drone_bidding_node",    drone_bidding_node)
    workflow.add_node("resolve_bids_node",     resolve_bids_node)
    workflow.add_node("drone_agent_node",      drone_agent_node)
    workflow.add_node("join_node",             join_node)
    workflow.add_node("rescue_execution_node", rescue_execution_node)
    workflow.add_node("recovery_node",         recovery_node)

    # ── Entry point ───────────────────────────────────────────────────────────
    workflow.add_edge(START, "safety_governor_node")

    # ── Governor routing: mission complete → END, else → strategist ───────────
    workflow.add_conditional_edges(
        "safety_governor_node",
        route_after_governor,
        {
            "strategist_node": "strategist_node",
            "recovery_node":   "recovery_node",
            END:               END,
        },
    )

    # ── Strategist routing: fan-out bidding (search) or rescue execution ─────
    # broadcast_tasks returns either:
    #   • A list of Send("drone_bidding_node", ...) objects — parallel fan-out
    #   • "rescue_execution_node"  — string key
    #   • "join_node"              — string key
    workflow.add_conditional_edges(
        "strategist_node",
        broadcast_tasks,
        {
            "drone_bidding_node":    "drone_bidding_node",
            "rescue_execution_node": "rescue_execution_node",
            "join_node":             "join_node",
        },
    )

    # ── Bidding phase reconverges to resolve ──────────────────────────────────
    workflow.add_edge("drone_bidding_node", "resolve_bids_node")

    # ── Resolve bids → dispatch winners to agents ─────────────────────────────
    workflow.add_conditional_edges(
        "resolve_bids_node",
        dispatch_winners,
        {
            "join_node":        "join_node",
            "drone_agent_node": "drone_agent_node",
        },
    )

    # ── Drone agent → join (each parallel branch reconverges here) ────────────
    workflow.add_edge("drone_agent_node", "join_node")

    # ── Join → Governor (always loop back for the next cycle) ──────────────────
    workflow.add_edge("join_node", "safety_governor_node")

    # ── Rescue execution → governor (re-evaluate phase after each rescue) ──────
    workflow.add_edge("rescue_execution_node", "safety_governor_node")

    # ── Recovery → governor ───────────────────────────────────────────────────
    workflow.add_edge("recovery_node", "safety_governor_node")

    return workflow.compile()