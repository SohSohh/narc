"""
retrieval.py
============
The embed -> Qdrant search -> (optional) neighbor-chunk expansion ->
(optional) rerank retrieval pipeline. This is the original main.py's logic,
unchanged in behavior, refactored so it:

  - reads config from config.py and shared clients from state.py instead of
    module-level globals, so other modules (chat.py) can use it cleanly
  - exposes `run_search()` as a plain function, not just an HTTP route, so
    chat.py can call it directly (no self-HTTP-call, no duplicated logic)

Pipeline recap:
    query --> rewrite/translate/split via Ollama (llama3.2)
          --> one or more clean English sub-queries
          --> [per sub-query, run concurrently] embed (bge-large)
                                                  --> Qdrant search
                                                  --> (optional) neighbor-chunk expansion
                                                  --> rerank via Cohere Rerank (hosted API)
          --> merge sub-query results (dedup by chunk_id, best score wins)
          --> chunks

See config.py for all tunables (env vars) and their defaults/rationale.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from qdrant_client.http.exceptions import ResponseHandlingException
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

from . import config
from .state import clients

log = logging.getLogger("rag-backend.retrieval")

router = APIRouter()

# ---------------------------------------------------------------------------
# Query-rewrite + split prompt
# ---------------------------------------------------------------------------
# Goal: turn ANY raw input -- any language or script, romanized/transliterated
# text (Roman Urdu, Hinglish, etc.), mixed-language text, chat-style junk,
# typos, or an already-clean query -- into one or more clean English
# sentences optimized for cosine-similarity retrieval against our chunk
# embeddings. The chunks were embedded from plain English passage text
# (title/breadcrumb/source_url are metadata alongside, not part of the
# embedded text), so each rewritten query should read like a natural,
# information-dense English statement of the need -- not a boolean keyword
# string, not a question addressed to a chatbot, and not padded with
# meta-commentary.
#
# Splitting: if the raw input bundles multiple DISTINCT information needs
# (e.g. two unrelated questions joined by "and", or a list of separate
# asks), a single embedding vector for the whole thing tends to land
# between both topics and retrieve mediocre matches for either. Instead the
# model splits such input into separate, independently-searchable
# sub-queries -- each one gets embedded and searched on its own by the
# caller, and the chunk results are merged back together afterwards.
# Queries that only have one information need (the common case) still
# produce a single-item list, same as before.
#
# Structured JSON-only output is used (rather than free text) so a stray
# preamble like "Sure, here's the rewritten query:" can't leak into what gets
# embedded -- that preamble text would itself get embedded and pull the
# vector away from the actual information need.

REWRITE_SYSTEM_PROMPT = """You are a query normalization engine for a semantic search system. Your job is to take a raw user query and turn it into one or more clean, information-dense ENGLISH search queries optimized for embedding-based semantic retrieval. You do not answer questions, hold a conversation, or add commentary.

The input may be:
- In any language or script (English, Urdu script, Arabic, Chinese, etc.)
- Romanized/transliterated (Roman Urdu, Hinglish, or similar "type it how it sounds" spellings)
- A mix of languages or scripts in the same sentence (common code-switching, e.g. English mixed with Roman Urdu)
- Casual, chat-style text with greetings, filler, disfluencies, typos, or slang
- A SINGLE information need, or MULTIPLE distinct information needs bundled together (e.g. two unrelated questions joined by "and", separated by a comma, or listed one after another)
- Already a clean, well-formed English query
- Empty, gibberish, or carrying no extractable information need
- May include a "Recent conversation:" block followed by "New message:" -- if so, use the conversation only to resolve references (pronouns like "that"/"it", implied subjects) in the new message; the output should describe only the new message's information need, not the whole conversation

