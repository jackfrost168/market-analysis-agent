import html
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

try:
    from .analysis import DEFAULT_LLM_MODEL, analyze_with_memory, base_agent
    from .vector_store import EmbeddingService, SQLiteVectorStore
except ImportError:
    from analysis import DEFAULT_LLM_MODEL, analyze_with_memory, base_agent
    from vector_store import EmbeddingService, SQLiteVectorStore


HOST = os.environ.get(
    "MARKET_AGENT_HOST", os.environ.get("RAG_AGENT_HOST", "127.0.0.1")
)
PORT = int(
    os.environ.get("MARKET_AGENT_PORT", os.environ.get("RAG_AGENT_PORT", "8002"))
)
APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = APP_DIR / "data" / "news_vectors.sqlite3"

_STORE = None
_STORE_LOCK = threading.Lock()
_EMBEDDER = EmbeddingService()
_MODELS_CACHE = {"stored_at": 0.0, "models": []}


def get_store() -> SQLiteVectorStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                configured_path = os.environ.get("RAG_VECTOR_DB_PATH")
                database_path = Path(configured_path) if configured_path else DEFAULT_DATABASE_PATH
                if not database_path.is_absolute():
                    database_path = APP_DIR / database_path
                _STORE = SQLiteVectorStore(database_path)
    return _STORE


def get_ollama_models():
    if time.time() - _MODELS_CACHE["stored_at"] < 30 and _MODELS_CACHE["models"]:
        return _MODELS_CACHE["models"]
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/tags",
        headers={"Accept": "application/json"},
    )
    models = []
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = sorted(
            {
                item.get("name")
                for item in payload.get("models", [])
                if item.get("name")
            }
        )
    except Exception:
        pass
    default_model = os.environ.get("OLLAMA_MODEL", DEFAULT_LLM_MODEL)
    if default_model not in models:
        models.insert(0, default_model)
    _MODELS_CACHE.update({"stored_at": time.time(), "models": models})
    return models


def _list_items(items, empty_message):
    if not items:
        return f"<li class=\"muted\">{html.escape(empty_message)}</li>"
    return "".join(f"<li>{html.escape(str(item))}</li>" for item in items)


def _safe_source_link(url):
    value = str(url or "")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ["http", "https"]:
        return html.escape(value or "source unavailable")
    return (
        f'<a href="{html.escape(value, quote=True)}" target="_blank" '
        'rel="noreferrer">open source</a>'
    )


def _score_card(label, score, note=""):
    if score is None:
        score_text = "N/A"
        width = 0
        css_class = "score-na"
    else:
        score_value = max(0.0, min(100.0, float(score)))
        score_text = f"{score_value:.0f}"
        width = score_value
        css_class = ""
    return f"""
      <article class="score-card {css_class}">
        <div class="score-head"><span>{html.escape(label)}</span><strong>{score_text}</strong></div>
        <div class="score-track"><span style="width: {width:.1f}%"></span></div>
        <small>{html.escape(note)}</small>
      </article>
    """


