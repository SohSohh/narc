"""
session_store.py
=================
Redis-backed chat history store (works with any Redis, including Upstash's
TLS endpoint via a rediss:// URL).

Each session's history is kept as a single JSON-encoded list under
`chat:{session_id}`, capped at MAX_HISTORY_MESSAGES entries, with a sliding
TTL (SESSION_TTL_SECONDS) refreshed on every write so idle sessions expire
on their own -- no manual cleanup job needed.

Design note: one JSON blob per session (rather than a Redis list with N
separate entries) is deliberate. History is always read and written as one
unit per chat turn, so a single GET/SET round trip beats several list
operations -- and Upstash bills per-command, so fewer round trips also
means lower cost.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis

from .config import MAX_HISTORY_MESSAGES, SESSION_TTL_SECONDS

log = logging.getLogger("rag-backend.session_store")


class SessionStore:
    def __init__(self, url: str):
        if not url:
            raise ValueError("REDIS_URL is not configured")
        # decode_responses=True so we get str back from Redis, not bytes.
        self._client = redis.from_url(url, decode_responses=True)

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _key(session_id: str) -> str:
        return f"chat:{session_id}"

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Return the stored [{role, content}, ...] list, oldest first.
        Returns an empty list for a new/unknown/expired session, and also
        degrades to an empty list on a Redis error rather than raising --
        a chat request should still work (just without memory) if Redis
        has a hiccup."""
        try:
            raw = await self._client.get(self._key(session_id))
        except Exception as e:
            log.warning(f"Redis GET failed for session '{session_id}' ({e}) -- treating as empty history")
            return []
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning(f"Corrupt history JSON for session '{session_id}' -- discarding")
            return []

    async def append_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        """Append one user+assistant exchange, trim to the most recent
        MAX_HISTORY_MESSAGES entries, and refresh the TTL. Best-effort: a
        Redis failure here is logged and swallowed rather than failing the
        chat request, since losing history for one turn is much better
        than losing the answer the user is waiting for."""
        history = await self.get_history(session_id)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": assistant_message})
        if len(history) > MAX_HISTORY_MESSAGES:
            history = history[-MAX_HISTORY_MESSAGES:]
        try:
            await self._client.set(self._key(session_id), json.dumps(history), ex=SESSION_TTL_SECONDS)
        except Exception as e:
            log.warning(f"Redis SET failed for session '{session_id}' ({e}) -- history not persisted this turn")

    async def clear(self, session_id: str) -> None:
        try:
            await self._client.delete(self._key(session_id))
        except Exception as e:
            log.warning(f"Redis DELETE failed for session '{session_id}' ({e})")