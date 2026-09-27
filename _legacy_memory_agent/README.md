# Market Memory Agent

This is the separate persistent-memory upgrade of the market analysis agent. The original application in `../polymarket_trading` remains unchanged. This project runs directly from `server.py` and uses port `8002` by default.

## What Changed

- Live price, Google News, and ongoing price-related Polymarket retrieval are reused from the maintained agent.
- Publisher article paragraphs are extracted when available. Each paragraph becomes one news chunk.
- If a publisher blocks extraction or Google News exposes only a headline, that headline becomes one clearly reported fallback chunk.
- Live news chunks are embedded, deduplicated, and stored in a persistent SQLite vector database.
- Before current news is inserted, the top three prior chunks are selected by exact cosine similarity.
- Those historical chunks, with similarity scores, dates, and source metadata, are included in the local Ollama reasoning input.
- The answer is always organized as Current View, Why, Risks, and Future Expectation.
- The page exposes the exact LLM evidence payload, selected model, memory statistics, tool trace, and quality diagnostics.

Fixture fallback headlines are never written to persistent memory.

## Persistence And Retrieval Order

The database is stored at `data/news_vectors.sqlite3` unless `RAG_VECTOR_DB_PATH` is set.

Each analysis follows this order:

1. Collect current price, news, article paragraphs, and ongoing Polymarket markets.
2. Build one retrieval query from the asset, user question, price, target, and current headlines.
3. Embed the query.
4. If old documents lack the active embedding model, lazily re-embed them.
5. Search the existing database and return up to three prior paragraphs by cosine similarity.
6. Insert and embed current live paragraphs, deduplicating by normalized content hash.
7. Send live evidence and retrieved history to the selected Ollama reasoning model.
8. Persist the database on disk for later runs and restarts.

On the first run, no history is available, so the current chunks are saved. On the second and later runs, prior chunks can be retrieved. Retrieval occurs before insertion, so a new paragraph cannot appear as its own history during its first run.

## Embeddings

The app first tries the Ollama embedding model named by `OLLAMA_EMBED_MODEL` (default: `embeddinggemma`). The generation models currently installed on this machine, including `qwen3:8b`, do not expose Ollama's embedding capability.

If the configured embedding model is unavailable, the app immediately falls back to a stable 512-dimensional local hashing embedding. This fallback is persistent and supports cosine retrieval without installing anything, but a dedicated semantic embedding model will normally give better meaning-based matches.

To use Ollama semantic embeddings, install an embedding-capable model and restart the app:

```powershell
ollama pull embeddinggemma
$env:OLLAMA_EMBED_MODEL = "embeddinggemma"
python .\server.py
```

When the embedding space changes, old documents remain in the database and are automatically re-embedded into the new space.

## Quality Score

The quality score is separate from the LLM's market confidence. It combines:

- evidence coverage: current price, news count, and Polymarket coverage
- freshness: price retrieval time and news publication age
- source reliability: live sources score above fixture fallbacks
- news depth: extracted article paragraphs score above headline-only fallbacks
- historical retrieval: top-three coverage and cosine similarity
- answer completeness: all four requested output sections are populated
- grounding: the model cites only evidence IDs present in its input
- claim validation: source-specific claims are removed when that source returned no evidence

The history component is marked `N/A` on an empty first run and its weight is redistributed. This score measures analysis-process quality, not forecast correctness, future return, or trading profitability.

## Run

Start Ollama, then run from the project root:

```powershell
python .\server.py
```

Open `http://127.0.0.1:8002`.

The reasoning-model menu is populated from locally installed Ollama models. `OLLAMA_MODEL` controls the initial selection; it defaults to `qwen3:8b`.

## Configuration

Use PowerShell environment variables before starting the process. See `.env.example` for all supported values.

Important settings:

- `MARKET_AGENT_HOST`: bind address, default `127.0.0.1`
- `MARKET_AGENT_PORT`: web port, default `8002`
- `RAG_VECTOR_DB_PATH`: persistent SQLite database path
- `OLLAMA_MODEL`: default reasoning model
- `OLLAMA_EMBED_MODEL`: preferred embedding model
- `RAG_ARTICLE_FETCH_LIMIT`: maximum articles fetched per run, default `4`
- `RAG_ARTICLE_TIMEOUT_SECONDS`: timeout per publisher page, default `8`
- `OLLAMA_GENERATE_TIMEOUT_SECONDS`: LLM reasoning timeout, default `180`

## Tests

The tests do not use external network sources or Ollama:

```powershell
python -m unittest test_market_memory -v
```

They verify paragraph boundaries, headline fallback, persistence after database reopen, cosine ranking, deduplication, retrieve-before-insert ordering, structured-answer normalization, and unsupported-source claim removal.

## Practical Limits

- SQLite performs exact cosine ranking in Python. It is simple and reliable for this project; a large corpus should migrate to a vector index such as Qdrant, pgvector, or Chroma.
- Some publishers block automated article access. The app records extraction counts and falls back to the headline instead of fabricating article text.
- Historical similarity does not make old news current. The LLM prompt labels every memory chunk as potentially stale and prioritizes live evidence.
- The quality score is auditable but cannot prove factual correctness. A stronger evaluation layer would later add claim-level citation checks and delayed forecast backtesting.