def _render_results(result):
    state = result["state"]
    final = state["final_answer"]
    memory = result["memory"]
    quality = result["quality"]
    components = quality["components"]

    why_items = _list_items(final["why"], "No supporting explanation was produced.")
    risk_items = _list_items(final["risks"], "No explicit risks were produced.")
    workflow_items = _list_items(final["workflow_steps"], "No workflow trace available.")
    citation_pills = "".join(
        f'<span class="tag citation">{html.escape(item)}</span>'
        for item in final["evidence_used"]
    ) or '<span class="tag muted">No valid evidence IDs cited</span>'

    score_cards = "".join(
        [
            _score_card("Evidence coverage", components["evidence_coverage"], "price + news + crowd coverage"),
            _score_card("Freshness", components["freshness"], "timestamps and publication age"),
            _score_card("Source reliability", components["source_reliability"], "live sources score above fixtures"),
            _score_card("News depth", components["news_depth"], "article paragraphs score above headlines"),
            _score_card("History retrieval", components["historical_retrieval"], "cosine match and top-3 coverage"),
            _score_card("Answer completeness", components["answer_completeness"], "all four requested sections"),
            _score_card("Grounding", components["grounding"], "valid evidence IDs used"),
            _score_card("Claim validation", components["claim_validation"], "unsupported source claims removed"),
        ]
    )
    claim_validation = result.get("claim_validation") or {}
    claim_guard_html = ""
    if claim_validation.get("removed_claim_count"):
        claim_guard_html = f"""
          <p class="warning"><strong>Claim guard corrected the model output.</strong>
          Removed {int(claim_validation['removed_claim_count'])} source-specific claim(s) because
          the referenced evidence source was empty. The organized answer above is the validated version.</p>
        """

    history_cards = []
    for item in memory.get("historical_matches", []):
        similarity = float(item.get("similarity", 0.0)) * 100
        age = item.get("published_at") or item.get("first_seen_at") or "date unavailable"
        history_cards.append(
            f"""
            <article class="memory-item">
              <div class="memory-meta">
                <span class="tag">similarity {similarity:.1f}%</span>
                <span>{html.escape(str(item.get('asset_symbol') or 'unknown'))}</span>
                <span>{html.escape(str(age))}</span>
              </div>
              <h4>{html.escape(item.get('headline') or 'Untitled historical chunk')}</h4>
              <p>{html.escape(item.get('chunk_text') or '')}</p>
              <small>{_safe_source_link(item.get('source_url'))}</small>
            </article>
            """
        )
    history_html = "".join(history_cards) or (
        '<div class="empty-state">No prior chunks existed before this run. '
        "Current live news has now been saved for the next analysis.</div>"
    )

    news_items = []
    for index, item in enumerate(state["evidence"]["news_context"][:6], start=1):
        news_items.append(
            f"""
            <li>
              <span class="evidence-id">news-{index}</span>
              <strong>{html.escape(item.get('headline') or '')}</strong>
              <small>{html.escape(str(item.get('sentiment') or 'unknown'))} · relevance {html.escape(str(item.get('relevance') or 'n/a'))}</small>
            </li>
            """
        )
    news_html = "".join(news_items) or '<li class="muted">No current news remained.</li>'

    crowd_items = []
    for index, item in enumerate(state["evidence"]["crowd_probability"][:5], start=1):
        probability = item.get("probability")
        probability_text = f"{probability:.0%}" if isinstance(probability, (int, float)) else "n/a"
        crowd_items.append(
            f"""
            <li>
              <span class="evidence-id">poly-{index}</span>
              <strong>{html.escape(item.get('market') or '')}</strong>
              <small>probability {probability_text} · relevance {html.escape(str(item.get('relevance') or 'n/a'))} · price match {html.escape(str(item.get('price_relevance', 0)))}%</small>
            </li>
            """
        )
    crowd_html = "".join(crowd_items) or '<li class="muted">No ongoing price-related Polymarket item remained.</li>'

    embedding = memory["embedding"]
    embedding_warning = ""
    if embedding.get("warning"):
        embedding_warning = f"""
          <p class="warning"><strong>Embedding fallback active.</strong> The requested Ollama model
          <code>{html.escape(str(embedding.get('requested_ollama_model')))}</code> could not embed, so the
          stable local hashing vector was used. Detail: {html.escape(str(embedding['warning']))}</p>
        """

    tool_details = []
    for tool in result["tools"]:
        tool_details.append(
            f"""
            <details>
              <summary>{html.escape(tool['tool'])}</summary>
              <pre>{html.escape(json.dumps(tool['result'], indent=2, default=str))}</pre>
            </details>
            """
        )

    conflict_items = _list_items(
        state["conflicts"], "No direct conflict was detected by the rule checks."
    )
    llm_input_json = html.escape(json.dumps(result["llm_input"], indent=2, default=str))
    llm_result_json = html.escape(json.dumps(result["llm_result"], indent=2, default=str))

    return f"""
    <section class="results">
      <div class="result-heading">
        <div>
          <p class="kicker">Memory-augmented result</p>
          <h2>{html.escape(final['asset'])} <span>{html.escape(final['symbol'])}</span></h2>
        </div>
        <div class="result-tags">
          <span class="tag">price {html.escape(final['current_price_display'] or 'n/a')}</span>
          <span class="tag">confidence {float(final['confidence']) * 100:.0f}%</span>
          <span class="tag">quality {quality['overall']:.0f}/100</span>
          <span class="tag">{html.escape(final['llm_model'])}</span>
          <span class="tag">{final['runtime_ms']} ms</span>
        </div>
      </div>

      <div class="analysis-grid">
        <article class="panel view-panel">
          <p class="section-number">01 · Current View</p>
          <p class="view-copy">{html.escape(final['current_view'])}</p>
          <div class="citation-row">{citation_pills}</div>
        </article>
        <article class="panel">
          <p class="section-number">02 · Why</p>
          <ul>{why_items}</ul>
        </article>
        <article class="panel risk-panel">
          <p class="section-number">03 · Risks</p>
          <ul>{risk_items}</ul>
        </article>
        <article class="panel future-panel">
          <p class="section-number">04 · Future Expectation</p>
          <p class="view-copy">{html.escape(final['future_expectation'])}</p>
        </article>
      </div>

      <section class="panel quality-panel">
        <div class="section-title-row">
          <div><p class="kicker">Process diagnostics</p><h3>Analysis quality: {quality['overall']:.0f}/100 · {html.escape(quality['label'])}</h3></div>
          <span class="quality-orb">{quality['overall']:.0f}</span>
        </div>
        <div class="score-grid">{score_cards}</div>
        <p class="fine-print">{html.escape(quality['explanation'])}</p>
        {claim_guard_html}
      </section>

      <section class="panel memory-panel">
        <div class="section-title-row">
          <div><p class="kicker">Persistent RAG memory</p><h3>Top historical paragraphs</h3></div>
          <div class="memory-stats">
            <span><strong>{memory['documents_before']}</strong> before</span>
            <span><strong>{memory['inserted_count']}</strong> added</span>
            <span><strong>{memory['duplicate_count']}</strong> seen again</span>
            <span><strong>{memory['documents_after']}</strong> total</span>
          </div>
        </div>
        <div class="memory-list">{history_html}</div>
        <div class="memory-footer">
          <span>Vector DB: <code>{html.escape(memory['database_path'])}</code></span>
          <span>Embedding: <code>{html.escape(str(embedding.get('model')))}</code> · {html.escape(str(embedding.get('dimension')))} dimensions</span>
          <span>Reindexed: {memory['reindexed_count']}</span>
        </div>
        {embedding_warning}
      </section>

      <div class="evidence-grid">
        <section class="panel evidence-panel">
          <p class="kicker">Current retrieval</p><h3>News used now</h3>
          <ul class="evidence-list">{news_html}</ul>
        </section>
        <section class="panel evidence-panel">
          <p class="kicker">Current retrieval</p><h3>Polymarket used now</h3>
          <ul class="evidence-list">{crowd_html}</ul>
        </section>
      </div>

      <div class="evidence-grid">
        <section class="panel">
          <p class="kicker">Autonomous trace</p><h3>Workflow</h3>
          <ol>{workflow_items}</ol>
        </section>
        <section class="panel">
          <p class="kicker">Conflict check</p><h3>Disagreement found</h3>
          <ul>{conflict_items}</ul>
          <p class="fine-print"><strong>Stop reason:</strong> {html.escape(state['stop_reason'])}</p>
        </section>
      </div>

      <section class="panel diagnostics">
        <p class="kicker">Inspectability</p><h3>Exact reasoning inputs and outputs</h3>
        <details open><summary>Exact LLM evidence payload</summary><pre>{llm_input_json}</pre></details>
        <details><summary>LLM response metadata</summary><pre>{llm_result_json}</pre></details>
        <details><summary>Deterministic claim validation</summary><pre>{html.escape(json.dumps(claim_validation, indent=2, default=str))}</pre></details>
        {''.join(tool_details)}
      </section>
    </section>
    """


