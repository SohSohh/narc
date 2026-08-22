"""
groq_client.py
==============
Thin async wrapper around Groq's OpenAI-compatible chat completions
endpoint, used only for the final answer-generation step in chat.py.

This is a separate model/call from the llama3.2-on-Ollama query-rewrite
step in retrieval.py -- that one is a small, cheap, local classification-
ish call used for every /search request; this one is the larger generation
call (Groq gpt-oss-120b) used only for /chat, where an actual prose answer
needs to be produced for the user.

Reuses the shared httpx client from state.clients (same pattern as the
Ollama calls in retrieval.py) instead of opening a new client per request.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException

from . import config
from .state import clients

log = logging.getLogger("rag-backend.groq_client")


async def generate_answer(system_prompt: str, messages: list[dict[str, str]]) -> str:
    """`messages` is the chat history + current user turn (role/content
    dicts) that goes after the system prompt. Raises HTTPException(502) on
    any failure -- unlike the query-rewrite step, there's no sensible
    fallback for the final answer itself (nothing to show the user instead
    of an answer), so this propagates rather than silently degrading."""
    if not config.GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured")

    try:
        resp = await clients["http"].post(
            config.GROQ_API_URL,
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={
                "model": config.GROQ_MODEL,
                "messages": [{"role": "system", "content": system_prompt}, *messages],
                "temperature": config.GROQ_TEMPERATURE,
                "max_tokens": config.GROQ_MAX_TOKENS,
            },
            timeout=config.GROQ_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        log.error(f"Groq returned {e.response.status_code}: {e.response.text[:300]}")
        raise HTTPException(status_code=502, detail=f"Groq API error: {e.response.status_code}")
    except httpx.RequestError as e:
        log.error(f"Groq unreachable: {e}")
        raise HTTPException(status_code=502, detail=f"Could not reach Groq: {e}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise HTTPException(status_code=502, detail=f"Malformed Groq response: {e}")