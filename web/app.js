import { DEMO_REQUESTS, numericValue, stageStates, summarizeExecution, summarizePolymarket, summarizeEvidenceGate, summarizeVerification, modelTokenUsage, apiCostText, summarizeCounterfactual } from "./presentation.mjs?v=20261009-cfcompare1";

const NODE_ORDER = [
  "understand_request",
  "plan_research",
  "collect_evidence",
  "normalize_and_evidence_gate",
  "targeted_retrieval",
  "financial_quantitative_analysis",
  "generate_thesis_graph",
  "verify_and_calibrate",
  "build_report",
];

const NODE_LABELS = {
  understand_request: "Understand request",
  plan_research: "Plan research",
  collect_evidence: "Collect evidence",
  normalize_and_evidence_gate: "Normalize & gate",
  targeted_retrieval: "Targeted retrieval",
  financial_quantitative_analysis: "Financial & quantitative",
  generate_thesis_graph: "Generate thesis graph",
  verify_and_calibrate: "Verify & calibrate",
  build_report: "Build report",
  counterfactual_evidence_test: "Counterfactual Evidence Test",
};

const SOURCE_LABELS = {
  yahoo_finance: "Yahoo Finance",
  google_news: "Google News",
  polymarket: "Polymarket venue data",
  vector_db: "Historical vector DB",
  financial_statements: "SEC financial statements",
  price_history: "Price history",
};

const BENCHMARK_METRICS = [
  ["Task classification", data => data.task_classification_accuracy_pct, "%", "Correct task type / labelled questions"],
  ["Tool selection", data => data.tool_selection?.exact_match_accuracy_pct, "%", "Exact source-set match / labelled questions"],
  ["Critical evidence", data => data.critical_evidence_coverage_pct, "%", "Valid evidence types found / required types"],
  ["Invalid references", data => data.invalid_evidence_reference_rate_pct, "%", "Invalid evidence ID mentions / all cited IDs; lower is better"],
  ["Numeric traceability errors", data => data.numeric_error_rate_pct, "%", "Unsupported currency or percentage claims / measured claims; lower is better"],
  ["Completion", data => data.completion_rate_pct, "%", "Reports completed / submitted benchmark cases"],
  ["Average runtime", data => data.runtime?.average_ms, "ms", "End-to-end average per benchmark case"],
  ["Model calls", data => data.model_calls?.average_per_task, "calls / task", "Average model calls per benchmark question"],
  ["Token usage", data => data.token_and_cost?.average_tokens_per_task, "tokens / task", "Generation tokens per task; local Ollama has no metered API fee"],
];

function metricCard(label, value, unit, detail) {
  const hasValue = value !== null && value !== undefined && Number.isFinite(Number(value));
  const display = hasValue ? Number(value).toLocaleString(undefined, { maximumFractionDigits: unit === "USD" ? 6 : 2 }) : "N/A";
  return `<article class="evaluation-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(display)}${hasValue && unit === "%" ? "%" : ""}</strong><small>${escapeHtml(unit === "%" ? detail : `${unit} · ${detail}`)}</small></article>`;
}

function hideRunMetrics() {
  $("evaluationPanel").hidden = true;
  $("runMetrics").innerHTML = "";
  $("evaluationMetrics").innerHTML = "";
  $("serviceMetrics").innerHTML = "";
}

function showRunMetrics(report, replay = false) {
  const usage = report.observability?.model_calls || {};
  const elapsed = report.observability?.total_duration_ms;
  const evidence = report.evidence || [];
  $("runMetricsMeta").textContent = `${replay ? "Saved run" : "Just completed"} · ${formatTimestamp(report.generated_at)} · ${report.run_id || "Run ID unavailable"}`;
  $("runMetrics").innerHTML = [
    metricCard("Runtime", elapsed == null ? null : elapsed / 1000, "seconds", "End-to-end analysis time"),
    metricCard("Model calls", usage.total, "calls", "Calls recorded for this run"),
    metricCard("Token usage", usage.total_tokens, "tokens", "Recorded model token usage"),
    metricCard("API cost estimate", usage.metered_api_cost_usd, "USD", `${apiCostText(usage.metered_api_cost_usd, usage.api_cost_unreported_calls)} · Usage × recorded rates; excludes credits and taxes.`),
    metricCard("Evidence collected", evidence.length, "items", "Normalized evidence in this report"),
  ].join("");
  $("evaluationPanel").hidden = false;
  loadMetricsDashboard();
}

async function loadMetricsDashboard() {
  const button = $("refreshMetricsButton");
  button.disabled = true;
  const [evaluation, service] = await Promise.allSettled([
    fetch("/api/evaluation", { cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }),
    fetch("/api/metrics?limit=100", { cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }),
  ]);
  if (evaluation.status === "fulfilled" && evaluation.value.available) {
    const result = evaluation.value;
    const data = result.summary || {};
    $("evaluationMeta").textContent = `${data.case_count ?? 0} labelled cases · Measured ${formatTimestamp(result.generated_at)}`;
    $("evaluationMetrics").innerHTML = BENCHMARK_METRICS.map(([label, read, unit, detail]) => metricCard(label, read(data), unit, detail)).join("");
    const cost = data.token_and_cost || {};
    const equivalent = cost.estimated_equivalent_cost_usd;
    $("evaluationMetrics").insertAdjacentHTML("beforeend", metricCard("API-equivalent cost", equivalent, "USD / benchmark", "Only shown when token rates are configured; hardware cost is excluded"));
  } else {
    const message = evaluation.status === "fulfilled" ? evaluation.value.message : evaluation.reason?.message;
    $("evaluationMeta").textContent = message || "Benchmark unavailable";
    $("evaluationMetrics").innerHTML = BENCHMARK_METRICS.map(([label, , unit, detail]) => metricCard(label, null, unit, detail)).join("");
  }
  if (service.status === "fulfilled") {
    const data = service.value;
    $("serviceMetricsMeta").textContent = `Latest ${data.persisted_runs ?? 0} saved runs · ${data.active_runs ?? 0} active now`;
    $("serviceMetrics").innerHTML = [
      metricCard("Saved runs completed", data.completion_rate_pct, "%", `${data.completed_runs ?? 0} completed · ${data.failed_runs ?? 0} failed`),
      metricCard("Average runtime", data.average_runtime_ms, "ms", "Measured runs only; older reports predate instrumentation"),
      metricCard("Model calls", data.model_calls_total, "calls", "Across the saved runs with recorded usage"),
      metricCard("Tokens", data.tokens_total, "tokens", "Generation usage across measured saved runs"),
      metricCard("API cost estimate", data.metered_api_cost_usd, "USD", `Reported usage × recorded rates; ${data.api_cost_unreported_calls || 0} calls with unknown cost. Excludes credits and taxes.`),
    ].join("");
  } else {
    $("serviceMetricsMeta").textContent = `Service metrics unavailable: ${service.reason?.message || "request failed"}`;
    $("serviceMetrics").innerHTML = "";
  }
  button.disabled = false;
}

const $ = (id) => document.getElementById(id);
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

let priceTimer = null;
let priceController = null;
let queryWasEdited = false;
let lastAutoQuery = "";
let activeRunId = null;
let isBusy = false;
let displayedReport = null;
let elapsedTimer = null;
let historyLoading = false;
let counterfactualSettings = null;
let counterfactualChoiceEdited = false;

function updateCounterfactualChoiceStatus() {
  const selected = $("enableCounterfactualEvidenceTest").checked;
  const server = counterfactualSettings ? `Server default: ${counterfactualSettings.default_enabled ? "on" : "off"}. ` : "";
  $("counterfactualOptionStatus").textContent = selected
    ? `${server}Enabled for this analysis. ${counterfactualSettings ? `Up to ${counterfactualSettings.max_claims} main conclusion(s), ${counterfactualSettings.max_evidence} evidence removals per conclusion, and ${counterfactualSettings.max_added_model_calls} extra model calls within ${counterfactualSettings.budget_seconds}s.` : "Adds model calls."}`
    : `${server}Off for this analysis; no extra counterfactual model calls.`;
}

async function loadCounterfactualSettings() {
  try {
    const response = await fetch("/api/counterfactual/status", { cache: "no-store" });
    if (!response.ok) throw new Error("Counterfactual settings unavailable");
    counterfactualSettings = await response.json();
    if (!counterfactualChoiceEdited && !isBusy) $("enableCounterfactualEvidenceTest").checked = counterfactualSettings.default_enabled === true;
    updateCounterfactualChoiceStatus();
  } catch (_) {
    updateCounterfactualChoiceStatus();
    $("counterfactualOptionStatus").textContent += " Server settings unavailable; this analysis uses your selection.";
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function assetMonogram(assetName, symbol) {
  const ignored = new Set(["inc", "incorporated", "corp", "corporation", "ltd", "limited", "plc", "class"]);
  const words = String(assetName || symbol || "?")
    .replace(/[^a-z0-9]+/gi, " ")
    .trim()
    .split(/\s+/)
    .filter((word) => word && !ignored.has(word.toLowerCase()) && !/^\d+$/.test(word));
  return (words.slice(0, 2).map((word) => word[0]).join("") || "?").toUpperCase();
}

function assetTypeLabel(value) {
  return String(value || "asset").replaceAll("_", " ");
}

function formatPrice(value, currency = "USD") {
  const parsed = numericValue(value);
  if (parsed === null) return "Unavailable";
  const maximumFractionDigits = parsed >= 1000 ? 4 : parsed >= 1 ? 6 : 8;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency || "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits,
    }).format(parsed);
  } catch {
    return `${parsed.toLocaleString("en-US", { maximumFractionDigits })} ${currency || ""}`.trim();
  }
}

