import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embed"
DEFAULT_EMBED_MODEL = ""
HASH_EMBED_DIMENSION = 512


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_chunk_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def content_hash(text: str) -> str:
    normalized = normalize_chunk_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(payload: bytes, dimension: int) -> Tuple[float, ...]:
    return struct.unpack(f"<{dimension}f", payload)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class SQLiteVectorStore:
    """Persistent document and vector storage with exact cosine retrieval."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.database_path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS news_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_hash TEXT NOT NULL UNIQUE,
                    asset_symbol TEXT NOT NULL,
                    asset_name TEXT,
                    headline TEXT,
                    chunk_text TEXT NOT NULL,
                    source_url TEXT,
                    published_at TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS news_embeddings (
                    document_id INTEGER NOT NULL,
                    embedding_space TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (document_id, embedding_space),
                    FOREIGN KEY (document_id) REFERENCES news_documents(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_documents_asset ON news_documents(asset_symbol)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_embeddings_space ON news_embeddings(embedding_space)"
            )

    def stats(self, embedding_space: Optional[str] = None) -> Dict[str, int]:
        with self._connect() as connection:
            document_count = connection.execute(
                "SELECT COUNT(*) FROM news_documents"
            ).fetchone()[0]
            embedding_count = connection.execute(
                "SELECT COUNT(*) FROM news_embeddings"
            ).fetchone()[0]
            active_count = 0
            if embedding_space:
                active_count = connection.execute(
                    "SELECT COUNT(*) FROM news_embeddings WHERE embedding_space = ?",
                    (embedding_space,),
                ).fetchone()[0]
        return {
            "document_count": int(document_count),
            "embedding_count": int(embedding_count),
            "active_embedding_count": int(active_count),
        }

    def documents_without_embedding(
        self, embedding_space: str, limit: Optional[int] = None
    ) -> List[Dict]:
        sql = """
            SELECT d.*
            FROM news_documents d
            LEFT JOIN news_embeddings e
              ON e.document_id = d.id AND e.embedding_space = ?
            WHERE e.document_id IS NULL
            ORDER BY d.id ASC
        """
        params = [embedding_space]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def upsert_documents(self, chunks: Iterable[Dict]) -> Dict:
        now = utc_now_iso()
        inserted_count = 0
        duplicate_count = 0
        stored_documents = []
        with self._connect() as connection:
            for chunk in chunks:
                text = re.sub(r"\s+", " ", (chunk.get("chunk_text") or "").strip())
                if not text:
                    continue
                chunk_hash = content_hash(text)
                existing = connection.execute(
                    "SELECT id FROM news_documents WHERE content_hash = ?", (chunk_hash,)
                ).fetchone()
                if existing:
                    document_id = int(existing["id"])
                    duplicate_count += 1
                    connection.execute(
                        """
                        UPDATE news_documents
                        SET last_seen_at = ?, seen_count = seen_count + 1
                        WHERE id = ?
                        """,
                        (now, document_id),
                    )
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO news_documents (
                            content_hash, asset_symbol, asset_name, headline, chunk_text,
                            source_url, published_at, first_seen_at, last_seen_at, seen_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            chunk_hash,
                            chunk.get("asset_symbol") or "UNKNOWN",
                            chunk.get("asset_name"),
                            chunk.get("headline"),
                            text,
                            chunk.get("source_url"),
                            chunk.get("published_at"),
                            now,
                            now,
                        ),
                    )
                    document_id = int(cursor.lastrowid)
                    inserted_count += 1
                stored_documents.append(
                    {
                        "id": document_id,
                        "content_hash": chunk_hash,
                        "asset_symbol": chunk.get("asset_symbol") or "UNKNOWN",
                        "asset_name": chunk.get("asset_name"),
                        "headline": chunk.get("headline"),
                        "chunk_text": text,
                    }
                )
        return {
            "inserted_count": inserted_count,
            "duplicate_count": duplicate_count,
            "documents": stored_documents,
        }

    def upsert_embeddings(
        self,
        documents: Sequence[Dict],
        vectors: Sequence[Sequence[float]],
        embedding_space: str,
    ) -> int:
        if len(documents) != len(vectors):
            raise ValueError("documents and vectors must have the same length")
        if not documents:
            return 0
        now = utc_now_iso()
        stored_count = 0
        with self._connect() as connection:
            for document, vector in zip(documents, vectors):
                if not vector:
                    continue
                connection.execute(
                    """
                    INSERT INTO news_embeddings (
                        document_id, embedding_space, dimension, vector, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(document_id, embedding_space) DO UPDATE SET
                        dimension = excluded.dimension,
                        vector = excluded.vector,
                        created_at = excluded.created_at
                    """,
                    (
                        int(document["id"]),
                        embedding_space,
                        len(vector),
                        sqlite3.Binary(_pack_vector(vector)),
                        now,
                    ),
                )
                stored_count += 1
        return stored_count

    def search(
        self,
        query_vector: Sequence[float],
        embedding_space: str,
        limit: int = 3,
    ) -> List[Dict]:
        if not query_vector or limit <= 0:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*, e.dimension, e.vector
                FROM news_documents d
                JOIN news_embeddings e ON e.document_id = d.id
                WHERE e.embedding_space = ? AND e.dimension = ?
                """,
                (embedding_space, len(query_vector)),
            ).fetchall()

        ranked = []
        for row in rows:
            vector = _unpack_vector(row["vector"], int(row["dimension"]))
            similarity = cosine_similarity(query_vector, vector)
            item = {key: row[key] for key in row.keys() if key != "vector"}
            item["similarity"] = round(float(similarity), 4)
            ranked.append(item)
        ranked.sort(key=lambda item: (-item["similarity"], -item["id"]))
        return ranked[:limit]


