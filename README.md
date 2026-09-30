# Stateful Market Research Agent

This is a new LangGraph-based financial research application. It keeps the warm visual language of `E:\polymarket_trading`, but replaces the previous one-shot workflow with a stateful, observable Agent. The request form is full-width at the top and the complete result appears directly below it.

## Interview Demo

**One question -> task-aware tools -> evidence-linked analysis -> bounded verification.**

The page presents four stages rather than making the user inspect nine nodes upfront. Three example questions demonstrate technical analysis, earnings research with memory, and prediction-market expectations. Examples fill the form; only **Run autonomous analysis** starts research.

Completed reports summarize actual tool and LLM calls, fallbacks, memory usage, and retrieval retries. Detailed arguments, prompts, decisions, and intermediate state remain expandable. **Replay** loads a saved report with its original timestamp, without rerunning tools or the model. **Export report JSON** includes the original evidence and audit.

See [the Chinese interview guide](INTERVIEW_DEMO.md) for a short introduction, a three-minute demonstration, and honest technical boundaries.

The previous project was not deleted. It is archived at:

```text
E:\market_analysis_agent\_legacy_memory_agent
```

## Run

The tested environment is Python 3.12 in `ogc2026`.

The FastAPI entry point for a server or AWS EC2 is `awsdeploy.api:app`:

```bash
python3.12 -m pip install -r requirements.txt
python3.12 -m uvicorn awsdeploy.api:app --host 127.0.0.1 --port 8001 --workers 1
```

