"""
rag_backend/main.py
====================
FastAPI service that turns a plain-text query into relevant chunks from
Qdrant:

    query --> embed via Ollama (bge-large-en-v1.5) --> Qdrant search --> chunks

Expects:
  - An Ollama server (e.g. the `ollama-embeddings` container from
    docker-compose.yml) with the bge-large-en-v1.5 model pulled.
  - A Qdrant collection populated by upload_embeddings_to_qdrant.py, with
    1024-dim cosine vectors and payload containing at least `text`
    (if uploaded with --chunks) plus chunk_id/title/breadcrumb/source_url.

Config (env vars, all optional -- see .env.example):
  OLLAMA_URL                default: http://ollama-embeddings:11434
  EMBED_MODEL                default: bge-large-en-v1.5
  QUERY_INSTRUCTION_PREFIX   default: "Represent this sentence for searching relevant passages: "
  QDRANT_URL                 default: http://qdrant:6333
  QDRANT_API_KEY             default: None
  QDRANT_COLLECTION          default: nust_chunks
  EMBED_DIM                  default: 1024
  REQUEST_TIMEOUT            default: 30 (seconds, for the Ollama call)
"""

from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rag-backend")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama-embeddings:11434").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-large-en-v1.5")
# BGE models are trained with an instruction prefix on the QUERY side only
# (passages/chunks are embedded plain). Skipping this hurts retrieval quality
# noticeably -- keep it unless you embedded your chunks with a different
# convention and want symmetry instead.
QUERY_INSTRUCTION_PREFIX = os.environ.get(
    "QUERY_INSTRUCTION_PREFIX",
    "Represent this sentence for searching relevant passages: ",
)
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY") or None
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "nust_chunks")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# Lifespan: shared HTTP + Qdrant clients (created once, reused across requests)
# ---------------------------------------------------------------------------

clients: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    clients["http"] = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    clients["qdrant"] = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=REQUEST_TIMEOUT)
    log.info(f"Ollama:  {OLLAMA_URL} (model={EMBED_MODEL})")
    log.info(f"Qdrant:  {QDRANT_URL} (collection={QDRANT_COLLECTION})")
    yield
    await clients["http"].aclose()
    await clients["qdrant"].close()


app = FastAPI(title="RAG Search Backend", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language query to search for")
    top_k: int = Field(5, ge=1, le=100, description="Number of chunks to return")
    score_threshold: float | None = Field(
        None, description="Optional minimum cosine similarity score (0-1) to include a result"
    )
    filter: dict | None = Field(
        None, description="Optional raw Qdrant filter dict (e.g. {'must': [...]}), passed through as-is"
    )


class Chunk(BaseModel):
    score: float
    chunk_id: str | None = None
    title: str | None = None
    breadcrumb: str | None = None
    source_url: str | None = None
    text: str | None = None
    payload: dict


class SearchResponse(BaseModel):
    query: str
    results: list[Chunk]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def embed_query(text: str) -> list[float]:
    """Call Ollama's /api/embed to embed a single query string."""
    prompt = f"{QUERY_INSTRUCTION_PREFIX}{text}" if QUERY_INSTRUCTION_PREFIX else text
    try:
        resp = await clients["http"].post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": [prompt]},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama returned {e.response.status_code}: {e.response.text[:300]}",
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Ollama at {OLLAMA_URL}: {e}")

    data = resp.json()
    embeddings = data.get("embeddings")
    if not embeddings or not embeddings[0]:
        raise HTTPException(status_code=502, detail=f"Ollama response missing 'embeddings': {data}")

    vector = embeddings[0]
    if len(vector) != EMBED_DIM:
        log.warning(f"Embedding dim {len(vector)} != expected {EMBED_DIM} -- check EMBED_MODEL / EMBED_DIM.")
    return vector


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    status = {"ollama": "unknown", "qdrant": "unknown"}
    try:
        r = await clients["http"].get(f"{OLLAMA_URL}/api/tags")
        status["ollama"] = "ok" if r.status_code == 200 else f"error ({r.status_code})"
    except Exception as e:
        status["ollama"] = f"unreachable ({e})"

    try:
        ok = await clients["qdrant"].collection_exists(QDRANT_COLLECTION)
        status["qdrant"] = "ok" if ok else f"collection '{QDRANT_COLLECTION}' not found"
    except Exception as e:
        status["qdrant"] = f"unreachable ({e})"

    healthy = status["ollama"] == "ok" and status["qdrant"] == "ok"
    return {"healthy": healthy, **status}


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    vector = await embed_query(req.query)

    try:
        hits = await clients["qdrant"].query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            limit=req.top_k,
            score_threshold=req.score_threshold,
            query_filter=req.filter,
            with_payload=True,
        )
    except ResponseHandlingException as e:
        raise HTTPException(status_code=502, detail=f"Qdrant query failed: {e}")

    results = []
    for point in hits.points:
        payload = point.payload or {}
        results.append(
            Chunk(
                score=point.score,
                chunk_id=payload.get("chunk_id"),
                title=payload.get("title"),
                breadcrumb=payload.get("breadcrumb"),
                source_url=payload.get("source_url"),
                text=payload.get("text"),
                payload=payload,
            )
        )

    return SearchResponse(query=req.query, results=results)