class EmbeddingService:
    """Uses Ollama embeddings when available and a stable local vector fallback."""

    def __init__(
        self,
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        force_hash: bool = False,
    ):
        self.requested_model = (
            model
            if model is not None
            else os.environ.get("OLLAMA_EMBED_MODEL", DEFAULT_EMBED_MODEL)
        )
        self.endpoint = endpoint or os.environ.get("OLLAMA_EMBED_URL", OLLAMA_EMBED_URL)
        self.timeout_seconds = timeout_seconds or int(
            os.environ.get("OLLAMA_EMBED_TIMEOUT_SECONDS", "120")
        )
        self.force_hash = force_hash
        self.backend = None
        self.embedding_space = None
        self.dimension = None
        self.warning = None
        self._lock = threading.Lock()

    def _hash_embed(self, text: str) -> List[float]:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        features = list(tokens)
        features.extend(
            f"{tokens[index]}::{tokens[index + 1]}"
            for index in range(max(0, len(tokens) - 1))
        )
        vector = [0.0] * HASH_EMBED_DIMENSION
        for feature in features:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % HASH_EMBED_DIMENSION
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def _ollama_embed(self, texts: Sequence[str]) -> List[List[float]]:
        payload = json.dumps(
            {
                "model": self.requested_model,
                "input": list(texts),
                "truncate": True,
                "keep_alive": "10m",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama embedding HTTP {exc.code}: {detail}") from exc
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("Ollama returned an invalid embedding response")
        vectors = []
        for vector in embeddings:
            if not isinstance(vector, list) or not vector:
                raise RuntimeError("Ollama returned an empty embedding vector")
            vectors.append([float(value) for value in vector])
        return vectors

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        clean_texts = [str(text or "") for text in texts]
        if not clean_texts:
            return []
        with self._lock:
            if self.backend is None:
                if not self.force_hash and self.requested_model:
                    try:
                        vectors = self._ollama_embed(clean_texts)
                        self.backend = "ollama"
                        self.dimension = len(vectors[0])
                        self.embedding_space = (
                            f"ollama:{self.requested_model}:{self.dimension}"
                        )
                        return vectors
                    except Exception as exc:
                        self.warning = str(exc)
                self.backend = "local_hash"
                self.dimension = HASH_EMBED_DIMENSION
                self.embedding_space = f"local-hash-v1:{HASH_EMBED_DIMENSION}"

            if self.backend == "ollama":
                vectors = self._ollama_embed(clean_texts)
                if any(len(vector) != self.dimension for vector in vectors):
                    raise RuntimeError("Ollama embedding dimension changed during the run")
                return vectors
            return [self._hash_embed(text) for text in clean_texts]

    def details(self) -> Dict:
        if self.backend == "ollama":
            active_model = self.requested_model
        elif self.backend == "local_hash":
            active_model = "local-hash-v1"
        else:
            active_model = "not_initialized"
        return {
            "backend": self.backend or "not_initialized",
            "model": active_model,
            "requested_ollama_model": self.requested_model,
            "embedding_space": self.embedding_space,
            "dimension": self.dimension,
            "fallback_used": self.backend == "local_hash" and not self.force_hash,
            "local_vector_encoder": self.backend == "local_hash",
            "warning": self.warning,
        }
