export const STAGES = [
  { label: "Plan", nodes: ["understand_request", "plan_research"] },
  { label: "Research", nodes: ["collect_evidence", "normalize_and_evidence_gate", "targeted_retrieval"] },
  { label: "Analyze", nodes: ["financial_quantitative_analysis", "generate_thesis_graph"] },
  { label: "Verify", nodes: ["verify_and_calibrate", "build_report"] },
];

export const DEMO_REQUESTS = {
  technical: { asset: "AAPL", question: "Show Apple's six month price trend, volatility, and maximum drawdown." },
  earnings: { asset: "AAPL", question: "Analyze Apple's latest earnings, revenue, margins, risks, and outlook. Compare with relevant previous news if available." },
  probability: { asset: "BTC", question: "What do ongoing Polymarket price markets imply about Bitcoin? Show the odds, nearby price thresholds, and evidence limitations." },
};

export function numericValue(value) {
  if (value == null || value === "" || typeof value === "boolean") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function stageStates(trace = [], currentNode, status = "running") {
  const active = STAGES.findIndex(stage => stage.nodes.includes(currentNode));
  const visited = new Set(trace.map(item => item.node));
  return STAGES.map((stage, index) => ({
    ...stage,
    status: status === "running" && active === index ? "is-active"
      : stage.nodes.some(node => visited.has(node)) ? "is-complete" : "is-pending",
  }));
}

export function summarizePolymarket(report = {}) {
  const calls = (report.agent_audit?.tool_calls || []).filter(call => call.function === "get_polymarket_context");
  const statuses = Object.entries(report.data_source_status || {})
    .filter(([name]) => name === "polymarket" || name.startsWith("polymarket_retry_"));
  const last = calls.at(-1) || statuses.at(-1)?.[1];
  const crowd = (report.evidence || []).filter(item => item.evidence_type === "crowd_expectation");
  const used = crowd.length;
  const aggregated = crowd.some(item => item.metadata?.provider === "coinrithm");
  const provider = last?.provider || last?.result_preview?.provider || report.research_plan?.prediction_provider;
  if (used) return {
    status: last && !last.success ? "partial" : "success",
    title: last && !last.success ? "Evidence available; later call failed" : aggregated ? "Data included via CoinRithm" : "Data retrieved",
    message: `${used} price outcomes included in this report's evidence.${aggregated ? " Data by CoinRithm; original venue Polymarket. These are aggregated quotes, not a successful Gamma direct connection." : ""}${last && !last.success ? " Inspect the failed later attempt in the tool audit." : ""}`,
  };
  if (last) {
    const code = last.error_code || last.result_preview?.error_code;
    if (provider === "coinrithm" && (last.status === "blocked" || last.access_blocked)) return {
      status: "blocked", title: "CoinRithm access blocked",
      message: "The selected aggregation provider denied access. No other provider was tried, and no data was supplied to the thesis LLM.",
    };
    if (last.status === "blocked" || last.access_blocked || code === "gamma_access_blocked_451") return {
      status: "blocked", title: "Called, blocked by upstream (HTTP 451)",
      message: "This run used Gamma direct and the upstream blocked access. No market evidence reached the thesis LLM. Saved reports keep their original provider and result; changing the form does not rewrite them.",
    };
    if (last.success) return {
      status: "success", title: "Data retrieved, none included",
      message: "The tool retrieved data, but no Polymarket item remains in the report's normalized evidence. Inspect the evidence gate and tool result.",
    };
    if (last.status === "empty" || code === "no_matching_ongoing_price_markets") return {
      status: "empty", title: "Called, no matching price markets",
      message: "No ongoing, asset-matched price market was retained. Partial retrieval errors, if any, remain in source diagnostics.",
    };
    return { status: "failed", title: "Called, retrieval failed", message: last.error || "Inspect the tool audit for the network or payload error." };
  }
  if ((report.research_plan?.sources || []).includes("polymarket")) return {
    status: "unknown", title: "Selected, no call record",
    message: "The plan selected Polymarket, but this report has no execution record. API success cannot be confirmed.",
  };
  return {
    status: "skipped", title: "Not called in this run",
    message: "The initial source rules skipped Polymarket and no call is recorded. This says nothing about current API connectivity. Price forecasts, targets, or explicit Polymarket requests enable it in new runs.",
  };
}

export function summarizeExecution(report = {}) {
  const calls = report.agent_audit?.tool_calls || [];
  const llmCalls = report.llm?.calls || [];
  const decisions = report.agent_audit?.decision_audit || [];
  const trace = report.workflow?.node_trace || [];
  const failedLlm = llmCalls.filter(call => !call.success).length;
  const repaired = decisions.filter(record => record.decision?.repair_applied).length;
  const checks = Object.entries(report.verification?.checks || {});
  const failedChecks = checks.filter(([, passed]) => passed === false).map(([name]) => name);
  const override = decisions.find(record => record.decision?.task_rule_override)?.decision.task_rule_override;
  const rejected = Object.entries(report.research_plan?.source_rationale || {})
    .filter(([, reason]) => reason.llm_proposed_but_rejected).map(([name]) => name);
  const memoryCalls = calls.filter(call => call.function === "PersistentNewsMemory.retrieve");
  const embedding = memoryCalls.at(-1)?.result_preview?.embedding;
  const memoryUsed = (report.history_chunks_used || []).length;
  const retryCount = report.workflow?.retrieval_attempts ?? trace.filter(item => item.node === "targeted_retrieval").length;
  return {
    calls, llmCalls, failedLlm, repaired, override, rejected, embedding, memoryUsed,
    memoryRead: memoryCalls.length > 0,
    toolSuccess: calls.filter(call => call.success).length,
    llmSuccess: llmCalls.filter(call => call.success).length,
    retryCount, failedChecks,
    checksPassed: checks.filter(([, passed]) => passed === true).length,
    checksTotal: checks.length,
    issues: report.verification?.issues || [],
    missing: report.evidence_gate?.missing_critical || [],
    failedSources: Object.entries(report.data_source_status || {})
      .filter(([, value]) => ["failed", "blocked"].includes(value.status)).map(([name]) => name),
  };
}
