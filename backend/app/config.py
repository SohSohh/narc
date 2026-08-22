"""
config.py
=========
All environment-driven configuration for the service, in one place so
every module (retrieval, chat, session store, groq client) reads from a
single source instead of each re-parsing os.environ.
"""
import os

# --- Ollama (embeddings + query rewrite) ---
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama-embeddings:11434").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-large")
# BGE models are trained with an instruction prefix on the QUERY side only
# (passages/chunks are embedded plain). Skipping this hurts retrieval quality
# noticeably -- keep it unless you embedded your chunks with a different
# convention and want symmetry instead.
QUERY_INSTRUCTION_PREFIX = os.environ.get(
    "QUERY_INSTRUCTION_PREFIX",
    "Represent this sentence for searching relevant passages: ",
)
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.2")
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "30"))
ENABLE_QUERY_REWRITE = os.environ.get("ENABLE_QUERY_REWRITE", "true").strip().lower() in ("1", "true", "yes")
MAX_SUBQUERIES = int(os.environ.get("MAX_SUBQUERIES", "4"))

# --- Qdrant ---
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY") or None
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "nust_chunks")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "30"))

# --- Neighbor-chunk expansion ---
ENABLE_CHUNK_EXPANSION = os.environ.get("ENABLE_CHUNK_EXPANSION", "true").strip().lower() in ("1", "true", "yes")
EXPAND_FULL_PAGE_MAX_CHUNKS = int(os.environ.get("EXPAND_FULL_PAGE_MAX_CHUNKS", "10"))
EXPAND_WINDOW = int(os.environ.get("EXPAND_WINDOW", "2"))
EXPAND_MAX_CHUNKS_PER_SOURCE = int(os.environ.get("EXPAND_MAX_CHUNKS_PER_SOURCE", "20"))
EXPAND_MAX_TOTAL_CHUNKS = int(os.environ.get("EXPAND_MAX_TOTAL_CHUNKS", "30"))

# --- Cohere rerank ---
ENABLE_RERANK = os.environ.get("ENABLE_RERANK", "true").strip().lower() in ("1", "true", "yes")
COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "")
COHERE_RERANK_MODEL = os.environ.get("COHERE_RERANK_MODEL", "rerank-v4.0-fast")
RERANK_TOP_N = int(os.environ.get("RERANK_TOP_N", "10"))
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT", "10"))

# --- Groq (chat answer generation) ---
# Note: llama3-8b-8192 / llama3-70b-8192 were retired by Groq on 08/30/25,
# and their successors llama-3.1-8b-instant / llama-3.3-70b-versatile were
# in turn retired on 08/16/26 for free/developer-tier keys. gpt-oss-120b is
# Groq's current recommended replacement for the 70B-class model -- swap
# this if Groq's lineup changes again.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_API_URL = os.environ.get("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_TIMEOUT = float(os.environ.get("GROQ_TIMEOUT", "30"))
GROQ_MAX_TOKENS = int(os.environ.get("GROQ_MAX_TOKENS", "1024"))
GROQ_TEMPERATURE = float(os.environ.get("GROQ_TEMPERATURE", "0.3"))

# --- Redis (chat session history -- e.g. Upstash) ---
REDIS_URL = os.environ.get("REDIS_URL", "")  # e.g. rediss://default:<password>@<host>:<port>
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "3600"))  # sliding: refreshed on every turn
MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "20"))  # 10 user+assistant turns

# --- Chat orchestration ---
# How many recent messages get fed into the (cheap, local) query-resolution
# step so references like "what about that?" can be disambiguated --
# deliberately much smaller than MAX_HISTORY_MESSAGES, since only the last
# exchange or two is ever needed to resolve a reference.
REWRITE_CONTEXT_MESSAGES = int(os.environ.get("REWRITE_CONTEXT_MESSAGES", "4"))
# How many retrieved chunks get put in the Groq answer-generation prompt.
ANSWER_CONTEXT_CHUNKS = int(os.environ.get("ANSWER_CONTEXT_CHUNKS", "8"))