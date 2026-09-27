import { DEMO_REQUESTS, numericValue, stageStates, summarizeExecution, summarizePolymarket } from "./presentation.mjs";

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
};

const SOURCE_LABELS = {
  yahoo_finance: "Yahoo Finance",
  google_news: "Google News",
  polymarket: "Polymarket venue data",
  vector_db: "Historical vector DB",
  financial_statements: "SEC financial statements",
  price_history: "Price history",
};

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
    auto.textContent = `Auto (${payload.auto_selected || "best available"})`;
    select.append(auto);
    for (const model of payload.models || []) {
      const option = document.createElement("option");
      option.value = model.name;
      option.textContent = model.name;
      select.append(option);
    }
    $("ollamaDot").classList.toggle("online", Boolean(payload.success));
    $("llmStatus").textContent = payload.success
      ? `${payload.models.length} local models found. Auto selects ${payload.auto_selected}.`
      : `Ollama unavailable. The graph will use deterministic fallbacks. ${payload.error || ""}`;
  } catch (error) {
    $("ollamaDot").classList.remove("online");
    $("llmStatus").textContent = `Ollama check failed: ${error.message}. Deterministic fallback remains available.`;
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
  const occurrences = trace.reduce((result, item) => {
    result[item.node] = (result[item.node] || 0) + 1;
    return result;
  }, {});
  container.innerHTML = NODE_ORDER.map((node, index) => {
    const count = occurrences[node] || 0;
    let stateClass = count ? "is-complete" : "is-pending";
    if (status === "running" && node === currentNode) stateClass = "is-active";
    const repeat = count > 1 ? `<em>×${count}</em>` : "";
    const optional = node === "targeted_retrieval" ? "optional" : String(index + 1).padStart(2, "0");
    return `<div class="node-step ${stateClass}"><span>${optional}</span><strong>${escapeHtml(NODE_LABELS[node])}</strong>${repeat}</div>`;
  }).join("");
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
  $("runningMessage").textContent = "Understanding the ordinary question with local Ollama...";
  renderTimeline("nodeTimeline", [], "running", "understand_request");
  renderStages([], "understand_request");
  renderLiveSources({});
  renderLiveAudit({});
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
  $("runningMessage").textContent = run.message || "Waiting for the next node update...";
  renderTimeline("nodeTimeline", trace, run.status, run.current_node);
  renderStages(trace, run.current_node, run.status);
  renderLiveSources(run.source_status || {});
  renderLiveAudit(run);
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
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, { cache: "no-store" });
    const run = await response.json();
    if (!response.ok) throw new Error(run.error || `Run status failed (${response.status})`);
    updateRunProgress(run);
    if (run.status === "completed") return run;
    if (run.status === "failed") throw new Error(run.error || run.message || "Agent run failed");
    await delay(800);
  }
  throw new Error("The run exceeded the browser polling limit.");
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
    ? chain.map((step, index) => `<div class="chain-step"><span class="chain-index">${index + 1}</span><div><strong>${escapeHtml(step.claim || "")}</strong><p>${escapeHtml(step.transmission_mechanism || "")}</p><div class="evidence-id-row">${(step.evidence_ids || []).map((id) => `<span>${escapeHtml(id)}</span>`).join("")}</div></div></div>`).join("")
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
  $("llmCalls").innerHTML = calls.length
    ? calls.map((call) => `<article class="llm-call"><div><strong>${escapeHtml(call.node || "LLM step")}</strong><span class="source-chip ${call.success ? "success" : "failed"}">${call.success ? "structured output" : "fallback"}</span></div><p>${escapeHtml(call.purpose || "")}</p><dl><div><dt>Model</dt><dd>${escapeHtml(call.model || "--")}</dd></div><div><dt>Latency</dt><dd>${escapeHtml(call.latency_ms ?? "--")} ms</dd></div></dl><strong class="audit-subtitle">Input manifest</strong><pre>${escapeHtml(JSON.stringify(call.input_manifest || {}, null, 2))}</pre>${call.prompt_preview ? `<details class="llm-prompt-preview"><summary>Actual prompt sent to Ollama</summary><pre>${escapeHtml(call.prompt_preview)}</pre></details>` : ""}${call.error ? `<small>${escapeHtml(call.error)}</small>` : ""}</article>`).join("")
    : '<div class="empty-evidence">No LLM call record is available.</div>';
}

function renderExecutionBrief(report) {
  const summary = summarizeExecution(report);
  const humanize = value => String(value).replaceAll("_", " ");
  const embedding = summary.embedding;
  const embeddingText = embedding
    ? embedding.backend === "local_hash" ? "Hash-vector fallback, not a semantic embedding model."
      : `${embedding.backend || embedding.mode || "embedding"} · ${embedding.model || "see tool result"}`
    : summary.memoryRead ? "Embedding details available in the saved tool result, when recorded." : "Memory not queried for this task.";
  const metrics = [
    ["Tool calls", `${summary.toolSuccess} / ${summary.calls.length} succeeded`, "Actual calls, including retries and memory writes."],
    ["Local LLM", `${summary.llmSuccess} / ${summary.llmCalls.length} succeeded`, `${summary.failedLlm} fallback calls · ${summary.repaired} thesis repair passes`],
    ["Historical RAG", `${summary.memoryUsed} / 3 chunks used`, embeddingText],
    ["Feedback loop", `${summary.retryCount} retrieval retries`, `${summary.checksPassed} / ${summary.checksTotal} final checks passed`],
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
  $("allEvidence").innerHTML = evidence.length
    ? evidence.map((item) => renderEvidenceItem(item, item.evidence_type === "crowd_expectation" ? "polymarket" : item.evidence_type === "historical_news" ? "memory" : "default")).join("")
    : '<div class="empty-evidence">No normalized evidence is available.</div>';

  const limitations = [...(report.limitations || []), ...(report.evidence_conflicts || [])];
  $("limitationsList").innerHTML = limitations.length
    ? [...new Set(limitations)].map((item) => `<li>${escapeHtml(item)}</li>`).join("")
    : "<li>No major source limitation or evidence conflict was recorded.</li>";
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
    const run = await pollRun(activeRunId);
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
  $("analysisForm").addEventListener("submit", submitAnalysis);
  $("resetButton").addEventListener("click", resetOutput);
  $("refreshPolymarketButton").addEventListener("click", () => loadPolymarketStatus());
  $("checkGammaButton").addEventListener("click", () => loadPolymarketStatus("gamma"));
  $("predictionProvider").addEventListener("change", updatePredictionProviderHint);
  loadPredictionProviders();
  $("refreshHistoryButton").addEventListener("click", loadHistory);
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
  loadHistory();
  loadArchitecture();
  fetchPrice($("asset").value);
});
