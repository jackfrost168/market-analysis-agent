import test from "node:test";
import assert from "node:assert/strict";
import { numericValue, stageStates, summarizeExecution, summarizePolymarket } from "../web/presentation.mjs";

test("missing prices remain missing rather than becoming zero", () => {
  for (const value of [null, undefined, "", false, "bad", Infinity]) assert.equal(numericValue(value), null);
  assert.equal(numericValue("100.1234"), 100.1234);
  assert.equal(numericValue(0), 0);
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
