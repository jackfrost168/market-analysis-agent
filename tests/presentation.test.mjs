import test from "node:test";
import assert from "node:assert/strict";
import { numericValue, modelTokenUsage, apiCostText, stageStates, summarizeExecution, summarizePolymarket, summarizeEvidenceGate, summarizeVerification } from "../web/presentation.mjs";

test("API costs keep small dollar amounts visible and unknown costs explicit", () => {
  assert.equal(apiCostText(0.000503), "$0.000503");
  assert.equal(apiCostText(0), "$0.000000");
  assert.equal(apiCostText(null), "Unreported");
  assert.equal(apiCostText(0.000503, 1), "$0.000503 + unknown cost (1 calls)");
  assert.equal(modelTokenUsage({ success: true, usage: { usage_reported: false, prompt_tokens: 0, completion_tokens: 0 } }).measured, false);
});

test("missing prices remain missing rather than becoming zero", () => {
  for (const value of [null, undefined, "", false, "bad", Infinity]) assert.equal(numericValue(value), null);
  assert.equal(numericValue("100.1234"), 100.1234);
  assert.equal(numericValue(0), 0);
});

test("failed calls with zero placeholder counters have unreported usage", () => {
  assert.deepEqual(modelTokenUsage({ success: false, usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } }),
    { input: null, output: null, total: null, measured: false });
  assert.deepEqual(modelTokenUsage({ success: false, usage: { prompt_tokens: 0, completion_tokens: 0 } }),
    { input: null, output: null, total: null, measured: false });
});

test("failed calls preserve nonzero counters and prefer the recorded total", () => {
  assert.deepEqual(modelTokenUsage({ success: false, usage: { prompt_tokens: 20, completion_tokens: 5, total_tokens: 26 } }),
    { input: 20, output: 5, total: 26, measured: true });
  assert.deepEqual(modelTokenUsage({ success: false, usage: { prompt_tokens: 20, completion_tokens: 5 } }),
    { input: 20, output: 5, total: 25, measured: true });
});

test("successful zero-token calls remain measured", () => {
  assert.deepEqual(modelTokenUsage({ success: true, usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } }),
    { input: 0, output: 0, total: 0, measured: true });
});

test("legacy missing token fields remain unavailable", () => {
  assert.deepEqual(modelTokenUsage({}), { input: null, output: null, total: null, measured: false });
  assert.deepEqual(modelTokenUsage({ usage: { total_tokens: 30 } }),
    { input: null, output: null, total: 30, measured: false });
});

test("progress points forward after a completed node", () => {
  const stages = stageStates([{ node: "plan_research" }], "collect_evidence");
  assert.equal(stages[0].status, "is-complete");
  assert.equal(stages[1].status, "is-active");
  assert.equal(stages[2].status, "is-pending");
});

test("feedback activates Research again without inventing a linear run", () => {
  const stages = stageStates([{ node: "generate_thesis_graph" }, { node: "verify_and_calibrate" }], "targeted_retrieval");
  assert.equal(stages.filter(stage => stage.status === "is-active").length, 1);
  assert.equal(stages[1].status, "is-active");
});

test("execution summary uses actual successes, failures, repairs and memory records", () => {
  const summary = summarizeExecution({
    agent_audit: {
      tool_calls: [
        { function: "get_price_snapshot", success: true },
        { function: "get_polymarket_context", success: false },
        { function: "PersistentNewsMemory.retrieve", success: true, result_preview: { embedding: { backend: "local_hash", model: "local-hash-v1" } } },
      ],
      decision_audit: [{ decision: { repair_applied: true } }],
    },
    llm: { selected_model: "test-model", calls: [{ success: true }, { success: false }] },
    workflow: { retrieval_attempts: 1 },
    history_chunks_used: [{ id: "past-1" }],
    verification: { checks: { links_exist: true, enough_evidence: false } },
  });
  assert.equal(summary.toolSuccess, 2);
  assert.equal(summary.llmSuccess, 1);
  assert.equal(summary.failedLlm, 1);
  assert.equal(summary.repaired, 1);
  assert.equal(summary.memoryUsed, 1);
  assert.equal(summary.embedding.backend, "local_hash");
  assert.equal(summary.retryCount, 1);
  assert.deepEqual(summary.failedChecks, ["enough_evidence"]);
});