Rules:
1. Detect the language/script no matter what it is, and produce output ONLY in English. Translate meaning, not literal words -- preserve intent, not phrasing.
2. Preserve every piece of actual information need: entities, names, numbers, dates, program/course/department names, requirements, comparisons, constraints. Never drop meaningful content.
3. Strip anything that carries no search-relevant meaning: greetings ("hi", "assalam o alaikum"), filler ("um", "like"), politeness markers ("please", "can you", "kindly", "thanks", "sir", "bhai"), disfluencies, and meta-commentary about wanting an answer ("I was wondering if you could tell me", "just curious").
4. Do NOT answer the question. Do NOT add facts, assumptions, or specifics that were not present in the original query. Do NOT hallucinate.
5. Only expand an abbreviation if you are confident of its meaning from context; otherwise leave it as written.
6. If the query is already a clean, well-formed English search query, return it essentially unchanged -- only remove genuine junk, don't paraphrase for its own sake.
7. If the input is empty, pure gibberish, or has no extractable information need, return it back verbatim as the single entry in the list (untranslated, unmodified) so the caller can decide how to handle it -- do not invent a query.
8. Write each result as a natural sentence or noun phrase a person would say when describing what they want to find -- not a list of keywords, not a question starting with "What/How/Can", not addressed to anyone.
9. SPLITTING: If the input contains two or more DISTINCT information needs (different topics/entities/questions that just happen to be asked together), output one rewritten query per need, each self-contained enough to be searched on its own -- carry over shared context (e.g. "NUST", a program name) into each split-out query rather than leaving it implicit. If the input is really one information need (even if it has several clauses or details), keep it as ONE query -- do not split a single need apart just because it's long or has multiple details. Never produce more than 4 queries; if there are genuinely more than 4 distinct needs, keep the 4 most important and drop the rest.
10. Output MUST be a single JSON object and NOTHING else -- no markdown, no code fences, no explanation before or after: {"queries": ["...", "..."]}. The list has one entry for a single information need, or multiple entries only when the input truly bundles distinct needs.

Examples:

Input: "hi can u plz tell me what documents are needed for admission in NUST for undergrad programs thanx"
Output: {"queries": ["documents required for undergraduate admission at NUST"]}

Input: "NUST mein daakhla lenay k liye kitni fees lagti hai and kab tak form submit karna hota hai"
Output: {"queries": ["NUST admission fee amount and form submission deadline"]}

Input: "مجھے این یو ایس ٹی میں داخلے کے اہلیت کے معیار کے بارے میں بتائیں"
Output: {"queries": ["NUST admission eligibility criteria"]}

Input: "bhai NUST ka hostel available hai kya for girls, aur uski fees kitni hai"
Output: {"queries": ["girls hostel availability and fees at NUST"]}

Input: "what are the admission requirements for the CS department, and also is there a boys hostel and how much does it cost"
Output: {"queries": ["admission requirements for the Computer Science department at NUST", "boys hostel availability and cost at NUST"]}

Input: "NUST scholarship criteria for undergrads, library timings, and how do I contact the registrar's office"
Output: {"queries": ["NUST undergraduate scholarship eligibility criteria", "NUST library timings", "how to contact the NUST registrar's office"]}

Input: "scholarship"
Output: {"queries": ["scholarship"]}

Input: "asdkfj random text not a question"
Output: {"queries": ["asdkfj random text not a question"]}