def render_page(result=None, form_values=None, error_message=None):
    form_values = form_values or {}
    asset_input = html.escape(form_values.get("asset_input", ""), quote=True)
    question = html.escape(form_values.get("question", ""))
    target_price = html.escape(form_values.get("target_price", ""), quote=True)
    selected_model = form_values.get("model") or os.environ.get("OLLAMA_MODEL", DEFAULT_LLM_MODEL)
    models = get_ollama_models()
    model_options = "".join(
        f'<option value="{html.escape(model, quote=True)}"'
        f'{" selected" if model == selected_model else ""}>{html.escape(model)}</option>'
        for model in models
    )
    result_html = _render_results(result) if result else ""
    error_html = (
        f'<div class="error-banner"><strong>Analysis could not complete.</strong> {html.escape(error_message)}</div>'
        if error_message
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Market Memory Agent</title>
  <style>
    :root {{
      --ink: #14231f;
      --muted: #5f6d67;
      --paper: #f4f0e5;
      --panel: rgba(255, 253, 246, 0.9);
      --line: rgba(20, 35, 31, 0.14);
      --pine: #1f5c4c;
      --mint: #a8d5bd;
      --gold: #c6903f;
      --rust: #9b4939;
      --shadow: 0 22px 70px rgba(28, 49, 42, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Palatino Linotype", "Book Antiqua", Palatino, serif;
      background:
        linear-gradient(rgba(20, 35, 31, 0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(20, 35, 31, 0.035) 1px, transparent 1px),
        radial-gradient(circle at 8% 5%, rgba(168, 213, 189, 0.65), transparent 25%),
        radial-gradient(circle at 92% 14%, rgba(198, 144, 63, 0.24), transparent 23%),
        var(--paper);
      background-size: 28px 28px, 28px 28px, auto, auto, auto;
      min-height: 100vh;
    }}
    a {{ color: var(--pine); }}
    code, pre, select, input, textarea, button {{ font-family: Consolas, "Courier New", monospace; }}
    code {{ overflow-wrap: anywhere; word-break: break-word; }}
    .shell {{ width: min(1240px, calc(100% - 32px)); margin: 0 auto; padding: 34px 0 60px; }}
    .masthead {{ display: grid; grid-template-columns: 1.3fr .7fr; gap: 30px; align-items: end; margin-bottom: 26px; }}
    .masthead h1 {{ margin: 6px 0 12px; max-width: 11ch; font-size: clamp(3rem, 7vw, 6.8rem); line-height: .84; letter-spacing: -.065em; font-weight: 500; }}
    .masthead .intro {{ max-width: 63ch; color: var(--muted); font-size: 1.05rem; line-height: 1.65; }}
    .edition {{ justify-self: end; width: 210px; aspect-ratio: 1; border-radius: 50%; display: grid; place-content: center; text-align: center; border: 1px solid var(--line); background: rgba(255, 253, 246, .58); box-shadow: var(--shadow); transform: rotate(4deg); }}
    .edition strong {{ display: block; font-size: 2.8rem; color: var(--pine); line-height: 1; }}
    .edition span {{ color: var(--muted); text-transform: uppercase; letter-spacing: .14em; font: .7rem Consolas, monospace; }}
    .kicker, .section-number {{ margin: 0 0 8px; color: var(--pine); text-transform: uppercase; letter-spacing: .14em; font: 700 .74rem Consolas, monospace; }}
    .panel, .agent-form {{ border: 1px solid var(--line); background: var(--panel); box-shadow: var(--shadow); backdrop-filter: blur(12px); }}
    .agent-form {{ border-radius: 28px; padding: 24px; margin-bottom: 30px; }}
    .form-grid {{ display: grid; grid-template-columns: 1.15fr 1fr .75fr 1fr; gap: 14px; }}
    label {{ display: grid; gap: 8px; color: var(--muted); font: .76rem Consolas, monospace; text-transform: uppercase; letter-spacing: .06em; }}
    label.question-label {{ grid-column: 1 / -1; }}
    input, select, textarea, .preview-box {{ width: 100%; border: 1px solid var(--line); border-radius: 14px; background: rgba(255,255,255,.76); color: var(--ink); padding: 13px 14px; font-size: .9rem; text-transform: none; letter-spacing: 0; }}
    .preview-box {{ min-height: 45px; display: flex; align-items: center; }}
    .preview-box.muted, .muted {{ color: var(--muted); }}
    textarea {{ min-height: 96px; resize: vertical; }}
    .form-actions {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-top: 16px; }}
    button {{ border: 0; border-radius: 999px; padding: 12px 17px; cursor: pointer; }}
    .run-button {{ margin-left: auto; color: #fff; background: linear-gradient(135deg, var(--pine), #153f35); padding-inline: 24px; box-shadow: 0 10px 24px rgba(31,92,76,.2); }}
    .quick-button {{ border: 1px solid var(--line); background: rgba(255,255,255,.62); color: var(--ink); }}
    button:disabled {{ opacity: .55; cursor: wait; }}
    .form-note {{ margin: 14px 0 0; color: var(--muted); font-size: .88rem; }}
    .error-banner, .warning {{ border: 1px solid rgba(155,73,57,.3); background: rgba(155,73,57,.08); border-radius: 16px; padding: 14px 16px; margin: 0 0 20px; }}
    .results {{ display: grid; gap: 20px; min-width: 0; animation: reveal .55s ease both; }}
    @keyframes reveal {{ from {{ opacity: 0; transform: translateY(14px); }} }}
    .result-heading {{ display: flex; gap: 20px; justify-content: space-between; align-items: end; border-bottom: 1px solid var(--line); padding: 8px 4px 18px; }}
    .result-heading h2 {{ margin: 0; font-size: clamp(2rem, 4vw, 4rem); font-weight: 500; letter-spacing: -.04em; }}
    .result-heading h2 span {{ color: var(--muted); font: .9rem Consolas, monospace; letter-spacing: .04em; }}
    .result-tags, .citation-row {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }}
    .tag {{ border: 1px solid rgba(31,92,76,.18); background: rgba(168,213,189,.22); color: var(--pine); border-radius: 999px; padding: 6px 10px; font: .74rem Consolas, monospace; }}
    .tag.citation {{ background: rgba(198,144,63,.14); color: #704b16; }}
    .analysis-grid {{ display: grid; grid-template-columns: 1.15fr .85fr; gap: 18px; }}
    .panel {{ border-radius: 24px; padding: 22px; min-width: 0; }}
    .panel h3 {{ margin: 0 0 14px; font-size: 1.45rem; font-weight: 500; }}
    .view-panel {{ background: linear-gradient(135deg, rgba(31,92,76,.96), rgba(20,50,42,.96)); color: #f7f3e8; }}
    .view-panel .section-number {{ color: var(--mint); }}
    .view-panel .view-copy {{ font-size: clamp(1.25rem, 2.2vw, 2rem); }}
    .risk-panel {{ border-top: 5px solid var(--rust); }}
    .future-panel {{ border-top: 5px solid var(--gold); }}
    .view-copy {{ margin: 0 0 18px; font-size: 1.16rem; line-height: 1.5; }}
    ul, ol {{ margin: 0; padding-left: 20px; }}
    li {{ margin-bottom: 9px; line-height: 1.45; }}
    .quality-panel {{ overflow: hidden; }}
    .section-title-row {{ display: flex; justify-content: space-between; gap: 18px; align-items: center; margin-bottom: 16px; }}
    .quality-orb {{ width: 76px; aspect-ratio: 1; border-radius: 50%; display: grid; place-items: center; background: var(--ink); color: var(--paper); font: 1.6rem Consolas, monospace; }}
    .score-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
    .score-card {{ border: 1px solid var(--line); border-radius: 15px; padding: 13px; background: rgba(255,255,255,.5); }}
    .score-head {{ display: flex; justify-content: space-between; gap: 8px; font: .78rem Consolas, monospace; }}
    .score-head strong {{ color: var(--pine); font-size: 1.15rem; }}
    .score-track {{ height: 5px; background: rgba(20,35,31,.1); border-radius: 99px; margin: 10px 0; overflow: hidden; }}
    .score-track span {{ display: block; height: 100%; background: linear-gradient(90deg, var(--gold), var(--pine)); border-radius: inherit; }}
    .score-card small, .fine-print {{ color: var(--muted); font-size: .8rem; }}
    .memory-panel {{ border-left: 7px solid var(--pine); }}
    .memory-stats {{ display: flex; gap: 15px; flex-wrap: wrap; color: var(--muted); font: .76rem Consolas, monospace; }}
    .memory-stats strong {{ color: var(--ink); display: block; font-size: 1.2rem; }}
    .memory-list {{ display: grid; gap: 10px; }}
    .memory-item {{ border-top: 1px solid var(--line); padding-top: 14px; }}
    .memory-item h4 {{ margin: 9px 0 6px; font-size: 1rem; }}
    .memory-item p {{ margin: 0 0 7px; color: var(--muted); line-height: 1.5; }}
    .memory-meta, .memory-footer {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; color: var(--muted); font: .72rem Consolas, monospace; }}
    .memory-footer {{ margin-top: 17px; padding-top: 14px; border-top: 1px solid var(--line); justify-content: space-between; overflow-wrap: anywhere; }}
    .empty-state {{ padding: 25px; border: 1px dashed var(--line); border-radius: 15px; color: var(--muted); text-align: center; }}
    .evidence-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
    .evidence-list {{ list-style: none; padding: 0; }}
    .evidence-list li {{ display: grid; grid-template-columns: auto 1fr; column-gap: 9px; padding: 9px 0; border-bottom: 1px solid var(--line); }}
    .evidence-list small {{ grid-column: 2; color: var(--muted); margin-top: 3px; }}
    .evidence-id {{ color: var(--pine); font: .68rem Consolas, monospace; border: 1px solid var(--line); border-radius: 6px; padding: 3px 5px; align-self: start; }}
    details {{ border-top: 1px solid var(--line); padding: 13px 0; }}
    summary {{ cursor: pointer; font: .82rem Consolas, monospace; color: var(--pine); }}
    pre {{ width: 100%; max-width: 100%; max-height: 520px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: rgba(20,35,31,.05); border-radius: 13px; padding: 13px; font-size: .76rem; line-height: 1.45; }}
    .loading-overlay {{ position: fixed; inset: 0; z-index: 50; background: rgba(20,35,31,.48); display: grid; place-items: center; backdrop-filter: blur(5px); }}
    .loading-overlay[hidden] {{ display: none !important; }}
    .loading-card {{ width: min(430px, calc(100% - 32px)); background: var(--paper); border-radius: 24px; padding: 28px; text-align: center; box-shadow: var(--shadow); }}
    .memory-pulse {{ width: 64px; height: 64px; margin: 0 auto 16px; border-radius: 50%; border: 2px solid var(--pine); position: relative; animation: pulse 1.4s ease-in-out infinite; }}
    .memory-pulse::after {{ content: ""; position: absolute; inset: 11px; border-radius: 50%; background: var(--pine); }}
    @keyframes pulse {{ 50% {{ transform: scale(.82); opacity: .55; }} }}
    @media (max-width: 900px) {{
      .masthead, .analysis-grid, .evidence-grid {{ grid-template-columns: 1fr; }}
      .edition {{ display: none; }}
      .form-grid {{ grid-template-columns: 1fr 1fr; }}
      label.question-label {{ grid-column: 1 / -1; }}
      .score-grid {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 580px) {{
      .shell {{ width: min(100% - 18px, 1240px); padding-top: 20px; }}
      .form-grid, .score-grid {{ grid-template-columns: 1fr; }}
      .run-button {{ width: 100%; margin-left: 0; }}
      .result-heading, .section-title-row {{ align-items: flex-start; flex-direction: column; }}
      .result-tags {{ justify-content: flex-start; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="masthead">
      <div>
        <p class="kicker">Persistent RAG · Local Ollama · Live Evidence</p>
        <h1>Market memory agent.</h1>
        <p class="intro">This upgraded version remembers prior news paragraphs across restarts, retrieves the three closest historical chunks before each analysis, and exposes the exact evidence sent to the selected local LLM.</p>
      </div>
      <div class="edition"><span>separate build</span><strong>RAG</strong><span>memory / 03</span></div>
    </header>

    {error_html}
    <form class="agent-form" method="post" action="/analyze" id="analysis-form">
      <div class="form-grid">
        <label>Company or symbol
          <input id="asset-input" name="asset_input" value="{asset_input}" placeholder="Apple, AAPL, Bitcoin, BTC" required>
        </label>
        <label>Current price
          <div class="preview-box muted" id="price-preview-text">Enter a company name or symbol.</div>
        </label>
        <label>Target price
          <input name="target_price" value="{target_price}" placeholder="Optional">
        </label>
        <label>Reasoning model
          <select name="model">{model_options}</select>
        </label>
        <label class="question-label">Market question
          <textarea name="question" id="question-input" placeholder="Is Apple likely to move up or down in the near term?">{question}</textarea>
        </label>
      </div>
      <div class="form-actions">
        <button type="button" class="quick-button" data-asset="Bitcoin">Bitcoin</button>
        <button type="button" class="quick-button" data-asset="Apple">Apple</button>
        <button type="button" class="quick-button" data-asset="Google">Google</button>
        <button type="button" class="quick-button" data-asset="Tesla">Tesla</button>
        <button type="submit" class="run-button" id="analyze-button">Run Memory Analysis</button>
      </div>
      <p class="form-note">Runs independently on <code>127.0.0.1:{PORT}</code>. The maintained original remains on port 8001.</p>
    </form>

    {result_html}
  </main>

  <div class="loading-overlay" id="loading-overlay" hidden>
    <div class="loading-card">
      <div class="memory-pulse" aria-hidden="true"></div>
      <h3>Building the evidence view</h3>
      <p>Searching live sources, retrieving historical vectors, persisting current news, and asking the local LLM to reason.</p>
    </div>
  </div>

  <script>
    const form = document.getElementById("analysis-form");
    const button = document.getElementById("analyze-button");
    const overlay = document.getElementById("loading-overlay");
    const assetInput = document.getElementById("asset-input");
    const questionInput = document.getElementById("question-input");
    const pricePreviewText = document.getElementById("price-preview-text");
    const quickButtons = document.querySelectorAll(".quick-button");
    let questionWasEdited = false;
    let previewTimer = null;

    const buildQuestion = (asset) => {{
      const value = (asset || "").trim();
      return value ? `Is ${{value}} likely to move up or down in the near term?` : "";
    }};
    const syncButton = () => {{
      button.disabled = !assetInput.value.trim();
    }};
    const updatePricePreview = async () => {{
      const asset = assetInput.value.trim();
      if (!asset) {{
        pricePreviewText.textContent = "Enter a company name or symbol.";
        pricePreviewText.classList.add("muted");
        return;
      }}
      pricePreviewText.textContent = "Fetching current price...";
      pricePreviewText.classList.add("muted");
      try {{
        const response = await fetch(`/price-preview?asset=${{encodeURIComponent(asset)}}`);
        const data = await response.json();
        if (!data.success) throw new Error("price unavailable");
        const sign = data.change_pct >= 0 ? "+" : "";
        pricePreviewText.textContent = `${{data.asset_name}} (${{data.symbol}}): $${{data.price_display}} (${{sign}}${{data.change_pct.toFixed(2)}}%)`;
        pricePreviewText.classList.remove("muted");
      }} catch (_error) {{
        pricePreviewText.textContent = "Current price could not be fetched.";
      }}
    }};

    assetInput.addEventListener("input", () => {{
      syncButton();
      if (!questionWasEdited || !questionInput.value.trim()) questionInput.value = buildQuestion(assetInput.value);
      clearTimeout(previewTimer);
      previewTimer = setTimeout(updatePricePreview, 260);
    }});
    questionInput.addEventListener("input", () => {{
      questionWasEdited = questionInput.value.trim() !== buildQuestion(assetInput.value);
    }});
    quickButtons.forEach((quickButton) => quickButton.addEventListener("click", () => {{
      assetInput.value = quickButton.dataset.asset || "";
      if (!questionWasEdited) questionInput.value = buildQuestion(assetInput.value);
      syncButton();
      clearTimeout(previewTimer);
      previewTimer = setTimeout(updatePricePreview, 40);
    }}));
    form.addEventListener("submit", () => {{
      if (!assetInput.value.trim()) return;
      button.disabled = true;
      button.textContent = "Analyzing...";
      overlay.hidden = false;
    }});
    if (assetInput.value.trim()) updatePricePreview();
    syncButton();
  </script>
</body>
</html>"""


class RAGMarketAnalysisHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, page, status=200):
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/price-preview"):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            raw_asset = params.get("asset", [""])[0].strip()
            self._send_json(base_agent.build_price_preview(raw_asset, ""))
            return
        if self.path == "/health":
            embedding_details = _EMBEDDER.details()
            self._send_json(
                {
                    "status": "ok",
                    "port": PORT,
                    "vector_store": get_store().stats(
                        embedding_details.get("embedding_space")
                    ),
                    "embedding": embedding_details,
                }
            )
            return
        if self.path not in ["/", "/index.html"]:
            self._send_html(render_page(), status=404)
            return
        self._send_html(render_page())

    def do_POST(self):
        if self.path != "/analyze":
            self._send_html(render_page(), status=404)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        form = parse_qs(raw_body)
        asset_input = form.get("asset_input", [""])[0].strip()
        question = form.get("question", [""])[0].strip()
        if not question:
            question = f"Is {asset_input} likely to move up or down in the near term?"
        target_price_raw = form.get("target_price", [""])[0].strip()
        target_price = base_agent.parse_number(target_price_raw)
        model = form.get("model", [os.environ.get("OLLAMA_MODEL", DEFAULT_LLM_MODEL)])[0].strip()
        model = model or DEFAULT_LLM_MODEL
        asset_metadata = base_agent.resolve_user_asset(asset_input, question)
        form_values = {
            "asset_input": asset_input,
            "question": question,
            "target_price": target_price_raw,
            "model": model,
        }
        try:
            result = analyze_with_memory(
                question=question,
                symbol=asset_metadata["symbol"],
                target_price=target_price,
                asset_name_override=asset_metadata["asset_name"],
                model=model,
                store=get_store(),
                embedder=_EMBEDDER,
            )
            self._send_html(render_page(result=result, form_values=form_values))
        except Exception as exc:
            self._send_html(
                render_page(form_values=form_values, error_message=str(exc)), status=500
            )

    def log_message(self, fmt, *args):
        return


def main():
    get_store()
    server = ThreadingHTTPServer((HOST, PORT), RAGMarketAnalysisHandler)
    print(f"Market Memory Agent running at http://{HOST}:{PORT}")
    print(f"Persistent vector DB: {get_store().database_path.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
