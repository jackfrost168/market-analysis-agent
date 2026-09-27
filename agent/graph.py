from typing import Any, Dict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .nodes import AgentNodes
from .state import AgentState


GRAPH_NODES = [
    "understand_request",
    "plan_research",
    "collect_evidence",
    "normalize_and_evidence_gate",
    "targeted_retrieval",
    "financial_quantitative_analysis",
    "generate_thesis_graph",
    "verify_and_calibrate",
    "build_report",
]

GRAPH_EDGES = [
    ["START", "understand_request"],
    ["understand_request", "plan_research"],
    ["plan_research", "collect_evidence"],
    ["collect_evidence", "normalize_and_evidence_gate"],
    ["normalize_and_evidence_gate", "targeted_retrieval", "conditional: insufficient"],
    ["normalize_and_evidence_gate", "financial_quantitative_analysis", "conditional: sufficient or retry exhausted"],
    ["targeted_retrieval", "normalize_and_evidence_gate"],
    ["financial_quantitative_analysis", "generate_thesis_graph"],
    ["generate_thesis_graph", "verify_and_calibrate"],
    ["verify_and_calibrate", "targeted_retrieval", "conditional: verification gap"],
    ["verify_and_calibrate", "build_report", "conditional: verified or retry exhausted"],
    ["build_report", "END"],
]


def _route_after_gate(state: AgentState) -> str:
    return state.get("next_step") or "financial_quantitative_analysis"


def _route_after_verification(state: AgentState) -> str:
    return state.get("next_step") or "build_report"


def next_node_for_state(state: AgentState) -> str:
    """Streamed values describe a finished node; display the next scheduled node."""
    trace = state.get("node_trace") or []
    if not trace:
        return "understand_request"
    completed = trace[-1].get("node")
    if completed == "normalize_and_evidence_gate":
        return _route_after_gate(state)
    if completed == "verify_and_calibrate":
        return _route_after_verification(state)
    return next((edge[1] for edge in GRAPH_EDGES if len(edge) == 2 and edge[0] == completed), "END")


def build_graph(nodes: AgentNodes):
    builder = StateGraph(AgentState)
    builder.add_node("understand_request", nodes.understand_request)
    builder.add_node("plan_research", nodes.plan_research)
    builder.add_node("collect_evidence", nodes.collect_evidence)
    builder.add_node("normalize_and_evidence_gate", nodes.normalize_and_gate)
    builder.add_node("targeted_retrieval", nodes.targeted_retrieval)
    builder.add_node(
        "financial_quantitative_analysis", nodes.financial_quantitative_analysis
    )
    builder.add_node("generate_thesis_graph", nodes.generate_thesis_graph)
    builder.add_node("verify_and_calibrate", nodes.verify_and_calibrate)
    builder.add_node("build_report", nodes.build_report)

    builder.add_edge(START, "understand_request")
    builder.add_edge("understand_request", "plan_research")
    builder.add_edge("plan_research", "collect_evidence")
    builder.add_edge("collect_evidence", "normalize_and_evidence_gate")
    builder.add_conditional_edges(
        "normalize_and_evidence_gate",
        _route_after_gate,
        {
            "targeted_retrieval": "targeted_retrieval",
            "financial_quantitative_analysis": "financial_quantitative_analysis",
        },
    )
    builder.add_edge("targeted_retrieval", "normalize_and_evidence_gate")
    builder.add_edge("financial_quantitative_analysis", "generate_thesis_graph")
    builder.add_edge("generate_thesis_graph", "verify_and_calibrate")
    builder.add_conditional_edges(
        "verify_and_calibrate",
        _route_after_verification,
        {
            "targeted_retrieval": "targeted_retrieval",
            "build_report": "build_report",
        },
    )
    builder.add_edge("build_report", END)
    return builder.compile(checkpointer=InMemorySaver())


def graph_spec() -> Dict[str, Any]:
    return {
        "framework": "LangGraph",
        "node_count": len(GRAPH_NODES),
        "edge_count": len(GRAPH_EDGES),
        "nodes": GRAPH_NODES,
        "edges": GRAPH_EDGES,
        "loops": [
            "targeted_retrieval -> normalize_and_evidence_gate",
            "verify_and_calibrate -> targeted_retrieval when support is missing",
        ],
    }