Input: ""
Output: {"queries": [""]}
"""

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language query to search for, any language")
    top_k: int = Field(15, ge=1, le=100, description="Number of chunks to return")
    score_threshold: float | None = Field(
        None, description="Optional minimum cosine similarity score (0-1) to include a result"
    )
    filter: dict | None = Field(
        None, description="Optional raw Qdrant filter dict (e.g. {'must': [...]}), passed through as-is"
    )
    skip_rewrite: bool = Field(
        False, description="If true, embed the raw query as-is and skip the llama3.2 rewrite/split step "
                            "(the query is always treated as a single query in this case)"
    )
    expand_context: bool = Field(
        True, description="If true, pull in neighboring/whole-page chunks around each vector match "
                           "so answers that live in a nearby chunk on the same page aren't missed"
    )
    rerank: bool = Field(
        True, description="If true, rerank retrieved chunks with Cohere Rerank "
                           "before returning -- generally much more precise than cosine similarity alone"
    )
    rerank_top_n: int | None = Field(
        None, description="How many chunks to keep after reranking. Defaults to the RERANK_TOP_N env var. "
                           "Has no effect if rerank=false."
    )


class Chunk(BaseModel):
    score: float | None  # None for chunks pulled in via expansion rather than a direct vector match
    matched: bool = True  # True if this chunk was a direct vector hit, False if pulled in via expansion
    rerank_score: float | None = None  # cross-encoder relevance score, None if reranking was skipped/failed
    chunk_id: str | None = None
    title: str | None = None
    breadcrumb: str | None = None
    source_url: str | None = None
    text: str | None = None
    payload: dict


class SearchResponse(BaseModel):
    query: str  # original, as received
    queries: list[str]  # the one or more rewritten sub-queries actually searched (may be [query] unchanged)
    rewrite_applied: bool
    results: list[Chunk]  # merged, deduped results across all sub-queries in `queries`


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def split_query(text: str) -> tuple[list[str], bool]:
    """Rewrite/translate a raw query into one or more clean English search
    queries via llama3.2, splitting it into separate sub-queries if it
    bundles multiple distinct information needs. Returns (queries, applied).
    `queries` always has at least one entry. On any failure -- unreachable
    model, malformed JSON, empty result -- falls back to a single-item list
    containing the original text with applied=False rather than failing the
    whole search request."""
    try:
        resp = await clients["http"].post(
            f"{config.OLLAMA_URL}/api/chat",
            json={
                "model": config.LLM_MODEL,
                "messages": [
                    {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=config.LLM_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        log.warning(f"Query rewrite failed ({e.response.status_code}: {e.response.text[:200]}) -- using raw query")
        return [text], False
    except httpx.RequestError as e:
        log.warning(f"Query rewrite unreachable ({e}) -- using raw query")
        return [text], False

    try:
        content = resp.json()["message"]["content"]
        parsed = json.loads(content)["queries"]
        if not isinstance(parsed, list):
            raise TypeError(f"'queries' was {type(parsed).__name__}, expected list")
        queries = [q.strip() for q in parsed if isinstance(q, str) and q.strip()]
    except (KeyError, json.JSONDecodeError, TypeError) as e:
        log.warning(f"Query rewrite returned unparseable output ({e}) -- using raw query")
        return [text], False

    if not queries:
        # Model correctly identified no extractable info need (or returned
        # an empty list) -- fall back to the raw text rather than embedding
        # nothing.
        return [text], False

    if len(queries) > config.MAX_SUBQUERIES:
        log.warning(f"Query split into {len(queries)} sub-queries, capping to MAX_SUBQUERIES={config.MAX_SUBQUERIES}")
        queries = queries[: config.MAX_SUBQUERIES]

    return queries, True


async def embed_query(text: str) -> list[float]:
    """Call Ollama's /api/embed to embed a single (already-rewritten) query string."""
    prompt = f"{config.QUERY_INSTRUCTION_PREFIX}{text}" if config.QUERY_INSTRUCTION_PREFIX else text
    try:
        resp = await clients["http"].post(
            f"{config.OLLAMA_URL}/api/embed",
            json={"model": config.EMBED_MODEL, "input": [prompt]},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama returned {e.response.status_code}: {e.response.text[:300]}",
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Ollama at {config.OLLAMA_URL}: {e}")

    data = resp.json()
    embeddings = data.get("embeddings")
    if not embeddings or not embeddings[0]:
        raise HTTPException(status_code=502, detail=f"Ollama response missing 'embeddings': {data}")

    vector = embeddings[0]
    if len(vector) != config.EMBED_DIM:
        log.warning(f"Embedding dim {len(vector)} != expected {config.EMBED_DIM} -- check EMBED_MODEL / EMBED_DIM.")
    return vector


async def fetch_chunks_by_source(
    source_url: str, chunk_indices: list[int] | None, limit: int
) -> list[Any]:
    """Fetch chunks belonging to one source_url via Qdrant's scroll API (a
    payload filter, not a vector search -- no embedding needed for this).
    If chunk_indices is given, restricts to those indices (windowed
    expansion); otherwise returns the whole page (full-page expansion)."""
    conditions = [FieldCondition(key="source_url", match=MatchValue(value=source_url))]
    if chunk_indices is not None:
        conditions.append(FieldCondition(key="chunk_index", match=MatchAny(any=chunk_indices)))

    points, _ = await clients["qdrant"].scroll(
        collection_name=config.QDRANT_COLLECTION,
        scroll_filter=Filter(must=conditions),
        limit=limit,
        with_payload=True,
    )
    return points


async def expand_context(hit_points: list[Any]) -> list[dict]:
    """Auto-merging / parent-document style expansion, implemented directly
    against the flat source_url / chunk_index / n_chunks_from_source_text
    metadata already on each chunk (no separate parent index required).

    For each unique source_url among the vector-search hits:
      - if the page is short (<= EXPAND_FULL_PAGE_MAX_CHUNKS total chunks),
        pull every chunk on that page -- cheap, and guarantees a nearby fact
        (e.g. an address a couple chunks away from the matched "About this
        office" chunk) isn't missed.
      - otherwise, pull a +/- EXPAND_WINDOW window of chunk_index values
        around each matched chunk on that page.

    Returns a flat list of dicts (one per chunk, deduped by chunk_id),
    ordered so chunks from the same source stay grouped and in reading
    order -- fragmented, out-of-order context otherwise confuses the LLM
    that eventually consumes this.
    """
    # chunk_id -> {"score": float|None, "payload": dict, "matched": bool}
    merged: dict[str, dict] = {}

    by_source: dict[str, list[Any]] = {}
    for p in hit_points:
        payload = p.payload or {}
        cid = payload.get("chunk_id") or str(p.id)
        merged[cid] = {"score": p.score, "payload": payload, "matched": True}
        src = payload.get("source_url")
        if src:
            by_source.setdefault(src, []).append(p)

    for source_url, points in by_source.items():
        sample_payload = points[0].payload or {}
        total_chunks = sample_payload.get("n_chunks_from_source_text")
        matched_indices = {
            pt.payload.get("chunk_index") for pt in points
            if pt.payload and pt.payload.get("chunk_index") is not None
        }

        try:
            if isinstance(total_chunks, int) and total_chunks <= config.EXPAND_FULL_PAGE_MAX_CHUNKS:
                extra = await fetch_chunks_by_source(
                    source_url, chunk_indices=None, limit=min(total_chunks, config.EXPAND_MAX_CHUNKS_PER_SOURCE)
                )
            elif matched_indices:
                wanted = {
                    i for idx in matched_indices
                    for i in range(idx - config.EXPAND_WINDOW, idx + config.EXPAND_WINDOW + 1)
                    if i >= 0
                }
                extra = await fetch_chunks_by_source(
                    source_url, chunk_indices=sorted(wanted), limit=config.EXPAND_MAX_CHUNKS_PER_SOURCE
                )
            else:
                continue
        except Exception as e:
            # Expansion is a best-effort enrichment -- a failure here should
            # never take down the underlying search results.
            log.warning(f"Chunk expansion failed for source '{source_url}': {e}")
            continue

        for ep in extra:
            payload = ep.payload or {}
            cid = payload.get("chunk_id") or str(ep.id)
            if cid not in merged:
                merged[cid] = {"score": None, "payload": payload, "matched": False}

    # Group by source_url, order groups by their best score (so the
    # strongest-matching page still leads), and order chunks within a group
    # by chunk_index so expanded context reads in natural page order.
    groups: dict[str, list[dict]] = {}
    for info in merged.values():
        groups.setdefault(info["payload"].get("source_url", ""), []).append(info)

    ordered_groups = sorted(
        groups.values(),
        key=lambda g: max((i["score"] for i in g if i["score"] is not None), default=0.0),
        reverse=True,
    )

    ordered: list[dict] = []
    for group in ordered_groups:
        group.sort(key=lambda i: (i["payload"].get("chunk_index") is None, i["payload"].get("chunk_index", 0)))
        ordered.extend(group)

    return ordered[: config.EXPAND_MAX_TOTAL_CHUNKS]


async def rerank_chunks(query: str, chunks: list[dict], top_n: int) -> list[dict]:
    """Re-score `chunks` against `query` with Cohere's hosted Rerank API
    (COHERE_RERANK_MODEL, a cross-encoder-style model) and return them in
    descending relevance order, trimmed to `top_n`.

    Cross-encoder rerankers score a (query, passage) pair jointly, which is
    far more precise than the cosine similarity used for the initial vector
    search -- but it's also too slow to run over the whole collection, hence
    doing it as a second pass over just the candidates. Chunks with no
    `text` in their payload can't be scored and are left out of the
    reranked set entirely (there's nothing to rank). On any failure -- no
    API key configured, network error, bad response -- falls back to the
    original (vector-search / expansion) order rather than failing the
    whole request."""
    cohere_client = clients.get("cohere")
    if not cohere_client:
        log.warning("COHERE_API_KEY not configured -- skipping rerank, keeping original order")
        return chunks[:top_n]

    indexed = [(i, c) for i, c in enumerate(chunks) if c["payload"].get("text")]
    if not indexed:
        return chunks[:top_n]

    texts = [c["payload"]["text"] for _, c in indexed]
    try:
        response = await cohere_client.rerank(
            model=config.COHERE_RERANK_MODEL,
            query=query,
            documents=texts,
            top_n=min(top_n, len(texts)),
            request_options={"timeout_in_seconds": config.RERANK_TIMEOUT},
        )
    except Exception as e:
        # Cohere's SDK raises its own exception types (ApiError, etc.)
        # rather than httpx's, so this is caught broadly on purpose --
        # any failure here should degrade to the original order, not
        # take down the request.
        log.warning(f"Cohere rerank failed ({e}) -- keeping original order")
        return chunks[:top_n]

    reordered = []
    for r in response.results:  # already sorted by relevance_score descending
        _, chunk = indexed[r.index]
        reordered.append({**chunk, "rerank_score": r.relevance_score})

    return reordered


async def run_single_query_pipeline(query_text: str, req: SearchRequest) -> list[dict]:
    """Run the embed --> Qdrant search --> (optional) expansion --> (optional)
    rerank pipeline for a single already-rewritten sub-query string.
    Reranking, when enabled, scores chunks against this specific sub-query
    text -- not some blended multi-topic string -- which is why each
    sub-query gets its own full pipeline run rather than merging candidates
    before reranking. Returns a list of chunk dicts (the same
    score/payload/matched[/rerank_score] shape used by expand_context and
    rerank_chunks)."""
    vector = await embed_query(query_text)

    try:
        hits = await clients["qdrant"].query_points(
            collection_name=config.QDRANT_COLLECTION,
            query=vector,
            limit=req.top_k,
            score_threshold=req.score_threshold,
            query_filter=req.filter,
            with_payload=True,
        )
    except ResponseHandlingException as e:
        raise HTTPException(status_code=502, detail=f"Qdrant query failed: {e}")

    if config.ENABLE_CHUNK_EXPANSION and req.expand_context:
        merged = await expand_context(hits.points)
    else:
        merged = [
            {"score": p.score, "payload": p.payload or {}, "matched": True}
            for p in hits.points
        ]

    if config.ENABLE_RERANK and req.rerank:
        merged = await rerank_chunks(query_text, merged, req.rerank_top_n or config.RERANK_TOP_N)

    return merged


def merge_subquery_results(results_per_query: list[list[dict]]) -> list[dict]:
    """Merge the chunk-dict lists produced by running each sub-query through
    run_single_query_pipeline() into one combined, deduped list -- this is
    the "send the chunks together" step for a query that got split into
    several sub-queries.

    A chunk pulled in by more than one sub-query (e.g. two sub-queries about
    the same NUST department landing on the same page) is merged into a
    single entry: `matched` is True if any sub-query matched it directly
    (rather than via expansion), and `score`/`rerank_score` keep the best
    (highest) value seen across sub-queries, so a chunk that scored well for
    one sub-query isn't dragged down by a weaker score from another.

    Final order is by rerank_score descending (chunks with no rerank_score
    sort after ones that have it), then by score descending, so the
    strongest matches lead regardless of which sub-query surfaced them.
    """
    merged: dict[str, dict] = {}
    for chunk_list in results_per_query:
        for item in chunk_list:
            payload = item["payload"]
            cid = payload.get("chunk_id") or f"{payload.get('source_url')}::{payload.get('chunk_index')}"
            existing = merged.get(cid)
            if existing is None:
                merged[cid] = dict(item)
                continue

            existing["matched"] = existing["matched"] or item["matched"]
            if item.get("score") is not None and (
                existing.get("score") is None or item["score"] > existing["score"]
            ):
                existing["score"] = item["score"]
            if item.get("rerank_score") is not None and (
                existing.get("rerank_score") is None or item["rerank_score"] > existing["rerank_score"]
            ):
                existing["rerank_score"] = item["rerank_score"]

    def sort_key(item: dict) -> tuple:
        rr, sc = item.get("rerank_score"), item.get("score")
        return (rr is not None, rr if rr is not None else float("-inf"),
                sc is not None, sc if sc is not None else float("-inf"))

    return sorted(merged.values(), key=sort_key, reverse=True)


async def run_search(req: SearchRequest) -> SearchResponse:
    """The actual retrieval pipeline entrypoint, factored out of the
    /search route so chat.py can call it directly (no self-HTTP-call, and
    no second copy of this logic to keep in sync)."""
    if config.ENABLE_QUERY_REWRITE and not req.skip_rewrite:
        queries, applied = await split_query(req.query)
    else:
        queries, applied = [req.query], False

    # Each sub-query runs the full embed/search/expand/rerank pipeline
    # independently (and concurrently) -- see run_single_query_pipeline --
    # then the per-sub-query chunk lists are merged and deduped below so a
    # bundled multi-part question comes back as one combined result set.
    results_per_query = await asyncio.gather(
        *(run_single_query_pipeline(q, req) for q in queries)
    )
    merged = merge_subquery_results(list(results_per_query))

    results = []
    for item in merged:
        payload = item["payload"]
        results.append(
            Chunk(
                score=item["score"],
                matched=item["matched"],
                rerank_score=item.get("rerank_score"),
                chunk_id=payload.get("chunk_id"),
                title=payload.get("title"),
                breadcrumb=payload.get("breadcrumb"),
                source_url=payload.get("source_url"),
                text=payload.get("text"),
                payload=payload,
            )
        )

    return SearchResponse(
        query=req.query,
        queries=queries,
        rewrite_applied=applied,
        results=results,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health")
async def health():
    status = {"ollama": "unknown", "qdrant": "unknown", "reranker": "unknown"}
    try:
        r = await clients["http"].get(f"{config.OLLAMA_URL}/api/tags")
        if r.status_code == 200:
            tags = {m["name"].split(":")[0] for m in r.json().get("models", [])}
            missing = [m for m in (config.EMBED_MODEL, config.LLM_MODEL) if m.split(":")[0] not in tags]
            status["ollama"] = "ok" if not missing else f"reachable but missing model(s): {missing}"
        else:
            status["ollama"] = f"error ({r.status_code})"
    except Exception as e:
        status["ollama"] = f"unreachable ({e})"

    try:
        ok = await clients["qdrant"].collection_exists(config.QDRANT_COLLECTION)
        status["qdrant"] = "ok" if ok else f"collection '{config.QDRANT_COLLECTION}' not found"
    except Exception as e:
        status["qdrant"] = f"unreachable ({e})"

    # Cohere's Rerank API has no cheap unauthenticated health-check endpoint
    # worth calling on every /health hit, so this just confirms a key is
    # configured rather than round-tripping to api.cohere.com. An invalid
    # key would still surface at request time via rerank_chunks()'s
    # fallback-to-original-order path.
    status["reranker"] = "ok" if config.COHERE_API_KEY else "COHERE_API_KEY not configured"
    if not config.ENABLE_RERANK:
        status["reranker"] += " (disabled via ENABLE_RERANK)"

    status["redis"] = "ok" if config.REDIS_URL else "REDIS_URL not configured (/chat disabled)"
    status["groq"] = "ok" if config.GROQ_API_KEY else "GROQ_API_KEY not configured (/chat disabled)"

    healthy = status["ollama"] == "ok" and status["qdrant"] == "ok" and (
        not config.ENABLE_RERANK or status["reranker"].startswith("ok")
    )
    return {"healthy": healthy, **status}


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    return await run_search(req)