function formatPercent(value, digits = 1) {
  const parsed = numericValue(value);
  return parsed !== null ? `${parsed.toFixed(digits)}%` : "--";
}

function formatTimestamp(value) {
  if (!value) return "Unknown time";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function defaultQuestion() {
  const asset = $("asset").value.trim() || "the selected asset";
  const horizon = $("horizon").value || "the relevant horizon";
  const target = $("targetPrice").value.trim();
  const direction = $("targetCondition").value === "below" ? "at or below" : "at or above";
  const targetText = target ? ` Compare the latest quote with target ${target} (${direction}), then distinguish the current condition from the future outlook.` : "";
  return `Analyze the current situation, key drivers, risks, and outlook for ${asset} over ${horizon}.${targetText}`;
}

function refreshDefaultQuestion(force = false) {
  if (!force && queryWasEdited) return;
  lastAutoQuery = defaultQuestion();
  $("query").value = lastAutoQuery;
}

function setPriceLoading(message = "Fetching live price...") {
  const display = $("currentPriceDisplay");
  const asset = $("asset").value.trim() || "Asset";
  display.className = "price-display is-loading";
  display.innerHTML = `
    <div class="quote-card-head">
      <span class="quote-mark" aria-hidden="true">${escapeHtml(assetMonogram(asset, asset))}</span>
      <div class="quote-identity"><strong>${escapeHtml(asset)}</strong><small>Resolving company or symbol</small></div>
    </div>
    <div class="quote-card-value"><strong>${escapeHtml(message)}</strong><span>Yahoo Finance</span></div>`;
}

async function fetchPrice(assetInput) {
  const asset = String(assetInput || "").trim();
  if (!asset) return;
  if (priceController) priceController.abort();
  priceController = new AbortController();
  const controller = priceController;
  setPriceLoading();
  try {
    const response = await fetch(`/api/price?asset=${encodeURIComponent(asset)}`, {
      signal: controller.signal,
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`Price request failed (${response.status})`);
    const payload = await response.json();
    const resolved = payload.asset || {};
    const price = payload.price || {};
    if (controller !== priceController || asset !== $("asset").value.trim()) return;
    $("assetNameHint").textContent = `${resolved.asset_name || asset} · ${resolved.symbol || asset} · ${resolved.asset_type || "asset"}`;
    const display = $("currentPriceDisplay");
    if (price.success) {
      display.className = "price-display is-ready";
      const change = numericValue(price.return_1d_pct);
      const changeClass = change > 0 ? "positive" : change < 0 ? "negative" : "";
      const changeText = change !== null ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}% 1D` : "1-day move unavailable";
      display.innerHTML = `
        <div class="quote-card-head">
          <span class="quote-mark" aria-hidden="true">${escapeHtml(assetMonogram(resolved.asset_name, resolved.symbol))}</span>
          <div class="quote-identity">
            <strong>${escapeHtml(resolved.asset_name || asset)}</strong>
            <small>${escapeHtml(resolved.symbol || asset)} · ${escapeHtml(assetTypeLabel(resolved.asset_type))}</small>
          </div>
        </div>
        <div class="quote-card-value">
          <strong>${escapeHtml(formatPrice(price.price, price.currency))}</strong>
          <span class="quote-change ${changeClass}">${escapeHtml(changeText)}</span>
        </div>
        <div class="quote-card-foot"><span>Latest available quote</span><small>${escapeHtml(price.source || "Yahoo Finance")} · ${escapeHtml(formatTimestamp(price.timestamp))}</small></div>`;
    } else {
      display.className = "price-display is-error";
      display.innerHTML = `
        <div class="quote-card-head">
          <span class="quote-mark" aria-hidden="true">${escapeHtml(assetMonogram(resolved.asset_name, resolved.symbol))}</span>
          <div class="quote-identity"><strong>${escapeHtml(resolved.asset_name || asset)}</strong><small>${escapeHtml(resolved.symbol || asset)} · ${escapeHtml(assetTypeLabel(resolved.asset_type))}</small></div>
        </div>
        <div class="quote-card-error"><strong>Live price unavailable</strong><small>${escapeHtml(price.error || "Yahoo Finance did not return a quote")}</small></div>`;
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    if (controller !== priceController) return;
    const display = $("currentPriceDisplay");
    display.className = "price-display is-error";
    display.innerHTML = `<div class="quote-card-head"><span class="quote-mark" aria-hidden="true">!</span><div class="quote-identity"><strong>Price lookup failed</strong><small>${escapeHtml(asset)}</small></div></div><div class="quote-card-error"><small>${escapeHtml(error.message)}</small></div>`;
  }
}

function schedulePriceFetch() {
  clearTimeout(priceTimer);
  if (priceController) priceController.abort();
  setPriceLoading("Waiting for symbol...");
  priceTimer = setTimeout(() => fetchPrice($("asset").value), 450);
}

async function loadModels() {
  const select = $("llmModel");
  try {
    const response = await fetch("/api/models", { cache: "no-store" });
    const payload = await response.json();
    select.innerHTML = "";
    const auto = document.createElement("option");
    auto.value = "auto";
    const defaultLabel = (payload.models || []).find(model => model.name === payload.auto_selected)?.label || payload.auto_selected;
    auto.textContent = `Auto (${defaultLabel || "best available"})`;
    auto.disabled = payload.default_available === false;
    select.append(auto);
    for (const model of payload.models || []) {
      const option = document.createElement("option");
      option.value = model.name;
      option.textContent = `${model.label || model.name}${model.status === "missing_aws_credentials" ? " (AWS IAM role required)" : model.available === false ? " (unavailable)" : ""}`;
      option.disabled = model.available === false;
      select.append(option);
    }
    select.value = "auto";
    $("ollamaDot").classList.toggle("online", Boolean(payload.success));
    $("llmStatus").textContent = `Default: ${payload.auto_selected || "auto"}. ${payload.default_available === false ? "Default model unavailable; attach the AWS Bedrock IAM role or select Mac 8B. " : ""}${(payload.models || []).some(model => model.provider === "bedrock") ? "Bedrock status reflects AWS credential availability; IAM and model access are validated on each call." : payload.success ? "Ollama connected." : "Ollama unavailable."}`;
  } catch (error) {
    $("ollamaDot").classList.remove("online");
    $("llmStatus").textContent = `Model configuration check failed: ${error.message}.`;
  }
}

function updatePredictionProviderHint() {
  const provider = $("predictionProvider").value;
  $("predictionProviderHint").textContent = provider === "coinrithm"
    ? "Prediction data: Data by CoinRithm; original venue Polymarket. Not Gamma direct data."
    : "Prediction data: Gamma official direct. If access is blocked, no other provider will be used.";
  $("polyApiBase").textContent = provider === "coinrithm" ? "CoinRithm aggregation" : "https://gamma-api.polymarket.com";
  $("polyApiStatus").textContent = "Not checked for this selection. Check the selected provider or Gamma separately.";
  $("polyApiProbes").innerHTML = "";
  $("polyStatusDot").classList.remove("online", "blocked");
}

async function loadPredictionProviders() {
  try {
    const response = await fetch("/api/polymarket/providers");
    if (!response.ok) throw new Error("Provider settings unavailable");
    const settings = await response.json();
    if (["coinrithm", "gamma"].includes(settings.default)) $("predictionProvider").value = settings.default;
  } catch {
    // The form explicitly sends its visible provider even if defaults cannot load.
  }
  updatePredictionProviderHint();
}

async function loadPolymarketStatus(provider = $("predictionProvider").value) {
  const button = $("refreshPolymarketButton");
  button.disabled = true;
  $("checkGammaButton").disabled = true;
  $("polyApiStatus").textContent = `Testing ${provider === "gamma" ? "Gamma official endpoints" : "CoinRithm's published dataset"}...`;
  $("polyApiProbes").innerHTML = "";
  try {
    const response = await fetch(`/api/polymarket/status?provider=${encodeURIComponent(provider)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Status request failed (${response.status})`);
    $("polyApiBase").textContent = payload.base_url || "https://gamma-api.polymarket.com";
    $("polyStatusDot").classList.toggle("online", payload.status === "reachable");
    $("polyStatusDot").classList.toggle("blocked", payload.status === "access_blocked");
    $("polyApiStatus").textContent = `Checked provider: ${payload.provider || provider}. ${payload.message} This check does not change the provider selected for analysis.`;
    const probes = [...(payload.probes || []), payload.geoblock_probe].filter(Boolean);
    $("polyApiProbes").innerHTML = probes.map((probe) => {
      const state = probe.success ? "success" : probe.http_status === 451 ? "blocked" : "failed";
      const successDetail = probe.endpoint === "official-geoblock"
        ? `Blocked: ${probe.blocked ? "yes" : "no"}${probe.country ? ` · ${probe.country}${probe.region ? `-${probe.region}` : ""}` : ""} · ${probe.latency_ms ?? "--"} ms`
        : `${probe.event_count ?? 0} sample events · ${probe.latency_ms ?? "--"} ms`;
      const blockedDetail = [
        probe.error,
        probe.provider_error_code ? `provider code ${probe.provider_error_code}` : "",
        probe.edge_ray ? `edge ${probe.edge_ray}` : "",
      ].filter(Boolean).join(" · ");
      return `<div class="api-probe ${state}"><strong>${escapeHtml(probe.endpoint)}</strong><code>${escapeHtml(probe.path || "")}</code><span>HTTP ${escapeHtml(probe.http_status ?? "network error")}</span><small>${escapeHtml(probe.success ? successDetail : blockedDetail || "No response")}</small></div>`;
    }).join("");
  } catch (error) {
    $("polyStatusDot").classList.remove("online", "blocked");
    $("polyApiStatus").textContent = `Polymarket diagnostic failed: ${error.message}`;
    $("polyApiProbes").innerHTML = '<div class="empty-evidence">The application is configured, but the diagnostic endpoint did not complete.</div>';
  } finally {
    button.disabled = false;
    $("checkGammaButton").disabled = false;
  }
}

function renderTimeline(target, trace = [], status = "completed", currentNode = null) {
  const container = typeof target === "string" ? $(target) : target;
  const visits = {};
  const slowest = Math.max(1, ...trace.map(item => numericValue(item.duration_ms) ?? 0));
  const steps = trace.map((item, index) => {
    const visit = visits[item.node] = (visits[item.node] || 0) + 1;
    const duration = numericValue(item.duration_ms);
    const label = NODE_LABELS[item.node] || item.node;
    const repeat = visit > 1 ? `<em>Visit ${visit}</em>` : "";
    const bar = duration === null ? "" : `<span class="duration-track" aria-hidden="true"><i style="width:${Math.max(0, Math.min(100, duration / slowest * 100))}%"></i></span>`;
    return `<details class="execution-step ${item.node === "targeted_retrieval" ? "is-retrieval" : ""}"><summary><span class="execution-index">${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(label)} ${repeat}</strong><small>${duration === null ? "Time unavailable" : `${(duration / 1000).toFixed(2)} s`}</small>${bar}</summary><p>${escapeHtml(item.summary || "No summary recorded for this node.")}</p></details>`;
  });
  if (["running", "queued"].includes(status) && currentNode && currentNode !== "END") {
    steps.push(`<div class="execution-step is-active" aria-current="step"><span class="execution-index">${String(trace.length + 1).padStart(2, "0")}</span><strong>${escapeHtml(NODE_LABELS[currentNode] || currentNode)}</strong><small>${status === "queued" ? "Waiting" : "Running"}</small></div>`);
  }
  container.innerHTML = steps.join("") || '<div class="empty-evidence">Execution order was not recorded in this saved report.</div>';
}

const EVIDENCE_LABELS = {
  market_price: "Current quote", price_history: "Price history", financial_statement: "Financial statements",
  news: "Recent news", historical_news: "Historical news", crowd_expectation: "Market expectations", user_context: "User context",
};
const CHECK_LABELS = {
  evidence_ids_exist: ["Evidence IDs resolve", "Referenced IDs exist in the evidence bundle."],
  claims_have_support: ["Claims include references", "Checks for required references; does not judge semantic support."],
  chronology_valid: ["Evidence respects the cutoff", "Checks timestamps against the analysis cutoff."],
  yahoo_not_causal: ["Price data is not used as a cause", "Flags configured causal wording supported only by price data."],
  polymarket_is_expectation: ["Market odds remain expectations", "Flags configured wording that treats market odds as certainty."],
  numbers_calculated_by_python: ["Numbers are traceable", "Checks supported amounts and percentages within a bounded tolerance."],
  target_condition_consistent: ["Target summary is consistent", "Compares the summary with the deterministic target assessment."],
};
const ISSUE_LABELS = {
  unverified_numeric_claim_repaired: "Untraceable numeric claim replaced",
  invalid_evidence_id: "Unknown evidence reference detected",
  unsupported_claim: "Claim without required references detected",
  momentum_used_as_fundamental_cause: "Price momentum used as a causal explanation",
  polymarket_treated_as_fact: "Market expectation presented as fact",
  target_summary_repaired: "Target summary corrected",
  future_dated_evidence: "Evidence after the cutoff detected",
};
const evidenceLabel = type => EVIDENCE_LABELS[type] || String(type).replaceAll("_", " ");

function checkpointMarkup(rounds = []) {
  return rounds.length ? rounds.map((round, index) => {
    let title, detail, searches = "";
    if (round.kind === "gate") {
      title = `Evidence check · ${round.coverage === null ? "coverage unavailable" : `${round.coverage}% of required types`}`;
      const missing = round.missing.map(evidenceLabel).join(", ");
      const reason = missing && round.reason?.startsWith("Missing critical evidence:") ? "Requested targeted retrieval." : round.reason;
      detail = [missing ? `Missing: ${missing}.` : "", reason].filter(Boolean).join(" ");
    } else if (round.kind === "retrieval") {
      title = `Targeted retrieval${round.attempt == null ? "" : ` · attempt ${round.attempt}`}`;
      detail = "Additional searches selected to address an evidence or verification gap.";
      if (round.searches.length) searches = `<details class="checkpoint-searches"><summary>${round.searches.length} search instructions</summary><ul>${round.searches.map(search => `<li><strong>${escapeHtml(SOURCE_LABELS[search.source] || search.source || "Source unavailable")}</strong><p>${escapeHtml(search.query || "Query unavailable")}</p>${search.reason ? `<small>${escapeHtml(search.reason)}</small>` : ""}</li>`).join("")}</ul></details>`;
    } else {
      title = "Verification checkpoint";
      detail = `${round.issues.length} issue records.${round.route === "targeted_retrieval" ? " Requested another retrieval pass." : round.route === "build_report" ? " Continued to the report." : ""}`;
    }
    return `<article class="checkpoint ${round.kind}"><span class="execution-index">${String(index + 1).padStart(2, "0")}</span><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail || "No decision explanation recorded.")}</p>${searches}</div></article>`;
  }).join("") : '<div class="empty-evidence">No checkpoint history recorded. New runs show evidence checks and any retrieval decisions here.</div>';
}

function renderEvidenceInspection(report) {
  const gate = summarizeEvidenceGate(report);
  $("evidenceGateSummary").textContent = gate.recorded
    ? `${gate.coverage === null ? "Coverage unavailable" : `${gate.coverage}% of required evidence types present`}. ${gate.reason || "No final gate explanation recorded."}`
    : "This saved report has no evidence gate record.";
  $("evidenceCoverage").innerHTML = gate.critical.length ? gate.critical.map(item => {
    const status = item.present === true ? "present" : item.present === false ? "missing" : "unknown";
    const counts = item.validCount === null ? "Item count unavailable" : `${item.validCount} valid item${item.validCount === 1 ? "" : "s"}${item.excludedCount ? ` · ${item.excludedCount} after-cutoff item${item.excludedCount === 1 ? "" : "s"} excluded` : ""}`;
    return `<div class="coverage-row ${status}"><strong>${escapeHtml(evidenceLabel(item.type))}</strong><span>${escapeHtml(counts)}</span><small>${status === "present" ? "Present" : status === "missing" ? "Missing" : "Not recorded"}</small></div>`;
  }).join("") : '<div class="empty-evidence">Required evidence types were not recorded.</div>';
  $("evidenceCheckpointHistory").innerHTML = checkpointMarkup(gate.rounds);
  const verification = summarizeVerification(report);
  $("verificationChecks").innerHTML = verification.checks.length ? verification.checks.map(check => {
    const [label, detail] = CHECK_LABELS[check.key] || [String(check.key).replaceAll("_", " "), "Recorded rule check."];
    const statusLabel = { pass: "Passed", fail: "Flagged", not_applicable: "No target set", unknown: "Not recorded" }[check.status];
    return `<article class="verification-check ${escapeHtml(check.status)}"><div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(statusLabel)}</span></div><p>${escapeHtml(detail)}</p></article>`;
  }).join("") : '<div class="empty-evidence">Individual verification checks were not recorded.</div>';
  const repairNote = verification.ruleRepairCount ? `<p class="field-hint">${verification.ruleRepairCount} generation pass${verification.ruleRepairCount === 1 ? "" : "es"} recorded a rule adjustment before verification. See intermediate snapshots for the original and adjusted outputs.</p>` : "";
  $("verificationIssues").innerHTML = repairNote + (verification.issues.length
    ? `<h4>Final verification issue records</h4><ul>${verification.issues.map(issue => {
      const locations = [issue.branch, issue.section, issue.claim, ...(issue.details || []).map(detail => `${detail.path || "Numeric claim"}${detail.value == null ? "" : `: ${detail.value}`}`)].filter(Boolean);
      return `<li><strong>${escapeHtml(ISSUE_LABELS[issue.type] || String(issue.type || "Recorded issue").replaceAll("_", " "))}</strong>${locations.length ? `<p>${escapeHtml(locations.join(" · "))}</p>` : ""}${issue.evidence_ids?.length ? evidenceReferences(issue.evidence_ids) : ""}</li>`;
    }).join("")}</ul>`
    : `<p class="field-hint">${verification.recorded ? "No issue records in the final verification pass." : "Verification details unavailable for this report."}</p>`);
}

function evidenceReferences(ids = []) {
  if (!ids.length) return "";
  const known = new Set((displayedReport?.report.evidence || []).map(item => item.id));
  return `<div class="evidence-id-row citation-row">${[...new Set(ids)].map(id => `<button class="evidence-reference" type="button" data-evidence-id="${escapeHtml(id)}" aria-label="Inspect evidence ${escapeHtml(id)}" ${known.has(id) ? "" : 'disabled title="Evidence not available in this report"'}>${escapeHtml(id)}</button>`).join("")}</div>`;
}

function openReferencedEvidence(id) {
  const evidence = (displayedReport?.report.evidence || []).find(item => item.id === id);
  if (!evidence) return;
  $("evidenceDialogTitle").textContent = `Evidence ${id}`;
  const mode = evidence.evidence_type === "crowd_expectation" ? "polymarket" : evidence.evidence_type === "historical_news" ? "memory" : "default";
  $("evidenceDialogContent").innerHTML = renderEvidenceItem(evidence, mode) + `<p class="field-hint">Type: ${escapeHtml(evidenceLabel(evidence.evidence_type))} · ${evidence.temporal_valid === false ? "After the analysis cutoff; excluded from gate coverage" : "Not flagged as after the analysis cutoff"}</p>`;
  if (!$("evidenceDialog").open) $("evidenceDialog").showModal();
}

function renderLiveSources(statuses = {}) {
  const entries = Object.entries(statuses);
  $("liveSourceStatus").innerHTML = entries.length
    ? entries.map(([name, item]) => `<span class="source-chip ${escapeHtml(item.status || "failed")}">${escapeHtml(name.replaceAll("_", " "))}: ${escapeHtml(item.status || "unknown")}</span>`).join("")
    : '<span class="source-chip pending">Sources start in Node 3</span>';
}

function makerBadges(makers = []) {
  return makers.map((maker) => `<span class="maker-badge ${escapeHtml(String(maker).toLowerCase())}">${escapeHtml(maker)}</span>`).join("");
}

function renderLiveAudit(run = {}) {
  const decisions = run.decision_audit || [];
  const latest = decisions.at(-1);
  if (latest) {
    const nextNode = latest.decision?.next_node || latest.decision?.route || latest.decision?.gate_decision || "next node";
    $("liveDecisionAudit").innerHTML = `<div class="live-decision-head"><strong>${escapeHtml(NODE_LABELS[latest.node] || latest.node)}</strong><div>${makerBadges(latest.decision_makers || [])}</div></div><p>${escapeHtml(String(latest.decision_mode || "recorded decision").replaceAll("_", " "))}</p><span>Next node: ${escapeHtml(nextNode)}</span>`;
  } else {
    $("liveDecisionAudit").textContent = "Waiting for the first node...";
  }
  const calls = run.tool_calls || [];
  $("liveToolCalls").innerHTML = calls.length
    ? calls.slice(-8).map((call) => `<span class="source-chip ${call.success ? "success" : "failed"}">${escapeHtml(call.function || call.call_key)} · ${escapeHtml(call.status || "unknown")}</span>`).join(" ")
    : "<span>None yet</span>";
}

function beginRunUi() {
  setBusy(true);
  displayedReport = null;
  hideRunMetrics();
  clearInterval(elapsedTimer);
  const started = Date.now();
  $("runElapsed").textContent = "0s elapsed";
  elapsedTimer = setInterval(() => {
    $("runElapsed").textContent = `${Math.floor((Date.now() - started) / 1000)}s elapsed`;
  }, 1000);
  $("resultEmpty").classList.add("hidden");
  $("resultContent").classList.remove("hidden");
  $("reportContent").classList.add("hidden");
  $("runProgress").classList.remove("hidden");
  $("analysisStateBanner").className = "analysis-state-banner running";
  $("analysisStateBanner").textContent = "Autonomous analysis is running. This panel updates after every LangGraph node.";
  $("statusPill").className = "status-pill mixed";
  $("statusPill").textContent = "Agent running";
  $("analyzeButton").disabled = true;
  $("analyzeButton").classList.add("is-running");
  $("analyzeButton").querySelector(".button-label").textContent = "Agent is analyzing...";
  $("runningTitle").textContent = "Agent is working";
  $("runningMessage").textContent = "Understanding your question with the selected model...";
  renderTimeline("nodeTimeline", [], "running", "understand_request");
  renderStages([], "understand_request");
  renderLiveSources({});
  renderLiveAudit({});
  $("liveEvidenceProgress").innerHTML = '<div class="empty-evidence">Waiting for collection and the first evidence check.</div>';
}

function finishRunUi(success, message) {
  clearInterval(elapsedTimer);
  setBusy(false);
  $("analyzeButton").disabled = false;
  $("analyzeButton").classList.remove("is-running");
  $("analyzeButton").querySelector(".button-label").textContent = "Run autonomous analysis";
  if (success) {
    $("runProgress").classList.add("hidden");
    $("analysisStateBanner").className = "analysis-state-banner done";
    $("analysisStateBanner").textContent = message || "Report ready. Inspect evidence checks below.";
    $("statusPill").className = "status-pill bullish";
    $("statusPill").textContent = "Report ready";
  } else {
    $("runProgress").classList.add("hidden");
    $("analysisStateBanner").className = "analysis-state-banner stale";
    $("analysisStateBanner").textContent = message || "The run failed.";
    $("statusPill").className = "status-pill bearish";
    $("statusPill").textContent = "Run failed";
  }
}

function updateRunProgress(run) {
  const trace = run.node_trace || [];
  $("runningTitle").textContent = run.current_node ? NODE_LABELS[run.current_node] || run.current_node : "Agent is working";
  $("runningMessage").textContent = runProgressMessage(run, "Waiting for the next node update...");
  renderTimeline("nodeTimeline", trace, run.status, run.current_node);
  renderStages(trace, run.current_node, run.status);
  renderLiveSources(run.source_status || {});
  renderLiveAudit(run);
  const checkpoints = summarizeEvidenceGate({
    evidence_gate: run.evidence_gate,
    agent_audit: { intermediate_results: run.intermediate_results || [], decision_audit: run.decision_audit || [] },
  }).rounds;
  $("liveEvidenceProgress").innerHTML = checkpoints.length ? checkpointMarkup(checkpoints)
    : '<div class="empty-evidence">Waiting for collection and the first evidence check.</div>';
  if (run.evidence_gate?.decision === "retrieve_more") {
    $("analysisStateBanner").textContent = `Evidence gate requested targeted retrieval. Attempt ${run.retrieval_attempts || 0} is bounded by the retry limit.`;
  }
}

function renderStages(trace, currentNode, status = "running") {
  $("stageTimeline").innerHTML = stageStates(trace, currentNode, status).map((stage, index) =>
    `<div class="stage ${stage.status}" ${stage.status === "is-active" ? 'aria-current="step"' : ""}><span>0${index + 1}</span><strong>${stage.label}</strong></div>`
  ).join("");
}

function setBusy(value) {
  isBusy = value;
  document.querySelectorAll("#analysisForm input, #analysisForm select, #analysisForm textarea, #analysisForm button, .demo-request, #resetButton, #savedRunSelect").forEach(element => {
    element.disabled = value;
  });
  $("replayButton").disabled = value || historyLoading || !$("savedRunSelect").value;
}

async function pollRun(runId) {
  for (let attempt = 0; attempt < 900; attempt += 1) {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}?poll=${attempt}`, { cache: "no-store" });
    const run = await response.json();
    if (!response.ok) throw new Error(run.error || `Run status failed (${response.status})`);
    updateRunProgress(run);
    if (run.status === "completed") return run;
    if (run.status === "failed") throw new Error(run.error || run.message || "Agent run failed");
    if (attempt > 0 && attempt % 30 === 0) {
      $("runningMessage").textContent = `${runProgressMessage(run, "Waiting for the Agent...")} · Poll ${attempt}/900`;
    }
    await delay(800);
  }
  throw new Error("The run exceeded the browser polling limit.");
}

