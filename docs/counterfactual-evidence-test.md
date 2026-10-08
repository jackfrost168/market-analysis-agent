# Optional Counterfactual Evidence Test

This post-analysis test asks whether an existing claim still has support after
withholding one cited evidence item. It measures qualitative evidence dependence,
not causal effects, prediction probabilities, or proof that a financial claim is true.

## Enable

On the website, the always-visible **反事实证据测试 / Counterfactual Evidence Test**
checkbox below the question enables this check for the next analysis. Leave it
unchecked to avoid additional calls. The adjacent text shows the server default
and per-run choice, and a result shortcut scrolls to the report section. Historical
reports without this field display “本报告未记录反事实证据测试”; failed checks display
their status instead of hiding the whole section.

API callers can pass `enable_counterfactual_evidence_test: true` or `false` in
`POST /api/runs` or `POST /api/analyze`. An explicit choice overrides the environment
default **for that request only**. Omit the field to follow the environment setting.
`GET /api/counterfactual/status` exposes the default and limits without model calls.

To change the server default, set these environment variables and restart it:

```sh
ENABLE_COUNTERFACTUAL_EVIDENCE_TEST=true
COUNTERFACTUAL_MAX_CLAIMS=1
COUNTERFACTUAL_MAX_EVIDENCE=3
COUNTERFACTUAL_BUDGET_SECONDS=60
COUNTERFACTUAL_CALL_TIMEOUT_SECONDS=15
```

The default is **disabled**. Merely editing `.env.example` does not enable it.
Existing configuration loading/startup should be used to provide these variables.
Set the flag to `false` to disable it; no extra model calls, state fields, or report
section are added in that mode.

## Integration and bounds

`AgentService.analyze()` calls `run_counterfactual()` once **after the existing
nine-node graph finishes**, and before metrics/persistence. The graph, retrieval,
Evidence Gate, original thesis, verifier and report sections remain intact.
When enabled, only `report.counterfactual_evidence_test` is appended. The website
renders this compact section and saved reports retain it.

- Main claim candidates reuse the decision brief and linked upside/downside
  scenarios. If none has links, an existing current-view claim can use a bounded
  evidence bundle; the selector infers links in that case.
- A single structured LLM call selects at most **two claims**, ranks at most
  **three evidence IDs per claim**, and judges baseline support using each claim's
  full supporting **excerpt** set. It does not regenerate the claim.
- Selection prefers direct relevance, source reliability, existing relevance
  scores, and distinct facts. Duplicate excerpts/news-group targets are collapsed.
- By default, one claim has at most three ablations: **four added calls** overall.
  Set `COUNTERFACTUAL_MAX_CLAIMS=2` for at most six ablation calls and
  **seven added calls** overall.
- Each ablation receives the unchanged claim and only its remaining supporting
  evidence excerpts. Removed content, baseline reasoning, other claim bundles,
  quantitative aggregates and the full original report are not supplied. A fact
  repeated independently in another retained item remains legitimate support.
- Selection uses at most 16 distinct evidence items, with 600-character content
  excerpts. Explicit supporting sets over 16 items are skipped instead of silently
  truncating their evidence. Long claims over 1,800 characters are skipped.
- Outputs are capped at 512 tokens per call. These optional calls request
  `think=false` via the [Ollama generation API](https://docs.ollama.com/api/generate)
  so the small output budget is available for JSON. Existing model calls keep
  their settings. Calls run sequentially and share a
  60-second default budget, with a 15-second per-request timeout. Model/network
  overhead may add small timing overhead. No retries occur. A model failure stops
  further calls and leaves untested evidence unclassified.
- Unsupported baselines are not ablated: necessity cannot be established without
  baseline support. Failures produce `partial`/`unavailable`, never fabricated
  importance labels. The original research report remains available.

## Categories

Support: `strong`, `moderate`, `weak`, `unsupported`.

- **Critical / high**: removal makes the claim unsupported, loses a core thesis
  assertion/direction, or drops support by at least two ordinal categories.
- **Important / medium**: support drops by one category but remains supported.
- **Supporting / low**: no categorical weakening and no core thesis change.

The ordinal categories are not numeric confidence estimates. A higher support
category after removal is retained in the record; it is labelled low dependency,
not silently changed into a decrease. Redundant evidence can appear Supporting
even when its information is useful, because another item supplies the same fact.

## State, logging and example

Only `counterfactual_tests` and `evidence_dependency` are added to state. They hold
IDs, categories, short reasons, statuses and timing, not new copies of documents.
Additional model calls reuse `llm_calls` and existing token/runtime metrics.
Each attempted ablation also logs a JSON record with `claim_id`,
`removed_evidence_id`, `impact`, `support_before`, `support_after`, and `latency_ms`.

Illustrative output from the deterministic **test fixture**, not a measured model score:

```text
Counterfactual Evidence Test
Claim: Revenue and margin improvement support the positive thesis.

Revenue Growth → Critical     (strong → weak; high impact)
Gross Margin   → Critical     (strong → weak; high impact)
AI Demand News → Supporting   (strong → strong; low impact)

Strong dependence on Revenue Growth and Gross Margin;
limited effect from removing AI Demand News.
```

The expected fixture behavior is not encoded in production. Run the tests with:

```sh
python -m unittest discover -s tests -p 'test_counterfactual.py' -v
```

A live `qwen3:8b` smoke test on the same synthetic financial facts selected
Revenue Growth and Gross Margin, judging both removals `strong -> weak` with
`Critical / high` dependency. It did not select AI Demand News, which remains
untested rather than being assigned a label. This single warm-model run took
13.9 seconds, three added calls and 1,253 recorded tokens using the default
budgets. See [the recorded example](counterfactual-example.json). These timings
and judgments are illustrative, not benchmark accuracy or a latency guarantee.

This is an additional LLM judgment, not a calibrated or independently certified
semantic verifier. A sensitivity result can be wrong; test it on labelled examples
before treating it as a quality metric.
