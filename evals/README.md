# Agent evaluation

`benchmark.json` is an independently labelled set of task, tool, and critical-evidence expectations. Run the complete live benchmark with:

```bash
python -m evals.run_eval --output evals/latest_report.json
```

Use `--limit 1` for a smoke test. A complete run calls live data sources and the configured Ollama model, so it is intentionally not part of the fast unit-test suite.
Each benchmark case uses a fresh temporary data directory by default, so it does not pollute normal report history or production vector memory and cannot inherit news from an earlier case. Pass `--data-dir` only when persistent evaluation state is intentional.

The report contains task classification accuracy, exact and multilabel tool-selection scores, critical evidence coverage, pre-repair invalid reference rate, traceability-based numeric error rate, completion rate, runtime, model calls, tokens, and cost. Local Ollama has a `$0` metered API token fee. Set `LLM_INPUT_USD_PER_MILLION_TOKENS` and `LLM_OUTPUT_USD_PER_MILLION_TOKENS` to add an explicitly labelled API-equivalent estimate.