function streamRun(runId) {
  if (!("EventSource" in window)) return pollRun(runId);
  return new Promise((resolve, reject) => {
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
    let settled = false;
    let receivedEvent = false;
    let fallbackStarted = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      source.close();
      callback(value);
    };
    const startPollingFallback = () => {
      if (settled || fallbackStarted) return;
      fallbackStarted = true;
      source.close();
      pollRun(runId).then(value => finish(resolve, value)).catch(error => finish(reject, error));
    };
    const parse = (event) => {
      receivedEvent = true;
      return JSON.parse(event.data);
    };
    source.addEventListener("run.progress", (event) => {
      try {
        updateRunProgress(parse(event));
      } catch (error) {
        finish(reject, error);
      }
    });
    source.addEventListener("run.completed", (event) => {
      try {
        const run = parse(event);
        updateRunProgress(run);
        finish(resolve, run);
      } catch (error) {
        finish(reject, error);
      }
    });
    source.addEventListener("run.failed", (event) => {
      try {
        const run = parse(event);
        finish(reject, new Error(run.error || run.message || "Agent run failed"));
      } catch (error) {
        finish(reject, error);
      }
    });
    source.addEventListener("run.missing", (event) => {
      const run = parse(event);
      finish(reject, new Error(run.error || "Run not found"));
    });
    source.onerror = () => {
      startPollingFallback();
    };
    setTimeout(() => {
      if (!settled && !receivedEvent) {
        startPollingFallback();
      }
    }, 5000);
  });
}