test("legacy reports do not imply an LLM ran just because a model was selected", () => {
  const summary = summarizeExecution({ llm: { selected_model: "qwen" } });
  assert.equal(summary.llmSuccess, 0);
  assert.equal(summary.calls.length, 0);
  assert.equal(summary.memoryRead, false);
});

test("Polymarket plan rejection does not mean an API request failed", () => {
  assert.equal(summarizePolymarket({ research_plan: { sources: ["yahoo_finance"] } }).status, "skipped");
  assert.equal(summarizePolymarket({ research_plan: { sources: ["polymarket"] } }).status, "unknown");
});

test("Polymarket distinguishes upstream block, timeout and empty markets", () => {
  const result = status => summarizePolymarket({ data_source_status: { polymarket: status } });
  assert.equal(result({ status: "blocked", error_code: "gamma_access_blocked_451" }).status, "blocked");
  assert.equal(result({ status: "failed", error_code: "gamma_retrieval_failed" }).status, "failed");
  assert.equal(result({ status: "empty", error_code: "no_matching_ongoing_price_markets" }).status, "empty");
});

test("successful retry overrides the original blocked status, using actual evidence", () => {
  const result = summarizePolymarket({
    data_source_status: { polymarket: { status: "blocked", success: false } },
    agent_audit: { tool_calls: [
      { function: "get_polymarket_context", status: "blocked", success: false },
      { function: "get_polymarket_context", status: "success", success: true },
    ] },
    evidence: [{ evidence_type: "crowd_expectation" }],
  });
  assert.equal(result.status, "success");
  assert.match(result.message, /1 price outcomes/);
});

test("aggregation evidence names its provider without claiming Gamma success", () => {
  const result = summarizePolymarket({
    data_source_status: { polymarket: { success: true, provider: "coinrithm" } },
    evidence: [{ evidence_type: "crowd_expectation", metadata: { provider: "coinrithm" } }],
  });
  assert.match(result.title, /CoinRithm/);
  assert.match(result.message, /not a successful Gamma/);
});

test("aggregation access denial is not mislabeled as Gamma failure", () => {
  const result = summarizePolymarket({ data_source_status: {
    polymarket: { provider: "coinrithm", success: false, status: "blocked" },
  } });
  assert.equal(result.title, "CoinRithm access blocked");
});

test("evidence rounds preserve the changing gaps and actual retrieval query", () => {
  const initialGate = {
    critical_types: ["market_price", "news"], present_types: ["market_price"],
    missing_critical: ["news"], coverage_pct: 50, decision: "retrieve_more",
    reason: "Missing critical evidence: news.", retry_allowed: true, degraded: false,
  };
  const finalGate = {
    ...initialGate, present_types: ["market_price", "news"], missing_critical: [],
    coverage_pct: 100, decision: "analyze", reason: "All critical evidence types are present.",
    retry_allowed: false,
  };
  const query = { source: "google_news", query: "Apple earnings catalyst", reason: "The evidence gate is missing news." };
  const report = { agent_audit: { intermediate_results: [
    { node: "normalize_and_evidence_gate", recorded_at: "first", output: { evidence_gate: initialGate } },
    { node: "targeted_retrieval", recorded_at: "second", output: { attempt: 1, searches: [query] } },
    { node: "normalize_and_evidence_gate", recorded_at: "third", output: { evidence_gate: finalGate } },
  ] } };
  const summary = summarizeEvidenceGate(report);
  assert.equal(summary.recorded, true);
  assert.equal(summary.coverage, 100);
  assert.deepEqual(summary.missing, []);
  assert.deepEqual(summary.rounds.map(round => round.kind), ["gate", "retrieval", "gate"]);
  assert.deepEqual(summary.rounds[0].missing, ["news"]);
  assert.equal(summary.rounds[1].attempt, 1);
  assert.deepEqual(summary.rounds[1].searches, [query]);
  assert.equal(summary.rounds[2].coverage, 100);
  assert.equal(summary.rounds[2].recordedAt, "third");
});

test("future-dated evidence is counted as excluded instead of satisfying the gate", () => {
  const summary = summarizeEvidenceGate({
    evidence_gate: { critical_types: ["news"], present_types: [], missing_critical: ["news"], coverage_pct: 0 },
    evidence: [{ evidence_type: "news", temporal_valid: false }],
  });
  assert.deepEqual(summary.critical, [{ type: "news", present: false, validCount: 0, excludedCount: 1 }]);
  assert.equal(summary.coverage, 0);
  assert.deepEqual(summary.missing, ["news"]);
});

