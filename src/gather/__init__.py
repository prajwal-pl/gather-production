"""gather — meeting-to-task follow-up agent.

Layout mirrors the architecture:

    connectors/   dumb provider I/O (Fireflies, Gmail, ...)
    domain.py     canonical objects, shared by everything downstream
    agents/       the only place an LLM is used
    graphs/       LangGraph workflows that orchestrate a single run
    workers/      timers: nudges, renewal, repair
    db.py         the system of record (SQLite here, Postgres in prod)
"""

__version__ = "0.1.0"