function scoreCard(label, value, explanation, conflict = false) {
  const parsed = numericValue(value);
  const score = Math.max(0, Math.min(100, parsed ?? 0));
  return `<article class="quality-card ${conflict ? "is-conflict" : ""}">
    <div class="score-ring" style="--score:${score}"><strong>${parsed === null ? "--" : score}</strong></div>
    <div><h4>${escapeHtml(label)}</h4><p>${escapeHtml(explanation || "")}</p></div>
  </article>`;
}

function renderEvidenceItem(item, mode = "default") {
  const metadata = item.metadata || {};
  const link = item.source_url && /^https?:\/\//i.test(item.source_url)
    ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">Open source</a>`
    : "";
  let metrics = "";
  if (mode === "polymarket") {
    const probability = numericValue(metadata.probability);
    const probabilityText = probability !== null ? `${(probability * 100).toFixed(1)}% ${metadata.probability_outcome || ""}` : "Probability unavailable";
    const levels = (metadata.matched_thresholds || []).map((value) => formatPrice(value)).join(", ");
    const distance = metadata.target_distance ?? metadata.current_distance;
    metrics = `<div class="evidence-metrics"><span>${escapeHtml(probabilityText)}</span><span>Price relevance ${escapeHtml(metadata.price_relevance ?? 0)}%</span>${levels ? `<span>Levels ${escapeHtml(levels)}</span>` : ""}${Number.isFinite(Number(distance)) ? `<span>Nearest distance ${escapeHtml(formatPrice(distance))}</span>` : ""}</div>`;
    if (metadata.provider === "coinrithm") metrics += '<p class="field-hint"><a href="https://www.coinrithm.com/en/prediction-markets/api" target="_blank" rel="noreferrer">Data by CoinRithm</a> · Original venue: Polymarket · Aggregated quote, not Gamma direct.</p>';
  } else if (mode === "memory") {
    metrics = `<div class="evidence-metrics"><span>Cosine similarity ${escapeHtml(metadata.similarity ?? "--")}</span><span>Document ${escapeHtml(metadata.document_id ?? "--")}</span></div>`;
  }
  return `<article class="evidence-item">
    <div class="evidence-item-top"><span class="evidence-id">${escapeHtml(item.id || "")}</span><span class="freshness ${escapeHtml(item.freshness || "unknown")}">${escapeHtml(item.freshness || "unknown")}</span></div>
    <h4>${escapeHtml(item.title || "Untitled evidence")}</h4>
    <p>${escapeHtml(item.content || "")}</p>
    ${metrics}
    <div class="evidence-footer"><span>${escapeHtml(item.source || "Unknown source")}</span><span>${escapeHtml(formatTimestamp(item.timestamp))}</span>${link}</div>
  </article>`;
}

