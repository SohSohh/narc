"""
chat.py
=======
Stateful chat on top of the stateless retrieval pipeline in retrieval.py.
Combines: Redis-backed session history (session_store.py) + reference
resolution via the existing llama3.2 rewrite step + retrieval.run_search()
+ answer generation via Groq (groq_client.py).

Flow per request:
  1. Resolve the session id: prefer the HttpOnly cookie the browser sends
     automatically; fall back to session_id in the request body (for non-
     browser callers); otherwise mint a new one. An unrecognized/expired
     id isn't rejected -- it's just treated as a fresh session with empty
     history, since Redis is the real source of truth on validity.
  2. Load this session's history from Redis (empty list for a new session).
  3. Resolve the raw message against a short slice of recent history by
     feeding both into the existing split_query() rewrite step, so
     "what about that?" becomes a standalone, embeddable query. Only
     REWRITE_CONTEXT_MESSAGES worth of history is used here -- resolving a
     reference never needs the whole conversation, just the last exchange
     or two.
  4. Run the resolved query through the normal retrieval pipeline
     (retrieval.run_search) -- unchanged, no chat-specific special-casing.
  5. Generate the final answer with Groq, given the full kept history +
     the retrieved chunks + the new message.
  6. Persist the new user/assistant turn to Redis (sliding TTL), set/refresh
     the session cookie, and return -- including how many seconds are left
     before the session expires, so the frontend can show/reset a countdown
     without needing to read the (HttpOnly, JS-inaccessible) cookie itself.

Kept as a separate module/router from retrieval.py on purpose: retrieval
stays a stateless, independently reusable service (e.g. a future non-chat
consumer can still hit /search directly); this module is the only place
that knows about sessions, conversation history, and Groq.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import config
from .groq_client import generate_answer
from .retrieval import Chunk, SearchRequest, run_search, split_query
from .session_store import SessionStore

log = logging.getLogger("rag-backend.chat")

router = APIRouter()

# Set by main.py's lifespan (needs REDIS_URL, which may be unset in
# retrieval-only environments) -- None means /chat is unavailable.
session_store: SessionStore | None = None

ANSWER_SYSTEM_PROMPT = """You are the NUST admissions assistant. Answer the \
user's question using ONLY the provided context chunks -- do not use \
outside knowledge, and do not make anything up. If the context doesn't \
contain the answer, say so plainly rather than guessing. Be concise and \
direct. When useful, mention which page/source the information came from.\
"""


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's chat message")
    session_id: str | None = Field(
        None, description="Fallback session id for non-browser callers that can't rely on "
                           "cookies. Browsers should omit this -- the session cookie set on "
                           "the previous response is used automatically instead."
    )


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    resolved_query: str  # what was actually searched, after reference resolution
    sources: list[Chunk]
    expires_in_seconds: int  # time left on this session before Redis expires it; resets every turn


def _format_context_block(chunks: list[Chunk]) -> str:
    """Turn the top retrieved chunks into a numbered context block for the
    Groq prompt. Capped to ANSWER_CONTEXT_CHUNKS -- retrieval may return
    more (e.g. via expansion), but the generation prompt only needs the
    strongest few to stay fast, cheap, and within context limits."""
    lines = []
    for i, c in enumerate(chunks[: config.ANSWER_CONTEXT_CHUNKS], start=1):
        if not c.text:
            continue
        source = c.title or c.source_url or "unknown source"
        lines.append(f"[{i}] ({source})\n{c.text}")
    return "\n\n".join(lines) if lines else "(no relevant context found)"


async def _resolve_query(message: str, history: list[dict[str, str]]) -> str:
    """Fold a short slice of recent history into the raw message before it
    hits the normal split_query() rewrite step, so references like "that"
    or "it" resolve into something embeddable. This only changes what text
    goes INTO that step -- splitting into multiple sub-queries still works
    normally afterward."""
    recent = history[-config.REWRITE_CONTEXT_MESSAGES:]
    if not recent:
        return message

    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in recent)
    return f"Recent conversation:\n{transcript}\n\nNew message: {message}"


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, response: Response):
    if session_store is None:
        raise HTTPException(status_code=500, detail="Chat is unavailable: REDIS_URL is not configured")

    # Cookie wins if present (the normal browser path); req.session_id is
    # only there for non-browser callers. Either way, an id Redis doesn't
    # recognize (new, or expired) just resolves to empty history below --
    # no separate "is this valid" check needed.
    session_id = request.cookies.get(config.SESSION_COOKIE_NAME) or req.session_id or str(uuid.uuid4())
    history = await session_store.get_history(session_id)

    contextualized = await _resolve_query(req.message, history)
    # split_query() already falls back to its raw input on any Ollama
    # failure, so this can't blow up the request -- worst case, the
    # contextualized wrapper text goes straight to embedding un-rewritten.
    resolved_queries, _ = await split_query(contextualized)
    resolved_query = resolved_queries[0] if resolved_queries else req.message

    search_result = await run_search(SearchRequest(query=resolved_query))

    context_block = _format_context_block(search_result.results)
    groq_messages = [
        *history,
        {"role": "user", "content": f"Context:\n{context_block}\n\nQuestion: {req.message}"},
    ]
    answer = await generate_answer(ANSWER_SYSTEM_PROMPT, groq_messages)

    # Store the ORIGINAL user message (not the context-stuffed prompt sent
    # to Groq) so history stays a clean, human-readable transcript, and so
    # it can be reused as-is for future reference resolution.
    await session_store.append_turn(session_id, req.message, answer)

    # append_turn() refreshes the Redis key's TTL to the full
    # SESSION_TTL_SECONDS on every successful write, so that constant IS
    # the remaining lifetime right now -- no separate Redis TTL lookup
    # needed. (If the Redis write silently failed -- session_store logs and
    # swallows that -- this value would be slightly optimistic for that one
    # turn; harmless, since the next successful turn corrects it.)
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
    )

    return ChatResponse(
        session_id=session_id,
        answer=answer,
        resolved_query=resolved_query,
        sources=search_result.results[: config.ANSWER_CONTEXT_CHUNKS],
        expires_in_seconds=config.SESSION_TTL_SECONDS,
    )


@router.delete("/chat/{session_id}")
async def clear_session(session_id: str, response: Response):
    if session_store is None:
        raise HTTPException(status_code=500, detail="Chat is unavailable: REDIS_URL is not configured")
    await session_store.clear(session_id)
    response.delete_cookie(config.SESSION_COOKIE_NAME)
    return {"cleared": session_id}