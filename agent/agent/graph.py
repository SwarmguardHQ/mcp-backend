from langgraph.graph import StateGraph, START, END
from .state import SwarmState
from .nodes import thinking_node, commander_node, tool_execution_node, recovery_node, route_after_execution

def create_graph():
    workflow = StateGraph(SwarmState)
    
    workflow.add_node("thinking_node", thinking_node)
    workflow.add_node("commander_node", commander_node)
    workflow.add_node("tool_execution_node", tool_execution_node)
    workflow.add_node("recovery_node", recovery_node)
    
    workflow.add_edge(START, "thinking_node")
    workflow.add_edge("thinking_node", "commander_node")
    workflow.add_edge("commander_node", "tool_execution_node")
    
    workflow.add_conditional_edges(
        "tool_execution_node",
        route_after_execution,
        {
            "recovery_node": "recovery_node",
            "commander_node": "commander_node",
            END: END
        }
    )
    workflow.add_edge("recovery_node", "commander_node")
    
    return workflow.compile()