function renderEmpty(container, message) {
  container.innerHTML = `<div class="empty-evidence">${escapeHtml(message)}</div>`;
}

function renderBranch(prefix, branch = {}) {
  $(`${prefix}Scenario`).textContent = branch.scenario || "No scenario was generated.";
  const chainContainer = $(`${prefix}Chain`);
  const chain = branch.chain || [];
  chainContainer.innerHTML = chain.length
    ? chain.map((step, index) => `<div class="chain-step"><span class="chain-index">${index + 1}</span><div><strong>${escapeHtml(step.claim || "")}</strong><p>${escapeHtml(step.transmission_mechanism || "")}</p>${evidenceReferences(step.evidence_ids || [])}</div></div>`).join("")
    : '<div class="empty-evidence">This branch is intentionally not asserted by the available evidence.</div>';
  const meta = $(`${prefix}Meta`);
  const groups = [
    ["Weak links", branch.weak_links || []],
    ["Future triggers", branch.triggers || []],
    ["Invalidation", branch.invalidation_conditions || []],
  ];
  meta.innerHTML = groups.map(([label, items]) => `<div><strong>${escapeHtml(label)}</strong><ul>${items.length ? items.map((item) => `<li>${escapeHtml(item)}</li>`).join("") : "<li>None stated</li>"}</ul></div>`).join("");
}

function renderSourceStatus(statuses = {}) {
  const rows = Object.entries(statuses);
  $("sourceStatusTable").innerHTML = rows.length
    ? rows.map(([name, item]) => {
      const diagnostics = [
        item.blocking_layer ? `blocked at ${String(item.blocking_layer).replaceAll("_", " ")}` : "",
        ...(item.provider_error_codes || []).map((code) => `provider code ${code}`),
        item.error || "",
      ].filter(Boolean).join(" · ");
      return `<div class="source-row"><div><strong>${escapeHtml(name.replaceAll("_", " "))}</strong><span>${escapeHtml(item.source || "")}</span></div><span class="source-chip ${escapeHtml(item.status || "failed")}">${escapeHtml(item.status || "unknown")}</span><span>${escapeHtml(String(item.record_count ?? 0))} records</span><span>${escapeHtml(String(item.latency_ms ?? "--"))} ms</span>${diagnostics ? `<small>${escapeHtml(diagnostics)}</small>` : ""}</div>`;
    }).join("")
    : '<div class="empty-evidence">No source status was recorded.</div>';
}

function renderResearchSourcePlan(plan = {}) {
  const rationale = plan.source_rationale || {};
  const sources = Object.keys(SOURCE_LABELS);
  const selected = plan.sources || [];
  const routeSignature = plan.route_signature || `${String(plan.task_type || "research").replaceAll("_", " ")}: ${selected.join(" + ") || "no external source"}`;
  const routeCard = `<div class="resolved-route"><div><span>Initial task route</span><strong>${escapeHtml(routeSignature.replaceAll("_", " "))}</strong></div><small>${escapeHtml(selected.length)} selected · ${escapeHtml((plan.skipped_sources || []).length)} skipped in the first round</small></div>`;
  const sourceCards = sources.map((source) => {
    const item = rationale[source] || {};
    const isSelected = selected.includes(source) || item.selected;
    const rejected = item.llm_proposed_but_rejected;
    const origins = (item.origin || []).map((origin) => `<span>${escapeHtml(String(origin).replaceAll("_", " "))}</span>`).join("");
    return `<article class="source-plan-card ${isSelected ? "selected" : "skipped"}"><div><strong>${escapeHtml(SOURCE_LABELS[source])}</strong><span class="source-chip ${isSelected ? "success" : "not_selected"}">${isSelected ? "selected" : "skipped"}</span></div><p>${escapeHtml(item.reason || "Not required by this request.")}</p>${origins ? `<div class="source-origin-row">${origins}</div>` : ""}${rejected ? `<small>LLM proposed it, but deterministic relevance rules rejected the call.</small>` : ""}</article>`;
  }).join("");
  $("researchSourcePlan").innerHTML = routeCard + sourceCards;
}

function renderDecisionAudit(records = [], proof = {}) {
  const counts = `${proof.actual_nodes_executed?.length ?? records.length} node executions · ${proof.decision_records ?? records.length} decisions · ${proof.tool_calls ?? 0} tool calls`;
  $("agentProofText").textContent = `${counts}. ${proof.observable_scope || "Each executed LangGraph node records its input snapshot, decision mechanism, applied rules, route, and output snapshot."}`;
  $("decisionAudit").innerHTML = records.length
    ? records.map((record, index) => {
      const nextNode = record.decision?.next_node || record.decision?.route || record.decision?.gate_decision || "--";
      const rules = record.rules_applied || [];
      return `<article class="decision-card">
        <div class="decision-card-head"><span class="audit-index">${String(index + 1).padStart(2, "0")}</span><div><strong>${escapeHtml(NODE_LABELS[record.node] || record.node)}</strong><p>${escapeHtml(String(record.decision_mode || "").replaceAll("_", " "))}</p></div><div class="maker-row">${makerBadges(record.decision_makers || [])}</div></div>
        <div class="decision-route"><span>Decision</span><strong>${escapeHtml(JSON.stringify(record.decision || {}))}</strong><span>Next node</span><strong>${escapeHtml(nextNode)}</strong></div>
        <ul class="audit-rule-list">${rules.length ? rules.map((rule) => `<li>${escapeHtml(rule)}</li>`).join("") : "<li>No separate rule was recorded.</li>"}</ul>
        <div class="audit-snapshot-grid"><details><summary>Input snapshot</summary><pre>${escapeHtml(JSON.stringify(record.input_snapshot || {}, null, 2))}</pre></details><details><summary>Output snapshot</summary><pre>${escapeHtml(JSON.stringify(record.output_snapshot || {}, null, 2))}</pre></details></div>
      </article>`;
    }).join("")
    : '<div class="empty-evidence">No node decision records are available.</div>';
}

function renderToolCallAudit(calls = []) {
  $("toolCallAudit").innerHTML = calls.length
    ? calls.map((call, index) => {
      const link = call.source_url && /^https?:\/\//i.test(call.source_url) ? `<a href="${escapeHtml(call.source_url)}" target="_blank" rel="noreferrer">Source endpoint</a>` : "";
      return `<article class="tool-call-card">
        <div class="tool-call-head"><span class="audit-index">${String(index + 1).padStart(2, "0")}</span><div><strong>${escapeHtml(call.function || call.call_key || "Tool")}</strong><p>${escapeHtml(NODE_LABELS[call.node] || call.node || "")}</p></div><span class="source-chip ${call.success ? "success" : "failed"}">${escapeHtml(call.status || "unknown")}</span></div>
        <div class="tool-call-meta"><span>${escapeHtml(call.record_count ?? 0)} records</span><span>${escapeHtml(call.latency_ms ?? "--")} ms</span><span>${escapeHtml(formatTimestamp(call.started_at))}</span>${link}</div>
        <div class="audit-snapshot-grid"><details><summary>Function arguments</summary><pre>${escapeHtml(JSON.stringify(call.arguments || {}, null, 2))}</pre></details><details><summary>Result preview</summary><pre>${escapeHtml(JSON.stringify(call.result_preview || {}, null, 2))}</pre></details></div>
        ${call.error ? `<small class="audit-error">${escapeHtml(`${call.error_code ? `${call.error_code}: ` : ""}${call.error}`)}</small>` : ""}
      </article>`;
    }).join("")
    : '<div class="empty-evidence">No external or memory tool was called in this run.</div>';
}

function renderIntermediateResults(records = []) {
  $("intermediateResults").innerHTML = records.length
    ? records.map((record, index) => `<details class="intermediate-record"><summary><span class="audit-index">${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(NODE_LABELS[record.node] || record.node)}</strong><small>${escapeHtml(formatTimestamp(record.recorded_at))}</small></summary><pre>${escapeHtml(JSON.stringify(record.output || {}, null, 2))}</pre></details>`).join("")
    : '<div class="empty-evidence">No intermediate state snapshots are available.</div>';
}

