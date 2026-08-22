"""
state.py
========
Process-wide shared clients (httpx, Qdrant, Cohere), created once in
main.py's lifespan and read from retrieval.py / groq_client.py.

Kept in its own tiny module -- rather than living directly in main.py --
so those modules can import `clients` without importing main.py itself,
since main.py needs to import routers/functions FROM them (importing back
would be a circular import).
"""
from typing import Any

clients: dict[str, Any] = {}