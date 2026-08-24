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
from fastapi.middleware.cors import CORSMiddleware
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
    if config.FRONTEND_ORIGINS:
        log.info(f"CORS: allowing {config.FRONTEND_ORIGINS} (credentials enabled -- session cookie will work)")
    else:
        log.warning(
            "FRONTEND_ORIGINS not set -- CORS is disabled. Cross-origin browser requests will be "
            "blocked, and the session cookie won't be sent/accepted even same-origin fetches that "
            "omit it. Set FRONTEND_ORIGINS to your frontend's origin(s) before going live."
        )

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

# allow_credentials=True is required for the browser to send/accept the
# HttpOnly session cookie cross-origin -- but per the CORS spec that only
# works with explicit origin(s), never "*". If FRONTEND_ORIGINS is empty,
# CORSMiddleware is skipped entirely (see the startup warning above) rather
# than falling back to a wildcard that would silently break cookies anyway.
if config.FRONTEND_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

app.include_router(retrieval.router)
app.include_router(chat.router)