function renderLlmCalls(llm = {}) {
  $("llmInputExplanation").textContent = llm.input_explanation || "";
  const calls = llm.calls || [];
  const usages = calls.map(modelTokenUsage);
  const measured = usages.filter(usage => usage.measured);
  const inputTotal = measured.reduce((sum, usage) => sum + usage.input, 0);
  const outputTotal = measured.reduce((sum, usage) => sum + usage.output, 0);
  $("llmTokenSummary").textContent = measured.length
    ? `${inputTotal.toLocaleString()} input + ${outputTotal.toLocaleString()} output = ${(inputTotal + outputTotal).toLocaleString()} recorded generation tokens across ${measured.length} of ${calls.length} calls. Repeated prompt content counts again; embedding usage is excluded.`
    : "Input/output token usage was not recorded. Generation usage excludes embeddings.";
  const tokenText = value => numericValue(value) === null ? "N/A" : Number(value).toLocaleString();
  $("llmCalls").innerHTML = calls.length
    ? calls.map((call, index) => `<article class="llm-call"><div><strong>${index + 1}. ${escapeHtml(NODE_LABELS[call.node] || call.node || "LLM step")}</strong><span class="source-chip ${call.success ? "success" : "failed"}">${call.success ? "structured output" : "fallback"}</span></div><p>${escapeHtml(call.purpose || "")}</p><dl><div><dt>Model</dt><dd>${escapeHtml(call.model || "--")}</dd></div><div><dt>Latency</dt><dd>${escapeHtml(call.latency_ms ?? "--")} ms</dd></div><div><dt>Input tokens</dt><dd>${tokenText(usages[index].input)}</dd></div><div><dt>Output tokens</dt><dd>${tokenText(usages[index].output)}</dd></div><div><dt>Total tokens</dt><dd>${tokenText(usages[index].total)}</dd></div><div><dt>API cost estimate</dt><dd>${escapeHtml(apiCostText(call.usage?.metered_api_cost_usd, ["bedrock", "qwen_api"].includes(call.usage?.provider) && call.usage?.metered_api_cost_usd == null ? 1 : 0))}</dd></div></dl>${call.usage?.pricing ? `<small>Rates per 1M tokens: $${escapeHtml(call.usage.pricing.input_usd_per_million_tokens)} input / $${escapeHtml(call.usage.pricing.output_usd_per_million_tokens)} output · ${escapeHtml(call.usage.pricing.mode)}</small>` : ""}<strong class="audit-subtitle">Input manifest</strong><pre>${escapeHtml(JSON.stringify(call.input_manifest || {}, null, 2))}</pre>${call.prompt_preview ? `<details class="llm-prompt-preview"><summary>Actual prompt sent to model</summary><pre>${escapeHtml(call.prompt_preview)}</pre></details>` : ""}${call.error ? `<small>${escapeHtml(call.error)}</small>` : ""}</article>`).join("")
    : '<div class="empty-evidence">No LLM call record is available.</div>';
}

function renderExecutionBrief(report) {
  const summary = summarizeExecution(report);
  const applicableChecks = summarizeVerification(report).checks.filter(check => ["pass", "fail"].includes(check.status));
  const humanize = value => String(value).replaceAll("_", " ");
  const embedding = summary.embedding;
  const embeddingText = embedding
    ? embedding.backend === "local_hash" ? "Hash-vector fallback, not a semantic embedding model."
      : `${embedding.backend || embedding.mode || "embedding"} · ${embedding.model || "see tool result"}`
    : summary.memoryRead ? "Embedding details available in the saved tool result, when recorded." : "Memory not queried for this task.";
  const metrics = [
    ["Tool calls", `${summary.toolSuccess} / ${summary.calls.length} succeeded`, "Actual calls, including retries and memory writes."],
    ["LLM", `${summary.llmSuccess} / ${summary.llmCalls.length} succeeded`, `${summary.failedLlm} fallback calls · ${summary.repaired} thesis repair passes`],
    ["Historical RAG", `${summary.memoryUsed} / 3 chunks used`, embeddingText],
    ["Feedback loop", `${summary.retryCount} retrieval retries`, applicableChecks.length ? `${applicableChecks.filter(check => check.status === "pass").length} / ${applicableChecks.length} applicable final checks passed` : "Individual checks not recorded"],
    ["Runtime", report.observability?.total_duration_ms != null ? `${(report.observability.total_duration_ms / 1000).toFixed(1)} s` : "Unavailable", `${report.observability?.node_executions ?? 0} node executions`],
    ["Model usage", `${report.observability?.model_calls?.total_tokens ?? 0} tokens`, `${report.observability?.model_calls?.total ?? summary.llmCalls.length} calls · ${apiCostText(report.observability?.model_calls?.metered_api_cost_usd, report.observability?.model_calls?.api_cost_unreported_calls)} API cost estimate`],
  ];
  $("executionMetrics").innerHTML = metrics.map(([label, value, detail]) =>
    `<div class="execution-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></div>`
  ).join("");
  const decisions = [];
  if (summary.override) decisions.push(`Task corrected: ${humanize(summary.override.llm_task_type)} → ${humanize(summary.override.rule_task_type)} by the task rule.`);
  if (summary.rejected.length) decisions.push(`Initial plan skipped ${summary.rejected.map(name => SOURCE_LABELS[name] || name).join(", ")} despite an LLM proposal. This planning decision is not an API failure.`);
  if (!decisions.length) decisions.push(`Initial plan: ${(report.research_plan?.sources || []).map(name => SOURCE_LABELS[name] || name).join(", ") || "no source plan recorded"}.`);
  $("decisionHighlight").textContent = decisions.join(" ");
  $("actualTools").innerHTML = summary.calls.length ? summary.calls.map(call =>
    `<span class="source-chip ${escapeHtml(call.status || (call.success ? "success" : "failed"))}">${escapeHtml(call.function || call.call_key)} · ${escapeHtml(call.status || "unknown")}</span>`
  ).join(" ") : '<span class="field-hint">No tool call records available.</span>';
  const polymarket = summarizePolymarket(report);
  $("polymarketRunStatus").className = `polymarket-run-status ${polymarket.status}`;
  $("polymarketRunStatus").innerHTML = `<strong>Polymarket: ${escapeHtml(polymarket.title)}</strong><span>${escapeHtml(polymarket.message)}</span>`;
  const notices = [];
  if (summary.failedLlm) notices.push("Some model steps used deterministic fallbacks.");
  if (summary.repaired) notices.push("Rule repair changed thesis output; inspect the node audit.");
  if (summary.failedSources.length) notices.push(`Source failures: ${summary.failedSources.map(humanize).join(", ")}.`);
  if (summary.missing.length) notices.push(`Missing critical evidence: ${summary.missing.join(", ")}.`);
  if (summary.failedChecks.length) notices.push(`Checks needing attention: ${summary.failedChecks.map(humanize).join(", ")}.`);
  if (summary.issues.length) notices.push(`${summary.issues.length} verification issue(s) recorded, including any repairs.`);
  $("verificationNotice").textContent = notices.join(" ") || "No failure was recorded in these checks. Evidence IDs and rule scores do not establish that every conclusion is correct.";
}