test("the saved final gate takes precedence and missing coverage is not calculated", () => {
  const summary = summarizeEvidenceGate({
    evidence_gate: { critical_types: ["news"], present_types: ["news"], missing_critical: [] },
    evidence: [{ evidence_type: "news" }],
    agent_audit: { intermediate_results: [{ node: "normalize_and_evidence_gate", output: {
      evidence_gate: { critical_types: ["news"], missing_critical: ["news"], coverage_pct: 0 },
    } }] },
  });
  assert.equal(summary.critical[0].present, true);
  assert.equal(summary.critical[0].validCount, 1);
  assert.equal(summary.coverage, null);
  assert.deepEqual(summary.missing, []);
});

test("legacy reports retain unavailable checks, counts and coverage instead of implying success", () => {
  const gate = summarizeEvidenceGate({});
  assert.equal(gate.recorded, false);
  assert.equal(gate.coverage, null);
  assert.deepEqual(gate.critical, []);
  assert.deepEqual(gate.rounds, []);
  const partial = summarizeEvidenceGate({ evidence_gate: { critical_types: ["news"] } });
  assert.deepEqual(partial.critical, [{ type: "news", present: null, validCount: null, excludedCount: null }]);
  const verification = summarizeVerification({});
  assert.equal(verification.recorded, false);
  assert.equal(verification.ruleRepairCount, null);
  assert.deepEqual(verification.checks, []);
});

test("a vacuously true target check is not applicable without a recorded target", () => {
  const summary = summarizeVerification({ verification: {
    checks: { evidence_ids_exist: true, claims_have_support: false, target_condition_consistent: true, undocumented: "true" },
  } });
  assert.deepEqual(summary.checks, [
    { key: "evidence_ids_exist", passed: true, status: "pass" },
    { key: "claims_have_support", passed: false, status: "fail" },
    { key: "target_condition_consistent", passed: true, status: "not_applicable" },
    { key: "undocumented", passed: null, status: "unknown" },
  ]);
});

test("recorded targets make the target check applicable across real report fields", () => {
  for (const target of [
    { current_situation: { target_price: 100 } },
    { target_assessment: { target_price: 100 } },
    { quantitative_signals: { target_assessment: { target_price: 100 } } },
    { quantitative_signals: { market: { target_price: 100 } } },
  ]) {
    const summary = summarizeVerification({ ...target, verification: { checks: { target_condition_consistent: false } } });
    assert.deepEqual(summary.checks, [{ key: "target_condition_consistent", passed: false, status: "fail" }]);
  }
});

test("verification issues are retained without treating every detected issue as repaired", () => {
  const issues = [
    { type: "unsupported_claim", claim: "Unsupported conclusion" },
    { type: "unverified_numeric_claim_repaired", details: [{ path: "current_view", value: "$999" }] },
  ];
  const summary = summarizeVerification({ agent_audit: { intermediate_results: [
    { node: "generate_thesis_graph", output: { rule_repair_applied: true } },
    { node: "generate_thesis_graph", output: { rule_repair_applied: false } },
    { node: "verify_and_calibrate", output: { verification: { checks: { claims_have_support: false }, issues } } },
  ] } });
  assert.equal(summary.recorded, true);
  assert.equal(summary.ruleRepairCount, 1);
  assert.deepEqual(summary.issues, issues);
  assert.equal(summary.checks[0].status, "fail");
  assert.equal(summary.issues[0].repaired, undefined);
});

test("verification rounds use recorded routes for each occurrence without inventing missing routes", () => {
  const report = { agent_audit: {
    intermediate_results: [
      { node: "verify_and_calibrate", output: { verification: { needs_more_evidence: true, requested_sources: ["google_news"] } } },
      { node: "verify_and_calibrate", output: { verification: { needs_more_evidence: false } } },
      { node: "verify_and_calibrate", output: { verification: { needs_more_evidence: false } } },
    ],
    decision_audit: [
      { node: "verify_and_calibrate", decision: { route: "targeted_retrieval" } },
      { node: "verify_and_calibrate", decision: { route: "build_report" } },
    ],
  } };
  const { rounds } = summarizeEvidenceGate(report);
  assert.deepEqual(rounds.map(round => round.route), ["targeted_retrieval", "build_report", null]);
  assert.deepEqual(rounds[0].requestedSources, ["google_news"]);
  assert.deepEqual(rounds.map(round => round.needsMoreEvidence), [true, false, false]);
});
