"""
main.py
=======
FastAPI app entrypoint. Wires together:
  - retrieval.router  -> GET /health, POST /search  (stateless retrieval pipeline)
  - chat.router        -> POST /chat, DELETE /chat/{session_id}
                           (stateful conversation on top of retrieval,
                           via Groq for generation + Redis for history)

All actual logic lives in retrieval.py, chat.py, session_store.py, and
groq_client.py -- this file only owns process lifecycle (shared clients)
and route registration. See config.py for every env var these modules read.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import cohere
import httpx
from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient

from . import chat
from . import config
from . import retrieval
from .session_store import SessionStore
from .state import clients

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rag-backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    clients["http"] = httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT)
    clients["qdrant"] = AsyncQdrantClient(
        url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY, timeout=config.REQUEST_TIMEOUT
    )
    # None if no key configured -- rerank_chunks() treats that as
    # "reranking unavailable" and falls back gracefully.
    clients["cohere"] = cohere.AsyncClientV2(api_key=config.COHERE_API_KEY) if config.COHERE_API_KEY else None

    log.info(
        f"Ollama:  {config.OLLAMA_URL} (embed={config.EMBED_MODEL}, llm={config.LLM_MODEL}, "
        f"rewrite_enabled={config.ENABLE_QUERY_REWRITE})"
    )
    log.info(f"Qdrant:  {config.QDRANT_URL} (collection={config.QDRANT_COLLECTION})")
    log.info(
        f"Reranker: Cohere {config.COHERE_RERANK_MODEL} "
        f"(enabled={config.ENABLE_RERANK}, configured={bool(config.COHERE_API_KEY)}, top_n={config.RERANK_TOP_N})"
    )
    log.info(f"Groq: {config.GROQ_MODEL} (configured={bool(config.GROQ_API_KEY)})")

    # Chat is opt-in: if REDIS_URL isn't set, /chat and /chat/{id} stay
    # mounted but return a 500 explaining why, rather than the whole
    # service failing to start -- retrieval-only deployments still work.
    if config.REDIS_URL:
        chat.session_store = SessionStore(config.REDIS_URL)
        log.info("Redis: configured -- /chat enabled")
    else:
        log.warning("REDIS_URL not set -- /chat is disabled (retrieval-only mode)")

    yield

    await clients["http"].aclose()
    await clients["qdrant"].close()
    if chat.session_store is not None:
        await chat.session_store.close()


app = FastAPI(title="narc", lifespan=lifespan)
app.include_router(retrieval.router)
app.include_router(chat.router)