function renderReport(report, trace = [], { replay = false } = {}) {
  displayedReport = { report, replay };
  showRunMetrics(report, replay);
  const task = report.task_summary || {};
  const situation = report.current_situation || {};
  const scores = report.confidence_scores || {};
  const evidence = report.evidence || [];
  $("reportContent").classList.remove("hidden");
  $("currentView").textContent = situation.view || "No current view generated.";
  $("qualityBadge").textContent = `${String(scores.quality_label || "unknown").toUpperCase()} SUPPORT`;
  $("reportProvenance").textContent = `${replay ? "Saved report" : "New run"} · Generated ${formatTimestamp(report.generated_at)} · ${report.run_id || "Run ID unavailable"}`;
  $("summaryAsset").textContent = task.asset_name || "--";
  $("summaryAssetMark").textContent = assetMonogram(task.asset_name, task.symbol);
  $("summarySymbol").textContent = `${task.symbol || "--"} · ${assetTypeLabel(task.asset_type)}`;
  $("summaryPrice").textContent = formatPrice(situation.price, situation.currency);
  const oneDayMove = numericValue(report.quantitative_signals?.market?.return_1d_pct);
  const moveText = oneDayMove !== null ? `${oneDayMove >= 0 ? "+" : ""}${oneDayMove.toFixed(2)}% 1D at quote` : "1-day move unavailable";
  $("summaryMove").textContent = moveText;
  $("summaryMove").className = `company-move ${oneDayMove > 0 ? "positive" : oneDayMove < 0 ? "negative" : ""}`;
  $("summaryTask").textContent = String(task.task_type || "--").replaceAll("_", " ");
  $("summaryHorizon").textContent = task.horizon || "Agent inferred";
  $("summaryModel").textContent = report.llm?.selected_model || "deterministic fallback";
  $("summaryAsOf").textContent = formatTimestamp(situation.price_timestamp || task.as_of || report.generated_at);
  $("understoodRequest").textContent = task.query || "No request text recorded.";
  $("understandingMode").textContent = task.interpretation_explanation || String(task.interpretation_mode || "validated request understanding").replaceAll("_", " ");
  renderExecutionBrief(report);
  $("whyList").innerHTML = (situation.why || []).length
    ? situation.why.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
    : "<li>No concise reason list was generated.</li>";
  $("futureExpectation").textContent = report.future_expectation || "";
  const decisionBrief = report.decision_brief || {};
  $("keyInsight").textContent = decisionBrief.key_insight || "No evidence-backed leading insight was available.";
  $("keyInsightWhy").textContent = decisionBrief.why_it_matters || "";
  $("keyInsightReferences").innerHTML = evidenceReferences(decisionBrief.evidence_ids || []);
  $("watchItems").innerHTML = (decisionBrief.watch_items || []).length
    ? decisionBrief.watch_items.map((item, index) => `<article class="watch-item"><span>${String(index + 1).padStart(2, "0")}</span><div><strong>${escapeHtml(item.signal || "Signal")}</strong><p>${escapeHtml(item.why_it_matters || "")}</p><small><b>Confirm:</b> ${escapeHtml(item.confirm_if || "--")}</small><small><b>Invalidate:</b> ${escapeHtml(item.invalidate_if || "--")}</small>${evidenceReferences(item.evidence_ids || [])}</div></article>`).join("")
    : '<div class="empty-evidence">No supported watch item survived verification.</div>';
  $("nextResearchAction").textContent = decisionBrief.next_research_action || "Collect the missing critical evidence before extending the conclusion.";
  renderCounterfactual(report.counterfactual_evidence_test, report);

  const explanations = scores.explanation || {};
  $("scoreGrid").innerHTML = [
    scoreCard("Chain support", scores.chain_support_confidence, explanations.chain_support_confidence),
    scoreCard("Evidence quality", scores.evidence_quality, explanations.evidence_quality),
    scoreCard("Completeness", scores.evidence_completeness, explanations.evidence_completeness),
    scoreCard("Evidence conflict", scores.evidence_conflict, explanations.evidence_conflict, true),
  ].join("");

  const signals = report.quantitative_signals?.signals || [];
  $("signalGrid").innerHTML = signals.length
    ? signals.map((signal) => `<article class="signal-card"><span>${escapeHtml(signal.label)}</span><strong>${escapeHtml(signal.value)}${escapeHtml(signal.unit || "")}</strong></article>`).join("")
    : '<div class="empty-evidence">No deterministic signal could be calculated from the available data.</div>';

  renderBranch("upside", report.upside_thesis || {});
  renderBranch("downside", report.downside_thesis || {});
  $("downsideStatus").textContent = report.downside_thesis?.status || "NOT_SUPPORTED";

  const selectedSources = report.research_plan?.sources || [];
  const news = evidence.filter((item) => item.evidence_type === "news");
  const sourceStatuses = report.data_source_status || {};
  const sourceWasCalled = (name) => selectedSources.includes(name) || Object.keys(sourceStatuses).some(key => key === name || key.startsWith(name + "_retry_"));
  const newsSelected = sourceWasCalled("google_news");
  $("newsCount").textContent = String(news.length);
  if (news.length) $("filteredNews").innerHTML = news.map((item) => renderEvidenceItem(item)).join("");
  else if (!newsSelected) renderEmpty($("filteredNews"), "Google News was not called because fresh reporting was not required for this task route.");
  else renderEmpty($("filteredNews"), "No Google News items remained after normalization and deduplication.");

  const polymarket = evidence.filter((item) => item.evidence_type === "crowd_expectation");
  const polymarketRun = summarizePolymarket(report);
  $("polyCount").textContent = String(polymarket.length);
  if (polymarket.length) $("filteredPolymarket").innerHTML = polymarket.map((item) => renderEvidenceItem(item, "polymarket")).join("");
  else renderEmpty($("filteredPolymarket"), `${polymarketRun.title}. ${polymarketRun.message}`);

  const memory = report.history_chunks_used || [];
  const memorySelected = sourceWasCalled("vector_db");
  $("memoryCount").textContent = `${memory.length} / 3`;
  if (memory.length) $("historyChunks").innerHTML = memory.map((item) => renderEvidenceItem(item, "memory")).join("");
  else if (!memorySelected) renderEmpty($("historyChunks"), "Historical vector memory was not called because this task did not require news or prior-event context.");
  else renderEmpty($("historyChunks"), "No historical chunk was included. Inspect the memory tool result for empty storage, retrieval failures, or incompatible embeddings.");

  renderTimeline("finalNodeTimeline", report.workflow?.node_trace || trace, "completed");
  renderResearchSourcePlan(report.research_plan || {});
  renderSourceStatus(report.data_source_status || {});
  const audit = report.agent_audit || {};
  renderDecisionAudit(audit.decision_audit || [], report.agent_proof || {});
  renderToolCallAudit(audit.tool_calls || []);
  renderIntermediateResults(audit.intermediate_results || []);
  renderLlmCalls(report.llm || {});
  renderEvidenceInspection(report);
  $("allEvidence").innerHTML = evidence.length
    ? evidence.map((item) => renderEvidenceItem(item, item.evidence_type === "crowd_expectation" ? "polymarket" : item.evidence_type === "historical_news" ? "memory" : "default")).join("")
    : '<div class="empty-evidence">No normalized evidence is available.</div>';

  const limitations = [...(report.limitations || []), ...(report.evidence_conflicts || [])];
  $("limitationsList").innerHTML = limitations.length
    ? [...new Set(limitations)].map((item) => `<li>${escapeHtml(item)}</li>`).join("")
    : "<li>No major source limitation or evidence conflict was recorded.</li>";
}

function runProgressMessage(run, fallback) {
  return run.current_node === "counterfactual_evidence_test"
    ? "Running Counterfactual Evidence Test: checking support after removing selected evidence."
    : run.message || fallback;
}

function renderCounterfactual(result, report = {}) {
  const section = $("counterfactualContent");
  $("counterfactualSection").open = false;
  const note = '<p class="field-hint">We re-check the same saved conclusion after removing one evidence item at a time. All other supporting evidence stays. This checks evidence dependence; it does not generate a new conclusion or a probability.</p>';
  if (!result) {
    section.innerHTML = `${note}<p class="counterfactual-status">Not recorded for this report</p><p class="detail-copy">The test was disabled or this is an older report. Enable Counterfactual Evidence Test under advanced options for a new analysis.</p>`;
    return;
  }
  const timedOut = (result.errors || []).some(error => /timed out|timeout|budget exhausted/i.test(error));
  const labels = { completed: "Completed", partial: "Partially completed", skipped: "Not run", unavailable: timedOut ? "Timed out — no test result" : "Failed — no test result", running: "Running" };
  const comparison = summarizeCounterfactual(result, report);
  const scope = `${comparison.selectedClaims} conclusion${comparison.selectedClaims === 1 ? "" : "s"} selected${comparison.candidateClaims != null ? ` from ${comparison.candidateClaims} eligible candidates` : ""} · ${comparison.completedTests} evidence removals completed. ${comparison.maxClaims != null ? `Configured limit: ${comparison.maxClaims} conclusion${comparison.maxClaims === 1 ? "" : "s"}. ` : ""}This is a focused test, not an evaluation of every claim in the report.`;
  const claims = comparison.claims.map((claim, index) => `<article class="prompt-card cf-claim">
    <p class="reason-title">Conclusion ${index + 1} · Original conclusion (kept unchanged)</p><strong>${escapeHtml(claim.claim)}</strong>
    <p class="cf-baseline">With all original supporting evidence: <b>${escapeHtml(claim.beforeLabel)}</b></p>
    ${claim.comparisons.map(item => `<article class="cf-comparison">
      <p class="reason-title">Remove only this evidence</p><strong>${escapeHtml(item.title || item.evidence_id)}</strong>${evidenceReferences([item.evidence_id])}
      <dl class="cf-support-pair"><div><dt>With all supporting evidence</dt><dd>${escapeHtml(claim.beforeLabel)}</dd></div><div><dt>Without this evidence</dt><dd>${escapeHtml(item.afterLabel)}</dd></div></dl>
      <p class="cf-outcome">${escapeHtml(item.outcome)}</p>
      ${item.meaning ? `<p class="detail-copy"><b>${escapeHtml(item.importance)}</b> · ${escapeHtml(item.meaning.split(": ").slice(1).join(": "))}</p>` : ""}
      <details><summary>Evidence details &amp; model reason</summary>${item.excerpt ? `<p class="detail-copy"><b>Removed evidence excerpt:</b> ${escapeHtml(item.excerpt)}</p>` : ""}<p class="detail-copy"><b>Reason:</b> ${escapeHtml(item.reason || "Not recorded")}</p>${item.remainingIds ? `<p class="detail-copy"><b>Remaining supporting evidence:</b> ${item.remainingIds.length ? evidenceReferences(item.remainingIds) : "None"}</p>` : ""}</details>
    </article>`).join("")}
    ${!claim.comparisons.length ? `<p class="detail-copy">${escapeHtml(claim.explanation || "No evidence removal was evaluated for this conclusion.")}</p>` : ""}
  </article>`).join("");
  section.innerHTML = `${note}<p class="counterfactual-status" role="status">${escapeHtml(labels[result.status] || "Unknown status")}</p>
    <p class="field-hint">${escapeHtml(scope)}</p>
    ${comparison.thesisGenerationFallback ? '<p class="detail-copy">The report\'s thesis-generation step used a deterministic fallback. This test evaluates the saved report\'s conclusion.</p>' : ""}
    ${claims || `<p class="detail-copy">${timedOut ? "The model did not return a valid result within the time limit. Evidence importance was not assigned." : "No evidence importance result is available."}</p>`}
    ${(result.errors || []).length ? `<details><summary>Test errors</summary><p class="detail-copy">${escapeHtml(result.errors.join("; "))}</p></details>` : ""}`;
}

