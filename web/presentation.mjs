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

export function modelTokenUsage(call = {}) {
  const input = numericValue(call.usage?.prompt_tokens);
  const output = numericValue(call.usage?.completion_tokens);
  const recordedTotal = numericValue(call.usage?.total_tokens);
  if (call.success === false && input === 0 && output === 0 && (recordedTotal === 0 || recordedTotal === null)) {
    return { input: null, output: null, total: null, measured: false };
  }
  const measured = input !== null && output !== null;
  return { input, output, total: recordedTotal ?? (measured ? input + output : null), measured };
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

function recordedObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length
    ? value : null;
}

function recordedArray(value) {
  return Array.isArray(value) ? value : [];
}

function intermediateRecords(report) {
  return recordedArray(report.agent_audit?.intermediate_results);
}

function finalRecordedOutput(report, field, node) {
  const direct = recordedObject(report[field]);
  if (direct) return direct;
  return intermediateRecords(report).filter(record => record.node === node)
    .map(record => recordedObject(record.output?.[field])).filter(Boolean).at(-1) || null;
}

function verificationChecks(verification, report) {
  const targetValues = [
    report.current_situation?.target_price,
    report.target_assessment?.target_price,
    report.quantitative_signals?.target_assessment?.target_price,
    report.quantitative_signals?.market?.target_price,
    report.task_summary?.target_price,
  ];
  const hasTarget = targetValues.some(value => numericValue(value) !== null);
  return Object.entries(recordedObject(verification?.checks) || {}).map(([key, value]) => {
    const passed = typeof value === "boolean" ? value : null;
    const status = key === "target_condition_consistent" && !hasTarget ? "not_applicable"
      : passed === true ? "pass" : passed === false ? "fail" : "unknown";
    return { key, passed, status };
  });
}

export function summarizeEvidenceGate(report = {}) {
  const gate = finalRecordedOutput(report, "evidence_gate", "normalize_and_evidence_gate");
  const hasEvidence = Array.isArray(report.evidence);
  const evidence = recordedArray(report.evidence);
  const presentTypes = Array.isArray(gate?.present_types) ? gate.present_types : null;
  const critical = recordedArray(gate?.critical_types).map(type => {
    const matches = evidence.filter(item => item.evidence_type === type);
    const validCount = hasEvidence ? matches.filter(item => item.temporal_valid !== false).length : null;
    const excludedCount = hasEvidence ? matches.filter(item => item.temporal_valid === false).length : null;
    return {
      type,
      present: presentTypes ? presentTypes.includes(type) : hasEvidence ? validCount > 0 : null,
      validCount,
      excludedCount,
    };
  });
  const decisions = recordedArray(report.agent_audit?.decision_audit);
  let verificationIndex = 0;
  const rounds = [];
  for (const record of intermediateRecords(report)) {
    const output = record.output || {};
    const base = { node: record.node, recordedAt: record.recorded_at ?? null };
    if (record.node === "normalize_and_evidence_gate" && recordedObject(output.evidence_gate)) {
      const round = output.evidence_gate;
      rounds.push({
        ...base,
        kind: "gate",
        coverage: numericValue(round.coverage_pct),
        criticalTypes: recordedArray(round.critical_types),
        presentTypes: recordedArray(round.present_types),
        missing: recordedArray(round.missing_critical),
        decision: round.decision ?? null,
        reason: round.reason ?? null,
        degraded: typeof round.degraded === "boolean" ? round.degraded : null,
        retryAllowed: typeof round.retry_allowed === "boolean" ? round.retry_allowed : null,
      });
    } else if (record.node === "targeted_retrieval") {
      rounds.push({
        ...base,
        kind: "retrieval",
        attempt: numericValue(output.attempt),
        searches: recordedArray(output.searches).map(search => ({
          source: search.source ?? null,
          query: search.query ?? null,
          reason: search.reason ?? null,
        })),
      });
    } else if (record.node === "verify_and_calibrate") {
      const decision = decisions.filter(item => item.node === record.node)[verificationIndex++];
      const verification = recordedObject(output.verification);
      if (!verification) continue;
      rounds.push({
        ...base,
        kind: "verification",
        checks: verificationChecks(verification, report),
        issues: recordedArray(verification.issues),
        needsMoreEvidence: typeof verification.needs_more_evidence === "boolean" ? verification.needs_more_evidence : null,
        requestedSources: recordedArray(verification.requested_sources),
        route: decision?.decision?.route ?? null,
      });
    }
  }
  return {
    recorded: Boolean(gate),
    critical,
    coverage: numericValue(gate?.coverage_pct),
    missing: recordedArray(gate?.missing_critical),
    decision: gate?.decision ?? null,
    reason: gate?.reason ?? null,
    rounds,
  };
}

export function summarizeVerification(report = {}) {
  const verification = finalRecordedOutput(report, "verification", "verify_and_calibrate");
  const repairs = intermediateRecords(report).filter(record => record.node === "generate_thesis_graph")
    .map(record => record.output?.rule_repair_applied).filter(value => typeof value === "boolean");
  const recordedRepairs = repairs.length ? repairs
    : recordedArray(report.agent_audit?.decision_audit).filter(record => record.node === "generate_thesis_graph")
      .map(record => record.decision?.repair_applied).filter(value => typeof value === "boolean");
  return {
    recorded: Boolean(verification),
    checks: verificationChecks(verification, report),
    issues: recordedArray(verification?.issues),
    ruleRepairCount: recordedRepairs.length ? recordedRepairs.filter(Boolean).length : null,
    referenceStats: recordedObject(verification?.reference_stats),
    numericStats: recordedObject(verification?.numeric_stats),
  };
}
