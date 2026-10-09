from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


TaskType = Literal[
    "company_analysis",
    "earnings_analysis",
    "move_explanation",
    "technical_analysis",
    "company_outlook",
    "event_impact",
    "general_research",
]

EvidenceSource = Literal[
    "yahoo_finance",
    "google_news",
    "polymarket",
    "vector_db",
    "financial_statements",
    "price_history",
    "user_context",
]

DownsideStatus = Literal[
    "ACTIVE",
    "VULNERABILITY",
    "CONDITIONAL",
    "NOT_SUPPORTED",
]


class RequestUnderstanding(BaseModel):
    asset: str = Field(description="Company name, ticker, or crypto symbol")
    horizon: str = Field(description="Short human-readable analysis horizon")
    as_of: str = Field(description="ISO-8601 as-of time")
    task_type: TaskType
    important_requirements: List[str] = Field(default_factory=list)


class SemanticPlan(BaseModel):
    research_questions: List[str] = Field(default_factory=list, max_length=6)
    optional_sources: List[EvidenceSource] = Field(default_factory=list)


class TargetedQuery(BaseModel):
    source: EvidenceSource
    query: str
    reason: str


class TargetedSearchPlan(BaseModel):
    searches: List[TargetedQuery] = Field(default_factory=list, max_length=5)


class ThesisStep(BaseModel):
    claim: str
    evidence_ids: List[str] = Field(default_factory=list)
    transmission_mechanism: str


class ThesisBranch(BaseModel):
    title: str
    status: Optional[DownsideStatus] = None
    scenario: str
    chain: List[ThesisStep] = Field(default_factory=list, max_length=5)
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    weak_links: List[str] = Field(default_factory=list)
    triggers: List[str] = Field(default_factory=list)
    invalidation_conditions: List[str] = Field(default_factory=list)


class WatchItem(BaseModel):
    signal: str
    why_it_matters: str
    confirm_if: str
    invalidate_if: str
    evidence_ids: List[str] = Field(default_factory=list, max_length=4)


class DecisionBrief(BaseModel):
    key_insight: str = ""
    why_it_matters: str = ""
    evidence_ids: List[str] = Field(default_factory=list, max_length=4)
    watch_items: List[WatchItem] = Field(default_factory=list, max_length=3)
    next_research_action: str = ""


class ThesisGraph(BaseModel):
    current_view: str
    why: List[str] = Field(default_factory=list, max_length=5)
    future_expectation: str
    upside: ThesisBranch
    downside: ThesisBranch
    decision_brief: DecisionBrief = Field(default_factory=DecisionBrief)


class ScoreCard(BaseModel):
    chain_support_confidence: int = Field(ge=0, le=100)
    evidence_quality: int = Field(ge=0, le=100)
    evidence_completeness: int = Field(ge=0, le=100)
    evidence_conflict: int = Field(ge=0, le=100)
    composite_score: Optional[float] = Field(default=None, ge=0, le=100)
    quality_label: Literal["low", "guarded", "moderate", "strong"]
    explanation: Dict[str, str] = Field(default_factory=dict)