async function submitAnalysis(event) {
  event.preventDefault();
  if (isBusy) return;
  const targetInput = $("targetPrice");
  const targetValue = targetInput.value.trim();
  targetInput.setCustomValidity(targetValue && (!Number.isFinite(Number(targetValue)) || Number(targetValue) <= 0) ? "Enter a finite positive target price." : "");
  if (!targetInput.reportValidity()) return;
  beginRunUi();
  const payload = {
    asset: $("asset").value.trim(),
    target_price: $("targetPrice").value.trim() || null,
    target_condition: $("targetCondition").value,
    horizon: $("horizon").value,
    query: $("query").value.trim(),
    model: $("llmModel").value,
    enable_counterfactual_evidence_test: $("enableCounterfactualEvidenceTest").checked,
    prediction_provider: $("predictionProvider").value,
    temperature: Number($("temperatureSlider").value) / 100,
    max_retries: Number($("maxRetries").value),
    notes: $("notes").value,
    headlines: $("headlines").value,
    conversation_id: localStorage.getItem("marketAgentConversation") || crypto.randomUUID(),
  };
  localStorage.setItem("marketAgentConversation", payload.conversation_id);
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const started = await response.json();
    if (!response.ok) throw new Error(started.error || `Run could not start (${response.status})`);
    activeRunId = started.run_id;
    const run = await streamRun(activeRunId);
    renderReport(run.report || {}, run.node_trace || []);
    finishRunUi(true, `Report ready. ${run.node_trace?.length || 0} node executions recorded; inspect the evidence checks below.`);
    loadHistory();
    $("reportContent").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    finishRunUi(false, error.message);
    $("runningMessage").textContent = error.message;
  }
}

function resetOutput() {
  if (isBusy) return;
  activeRunId = null;
  displayedReport = null;
  hideRunMetrics();
  $("resultContent").classList.add("hidden");
  $("resultEmpty").classList.remove("hidden");
  $("statusPill").className = "status-pill neutral";
  $("statusPill").textContent = "Waiting for input";
}

async function loadHistory() {
  if (historyLoading) return;
  historyLoading = true;
  $("refreshHistoryButton").disabled = true;
  $("replayButton").disabled = true;
  try {
    const response = await fetch("/api/history?limit=30", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "History unavailable");
    const previous = $("savedRunSelect").value;
    const runs = (payload.runs || []).filter(run => run.status === "completed");
    $("savedRunSelect").innerHTML = runs.length
      ? runs.map(run => `<option value="${escapeHtml(run.run_id)}">${escapeHtml(run.symbol || "Asset")} · ${escapeHtml((run.task_type || "research").replaceAll("_", " "))} · ${escapeHtml(formatTimestamp(run.created_at))}</option>`).join("")
      : '<option value="">No saved reports yet</option>';
    if (runs.some(run => run.run_id === previous)) $("savedRunSelect").value = previous;
    else if (runs.length) $("savedRunSelect").value = runs[0].run_id;
    $("historyStatus").textContent = runs.length
      ? `${runs.length} saved reports. Replay uses stored evidence and makes no new research calls.`
      : "Complete a run to enable replay. Reports survive a server restart.";
  } catch (error) {
    $("historyStatus").textContent = error.message;
  } finally {
    historyLoading = false;
    $("refreshHistoryButton").disabled = false;
    $("replayButton").disabled = isBusy || !$("savedRunSelect").value;
  }
}

async function replaySavedRun() {
  const runId = $("savedRunSelect").value;
  if (isBusy || !runId) return;
  setBusy(true);
  $("historyStatus").textContent = "Loading the saved report and its original audit...";
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, { cache: "no-store" });
    const run = await response.json();
    if (!response.ok || run.status !== "completed" || !run.report?.task_summary) throw new Error(run.error || "This run has no completed report.");
    $("resultEmpty").classList.add("hidden");
    $("resultContent").classList.remove("hidden");
    $("runProgress").classList.add("hidden");
    renderReport(run.report, run.node_trace || [], { replay: true });
    $("analysisStateBanner").className = "analysis-state-banner replay";
    $("analysisStateBanner").textContent = `Saved report replay · ${formatTimestamp(run.report.generated_at)} · No new research or LLM calls.`;
    $("statusPill").className = "status-pill mixed";
    $("statusPill").textContent = "Saved replay";
    $("historyStatus").textContent = "Original evidence, model calls, and decisions loaded.";
    $("reportContent").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("historyStatus").textContent = `Replay failed: ${error.message}`;
  } finally {
    setBusy(false);
  }
}

function exportReport() {
  if (!displayedReport) return;
  const { report, replay } = displayedReport;
  const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), presentation: replay ? "saved_replay" : "new_run", report }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `market-agent-${String(report.run_id || "report").replace(/[^a-z0-9-]/gi, "")}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function selectDemo(name) {
  const demo = DEMO_REQUESTS[name];
  if (!demo || isBusy) return;
  clearTimeout(priceTimer);
  $("asset").value = demo.asset;
  $("query").value = demo.question;
  $("targetPrice").value = "";
  $("horizon").value = "";
  queryWasEdited = true;
  document.querySelectorAll(".demo-request").forEach(button => {
    const active = button.dataset.demo === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  document.querySelectorAll(".favorite-asset-button").forEach(button => button.classList.toggle("is-active", button.dataset.asset === demo.asset));
  fetchPrice(demo.asset);
}

function clearDemoSelection() {
  document.querySelectorAll(".demo-request").forEach(button => {
    button.classList.remove("is-active");
    button.setAttribute("aria-pressed", "false");
  });
}

async function loadArchitecture() {
  const container = $("architectureFlow");
  try {
    const response = await fetch("/api/graph");
    const graph = await response.json();
    container.innerHTML = (graph.nodes || NODE_ORDER).map((node, index) => `<div class="architecture-node"><span>${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(NODE_LABELS[node] || node)}</strong>${index < (graph.nodes || NODE_ORDER).length - 1 ? "<i>→</i>" : ""}</div>`).join("");
  } catch {
    container.innerHTML = NODE_ORDER.map((node, index) => `<div class="architecture-node"><span>${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(NODE_LABELS[node])}</strong></div>`).join("");
  }
}

function bindEvents() {
  $("enableCounterfactualEvidenceTest").addEventListener("change", () => { counterfactualChoiceEdited = true; updateCounterfactualChoiceStatus(); });
  $("reportContent").addEventListener("click", event => {
    const reference = event.target.closest("button[data-evidence-id]");
    if (reference) openReferencedEvidence(reference.dataset.evidenceId);
  });
  $("closeEvidenceDialog").addEventListener("click", () => $("evidenceDialog").close());
  $("evidenceDialog").addEventListener("click", event => {
    if (event.target === $("evidenceDialog")) {
      const bounds = $("evidenceDialog").getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) $("evidenceDialog").close();
    }
  });
  $("analysisForm").addEventListener("submit", submitAnalysis);
  $("resetButton").addEventListener("click", resetOutput);
  $("refreshPolymarketButton").addEventListener("click", () => loadPolymarketStatus());
  $("checkGammaButton").addEventListener("click", () => loadPolymarketStatus("gamma"));
  $("predictionProvider").addEventListener("change", updatePredictionProviderHint);
  loadPredictionProviders();
  $("refreshHistoryButton").addEventListener("click", loadHistory);
  $("refreshMetricsButton").addEventListener("click", loadMetricsDashboard);
  $("replayButton").addEventListener("click", replaySavedRun);
  $("exportReportButton").addEventListener("click", exportReport);
  $("savedRunSelect").addEventListener("change", () => { $("replayButton").disabled = isBusy || historyLoading || !$("savedRunSelect").value; });
  document.querySelectorAll(".demo-request").forEach(button => button.addEventListener("click", () => selectDemo(button.dataset.demo)));
  $("asset").addEventListener("input", () => {
    if (document.querySelector(".demo-request.is-active")) queryWasEdited = false;
    clearDemoSelection();
    schedulePriceFetch();
    refreshDefaultQuestion();
    document.querySelectorAll(".favorite-asset-button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.asset.toLowerCase() === $("asset").value.trim().toLowerCase());
    });
  });
  $("asset").addEventListener("change", () => fetchPrice($("asset").value));
  $("targetPrice").addEventListener("input", () => { $("targetPrice").setCustomValidity(""); refreshDefaultQuestion(); });
  $("targetCondition").addEventListener("change", () => refreshDefaultQuestion());
  $("horizon").addEventListener("change", () => refreshDefaultQuestion());
  $("query").addEventListener("input", () => {
    clearDemoSelection();
    queryWasEdited = $("query").value !== lastAutoQuery;
  });
  $("temperatureSlider").addEventListener("input", () => {
    $("temperatureValue").textContent = (Number($("temperatureSlider").value) / 100).toFixed(2);
  });
  document.querySelectorAll(".favorite-asset-button").forEach((button) => {
    button.addEventListener("click", () => {
      clearTimeout(priceTimer);
      clearDemoSelection();
      $("asset").value = button.dataset.asset;
      queryWasEdited = false;
      refreshDefaultQuestion(true);
      fetchPrice(button.dataset.asset);
      document.querySelectorAll(".favorite-asset-button").forEach((item) => item.classList.toggle("is-active", item === button));
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  lastAutoQuery = $("query").value;
  queryWasEdited = true;
  bindEvents();
  loadModels();
  loadCounterfactualSettings();
  loadHistory();
  loadArchitecture();
  fetchPrice($("asset").value);
});
