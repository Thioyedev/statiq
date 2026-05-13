"""
Langfuse observability for StatIQ.

Two integration points:
1. LangChain CallbackHandler  → injected into LangGraph config, traces all LLM
   calls, nodes and chains automatically (tokens, cost, latency).
2. Score logger               → called from the LLM-as-judge to attach eval
   scores to the same trace.

Degrades gracefully when LANGFUSE_PUBLIC_KEY is not set — no tracing,
no errors, app continues to work normally.

Langfuse v4 API notes:
- CallbackHandler only accepts public_key and trace_context (no session_id/metadata)
- Use trace_context={"trace_id": ...} to set a deterministic trace ID per session
- Scores are logged via client.create_score() (renamed from client.score() in v3)
"""
from __future__ import annotations

import structlog
from functools import lru_cache

from backend.config import get_settings

log = structlog.get_logger()


# ── Lazy Langfuse client (singleton) ─────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_langfuse():
    """Return a Langfuse client or None if not configured."""
    settings = get_settings()
    if not settings.langfuse_public_key:
        return None
    try:
        from langfuse import Langfuse
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        log.info("langfuse.connected", host=settings.langfuse_host)
        return client
    except Exception as exc:
        log.warning("langfuse.init_failed", error=str(exc))
        return None


# ── Per-request callback handler ─────────────────────────────────────────────

def create_callback(session_id: str, question: str, dataset_ref: str = "") -> list:
    """
    Return a list containing a Langfuse CallbackHandler for this request.
    Returns an empty list when Langfuse is not configured.

    Usage in LangGraph config:
        config = {
            "configurable": {"thread_id": session_id},
            "callbacks": create_callback(session_id, question),
        }
    """
    settings = get_settings()
    if not settings.langfuse_public_key:
        return []
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        # Create a deterministic trace_id from session_id so all turns of the
        # same session are grouped under one trace in Langfuse.
        trace_id = Langfuse.create_trace_id(seed=session_id)
        handler = CallbackHandler(trace_context={"trace_id": trace_id})
        return [handler]
    except Exception as exc:
        log.warning("langfuse.callback_failed", error=str(exc))
        return []


# ── Score logger (for LLM-as-judge) ──────────────────────────────────────────

def log_eval_scores(trace_id: str, scores: dict[str, int | float | bool]) -> None:
    """
    Attach LLM-as-judge scores to an existing Langfuse trace.

    Args:
        trace_id:  Langfuse trace ID (from create_callback seed or "eval-<id>")
        scores:    e.g. {"grounding": 3, "precision": 2, "hallucination": False}
    """
    client = _get_langfuse()
    if client is None:
        return
    try:
        for name, value in scores.items():
            client.create_score(
                trace_id=trace_id,
                name=name,
                value=float(value),
            )
        log.info("langfuse.scores_logged", trace_id=trace_id, scores=scores)
    except Exception as exc:
        log.warning("langfuse.score_failed", error=str(exc))


# ── Flush helper ──────────────────────────────────────────────────────────────

def flush() -> None:
    """Force-flush pending events — call at app shutdown or after tests."""
    client = _get_langfuse()
    if client:
        client.flush()