Open [http://127.0.0.1:8001](http://127.0.0.1:8001). FastAPI serves the existing web UI, all existing `/api/` routes, and interactive API documentation at `/docs`. See [the AWS preparation guide](awsdeploy/README.md) for environment variables, persistent storage, and server setup.

The original standard-library HTTP server remains available for local use:

```powershell
cd E:\market_analysis_agent
D:\Anaconda3\envs\ogc2026\python.exe -m pip install -r requirements.txt
D:\Anaconda3\envs\ogc2026\python.exe server.py
```

### Target-price interpretation

Targets are positive finite amounts in the quote currency. The condition selector supports at-or-above and at-or-below; automatic mode recognizes explicit downward questions and otherwise defaults to at-or-above. Python computes current threshold satisfaction and the required percentage move before the LLM runs, and guards the final summary after verification. Current satisfaction is not a future closing-price prediction. A target at least 10 times or at most 0.1 times the quote triggers an input-sanity warning, not a claim of impossibility. No target probability is inferred from price distance or nearby prediction-market contracts. Raw LLM reasoning remains visible in the audit; directional thesis branches remain evidence-based qualitative analysis, not calibrated target forecasts.

For `server.py`, port `8001` is the default and `PORT` overrides it. For FastAPI, set the port with Uvicorn's `--port` option.

## Agent Graph

The application uses a real `langgraph.graph.StateGraph` with 9 nodes and 12 graph edges:

```text
START
  -> Understand Request
  -> Plan Research
  -> Collect Evidence
  -> Normalize and Evidence Gate
       -> Targeted Retrieval -> Normalize and Evidence Gate
       -> Financial and Quantitative Analysis
  -> Generate Thesis Graph
  -> Verify and Calibrate
       -> Targeted Retrieval when major support is missing
       -> Build Report
  -> END
```

Both loops are bounded by `max_retries`. A failed source degrades the report and is shown in source status instead of crashing the whole run.

Supported task types are fixed:

- `company_analysis`
- `technical_analysis`
- `earnings_analysis`
- `move_explanation`
- `company_outlook`
- `event_impact`
- `general_research`

Ollama cannot create arbitrary task types because structured output is validated against this enum.

## Data And RAG Flow

The planning node first selects relevant sources from the natural-language request. Only selected first-round calls run in parallel where possible:

- Yahoo Finance chart API: precise current price, returns, volume, and price history
- Google News RSS: recent news plus bounded article paragraph extraction
- SEC Company Facts: reported revenue, income, EPS, assets, and cash for supported US companies
- Prediction-market evidence: CoinRithm aggregation by default, or explicitly selected Gamma direct; ongoing asset-matched price events only
- SQLite vector DB: top 3 similar historical news paragraphs

Vector retrieval happens before current news is stored. This prevents current articles from being mislabeled as history during the same run. Each extracted article paragraph is one chunk. When publisher pages block extraction, the headline or RSS summary is stored as an explicit fallback chunk.

Persistent files are stored in `data/`:

```text
data/news_vectors.sqlite3   # paragraph chunks and embeddings
data/agent_runs.sqlite3    # completed reports and final Agent state
```

These databases remain available after stopping and restarting the application.

LangGraph execution checkpoints use `InMemorySaver`, not a durable checkpointer. Completed reports and final states persist in SQLite; interrupted runs do not automatically resume after a server restart.

The embedding service uses `OLLAMA_EMBED_MODEL` when configured. If no embedding model is configured or available, it uses a deterministic 512-dimensional local hash embedding so persistence and retrieval still work offline.

## LLM Use

No OpenAI API is used. All LLM reasoning is local through Ollama.

The Agent uses the LLM for:

1. Understanding an ordinary user question as a validated financial task.
2. Proposing focused research questions and candidate sources.
3. Constrained targeted search queries when the evidence gate finds a gap.
4. Evidence-linked upside and downside thesis wording.

`local_ollama_understanding` means Ollama understood the ordinary question and extracted validated fields such as asset, task, horizon, and requirements. It does not mean the user must write JSON or use a special prompt.

Source planning is not an unrestricted LLM choice. Ollama proposes sources, then deterministic rules check the task, asset type, target price, and source capability. The report shows every selected and skipped source, the reason, and whether an LLM proposal was rejected. Examples:

- A current-price-only question uses Yahoo Finance only.
- A stock earnings question can use Yahoo, Google News, SEC fundamentals, and vector memory, but skips Polymarket and price history unless explicitly needed.
- A move explanation uses current price, news, price history, and vector memory.
- Polymarket is selected for a price forecast (including ordinary wording such as "make a prediction of TSLA price"), a supplied target price, or an explicit probability, odds, crowd, or prediction-market request. Ordinary earnings and historical-trend questions still do not call it automatically.

The main task-aware routes are:

| User task | Default external and memory tools |
| --- | --- |
| Current quote | Yahoo Finance |
| Technical trend, volatility, or drawdown | Yahoo Finance + price history |
| Earnings or fundamentals for a US stock | Yahoo Finance + SEC; Google News + vector memory when fresh reporting is relevant |
| Explain a price move | Yahoo Finance + Google News + price history + vector memory |
| Event impact or company outlook | Yahoo Finance + Google News + vector memory |
| Price forecast, target-price probability or prediction-market view | Yahoo Finance + price history + Polymarket; news/memory when the task needs current context |

Every completed report displays a `Resolved task route` signature. The tool audit below it is the stronger check: it lists only functions that were actually called, including arguments, latency, result count, status, and bounded output preview.

Python, not the LLM, calculates returns, close-to-close maximum drawdown over the retrieved history, volatility, growth, margins, target distance, probability statistics, and consistency scores. Missing price history produces unavailable values, not zero risk.

The thesis LLM receives:

- the classified task and horizon
- the evidence gate result
- deterministic quantitative output
- normalized current evidence with evidence IDs
- at most 3 vector-retrieved historical paragraphs

The UI exposes each model call, its actual model, purpose, latency, input manifest, prompt preview, and fallback status.

`auto` now selects installed **Qwen 3 8B (`qwen3:8b`)** first, followed by Qwen 2.5 8B, Llama 3.1/3 8B, Gemma 4B, Llama 3.2 3B, Mistral Small, then the first available model. A manually selected model or an explicit `OLLAMA_MODEL` override still takes precedence. Larger models remain selectable; they are not the automatic default. The page and audit show the actual model used.

## Polymarket Rules

See [the current access diagnosis](POLYMARKET_ACCESS_DIAGNOSIS.md): the 2026-09-14 browser check displayed a Korea-specific legal access-block page, independently of the Python client. A code change or model switch cannot remove that restriction.

Prediction data now has two explicitly selected providers. The default is **CoinRithm aggregation**, the independent published dataset also used by the old Agent V2. Results are attributed as **Data by CoinRithm**, with original venue **Polymarket**. Gamma direct remains available under **Model, horizon & advanced options > Prediction data provider**. `PREDICTION_DATA_PROVIDER=coinrithm|gamma` configures the default; the form sends its visible choice as `prediction_provider`. Example environment files are not automatically loaded.

This is a data-provider change, not a repair to the Gamma legal restriction. A run calls only its selected provider; HTTP 401/403/451 never triggers a provider switch. No proxies, location routing, credentials, or fixture prices are used. See [CoinRithm's public API and attribution documentation](https://www.coinrithm.com/en/prediction-markets/api). Its public surface is for bounded exploratory use, with no uptime SLA.

CoinRithm requests `/api/prediction-markets/events?status=open&source=polymarket&q=...`. Searches use asset aliases plus a rounded price reference, then rank using the precise current/target price. The adapter rejects other venues/assets, non-price events, ended/resolved events, missing end dates, missing/stale/future timestamps, provider quality rejections, malformed outcomes, and ambiguous duplicate outcome labels. Data must be at most 24 hours old. Probabilities are converted from percent to ratio, including sub-1% values. Rounded 0%/100% quotes are excluded because an open event can contain settled outcome legs. Individual outcome tradability is not independently verified; this limitation is included in the final report.

The selected provider, HTTP probe results, filtering counts, attribution, provider data timestamp, and outcome probabilities are preserved in tool audits and normalized evidence. The thesis LLM receives that attributed evidence; a new retrieval timestamp does not make old provider data fresh.

When **Gamma direct** is selected, it uses `https://gamma-api.polymarket.com` and documented Gamma paths:

- `/public-search` with `q`, `events_status=active`, and `keep_closed_markets=0`
- `/events` with `active=true`, `closed=false`, and an asset tag when available

Under **Prediction data connection diagnostics**, **Check selected provider** tests that provider only. **Check Gamma separately** tests the official Gamma paths and geoblock diagnostic without changing the run's selected provider. Checks do not run automatically on page load. `GET /api/polymarket/providers` returns configuration without network probes; `GET /api/polymarket/status?provider=coinrithm|gamma` runs the selected diagnostic. Gamma does not require an API key for these public endpoints; `POLYMARKET_GAMMA_URL` remains a test-only base URL override.

Filtering requires:

- active, non-closed, non-archived status
- an end date that has not passed when an end date is available
- exact asset term matching across the event and market text
- a price question, price threshold, or explicit up/down market
- valid outcome and probability arrays

The report's Polymarket status separates **not called**, **called but blocked**, **retrieval failed**, **no matching markets**, and **data included**. A rule-rejected LLM source proposal is not an API error. HTTP errors and timeouts are not silently relabeled as zero matches. Event-detail responses from `/events/slug/{slug}` are parsed as a single event object, as specified in the [official Gamma API documentation](https://docs.polymarket.com/api-reference/events/get-event-by-slug).

On 2026-09-14, current Gamma probes and the older projects' `/public-search?q=tesla` and `/events?limit=100&closed=false&search=tsla` request patterns all returned HTTP 451, provider code 1026, from this host. This records that check only, not a claim about earlier availability. Use **Check Gamma separately** to recheck current connectivity. No stale or fixture markets are substituted for blocked live data.

Legacy comparison correction: `E:\polymarket_trading\agent_v2_core.py` also contains a CoinRithm aggregation path. It was missing here until 2026-09-16. The new adapter restores support for that independent data source, but intentionally does not copy the legacy automatic provider switch after an access denial. A successful V2 report does not prove Gamma succeeded. The original `server.py` snapshot function was retested without cached data for BTC and TSLA on the same interpreter and returned 451 for both. Existing saved reports retain their original provider and results: start a new analysis to use the new selection.

Live verification on 2026-09-16: BTC and TSLA each returned five filtered price outcomes from CoinRithm. A complete BTC run (`ad9e38a6-0623-4782-96ad-80f8b47ee672`) used Qwen3 8B for all three LLM steps successfully; its thesis input manifest includes all five crowd-evidence IDs. This verifies the aggregation adapter and evidence path, not Gamma direct availability or forecast accuracy.

When a target is provided, markets are ranked by nearest target threshold first. Otherwise they are ranked by nearest current-price threshold. The precise price remains unchanged in the UI and evidence; only search terms use a rounded reference price.

If Gamma returns HTTP `451`, the run reports `gamma_access_blocked_451` with source status `blocked`, not generic `failed`. That means the upstream edge rejected the request before any Gamma payload reached the filter, so it is intentionally not displayed as “no related markets.” On the current host, both discovery routes and the official geoblock diagnostic returned Cloudflare `451` with provider code `1026`; changing filters, search terms, or API keys cannot repair a request that never reaches Gamma.

## Agent Audit And Intermediate Results

Every executed node appends three persisted audit streams to the shared LangGraph state:

- `decision_audit`: input snapshot, decision mode, decision makers (`LLM`, rules, Python, or tools), applied rules, route, and output snapshot
- `intermediate_results`: the observable state product emitted by that node
- `tool_calls`: exact function name, sanitized arguments, timing, status, record count, error code, and bounded result preview

These records are stored inside `data/agent_runs.sqlite3` with the completed state. They are visible live while a run is executing and remain available after restart.

Audit endpoints:

```text
GET /api/runs/{run_id}        # run status, report, and live audit streams
GET /api/runs/{run_id}/audit  # decision, intermediate, tool, and LLM records only
GET /api/polymarket/status    # live Gamma configuration and endpoint probes
```

The audit shows externally observable inputs, explicit rules, routes, function calls, and structured outputs. It does not claim to expose a model's private token-by-token chain-of-thought.

## Quality Scores

The report separates four scores:

- **Chain support confidence**: valid thesis evidence references adjusted for source reliability
- **Evidence quality**: mean reliability of normalized evidence
- **Evidence completeness**: task-specific critical evidence coverage
- **Evidence conflict**: directional disagreement; higher means more conflict and is not a positive score

Verification also checks invalid evidence IDs, unsupported claims, chronology, causal misuse of Yahoo momentum, and factual misuse of Polymarket expectations. A downside branch can remain `NOT_SUPPORTED`; the Agent does not fabricate bearish evidence for visual symmetry.

These are heuristic evidence diagnostics, not calibrated prediction accuracy, truth probabilities, or investment success rates. Existing evidence IDs establish traceability, not semantic entailment of every claim.

## Evaluation And Runtime Metrics

The independently labelled cases in `evals/benchmark.json` measure task classification, tool selection, critical-evidence coverage, pre-repair invalid references, traceability-based numeric errors, completion, runtime, model calls, and generation-token usage. Run a smoke case or the full live suite with:

```bash
python -m evals.run_eval --limit 1
python -m evals.run_eval --output evals/latest_report.json
```

Ollama generation usage comes from `prompt_eval_count` and `eval_count`. Local Ollama has no metered API token fee. Optional input/output rates produce an explicitly labelled API-equivalent estimate; hardware and electricity are outside that estimate.

The browser receives node-level progress over Server-Sent Events and falls back to polling after a connection failure. This is structured workflow streaming rather than token-by-token thesis streaming, so incomplete JSON is never rendered as a report. `GET /api/metrics` summarizes persisted run completion, duration, model calls, tokens, and active in-process work.

## Tests

```powershell
D:\Anaconda3\envs\ogc2026\python.exe -m unittest discover -s tests -v
# Optional, when pytest and Node.js are installed:
D:\Anaconda3\envs\ogc2026\python.exe -m pytest tests -q
node --test tests/presentation.test.mjs
```

The test suite covers evidence normalization and bounded gates, deterministic financial calculations, Polymarket price parsing, vector persistence across fresh instances, and an end-to-end offline LangGraph run.

Regression tests also cover selective tool dispatch, precise target-price forwarding, bounded failure recovery, persisted report replay, drawdown calculations, pending-node progress, and the browser's execution-summary helpers. Scope tests to `tests/`; the archived previous project has its own separate suite.

## Main Modules

```text
agent/graph.py                  LangGraph nodes and conditional edges
agent/state.py                  shared Agent state
agent/nodes.py                  nine node implementations
agent/tools/research.py         parallel collection and targeted retrieval
agent/tools/market_data.py      Yahoo, Google News, and SEC tools
agent/tools/polymarket.py       Gamma discovery, filtering, and ranking
agent/tools/memory.py           paragraph extraction and persistent RAG
agent/tools/quantitative.py     deterministic calculations
agent/tools/evidence.py         normalization, deduplication, and gate
agent/llm.py                    local Ollama structured output
server.py                       async run API and static web server
web/                            browser application
```

This application is for research and demonstration, not financial advice.
