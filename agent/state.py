import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    conversation_id: str
    user_query: str
    asset_input: str
    requested_horizon: str
    requested_as_of: str
    target_price: Optional[float]
    target_condition: str
    model: str
    prediction_provider: str
    temperature: float
    max_retries: int
    user_notes: List[str]
    user_headlines: List[str]

    task: Dict[str, Any]
    research_plan: Dict[str, Any]
    raw_evidence: List[Dict[str, Any]]
    normalized_evidence: List[Dict[str, Any]]
    evidence_groups: List[Dict[str, Any]]
    source_status: Dict[str, Dict[str, Any]]
    evidence_gate: Dict[str, Any]
    retrieval_attempts: int
    targeted_queries: List[Dict[str, Any]]
    quantitative_analysis: Dict[str, Any]
    thesis_graph: Dict[str, Any]
    verification: Dict[str, Any]
    report: Dict[str, Any]
    run_metrics: Dict[str, Any]
    next_step: str

    node_trace: Annotated[List[Dict[str, Any]], operator.add]
    decision_audit: Annotated[List[Dict[str, Any]], operator.add]
    intermediate_results: Annotated[List[Dict[str, Any]], operator.add]
    tool_calls: Annotated[List[Dict[str, Any]], operator.add]
    errors: Annotated[List[str], operator.add]
    llm_calls: Annotated[List[Dict[str, Any]], operator.add]
