"""
Session memory backed by Redis (Cloud Memorystore on GCP).
Falls back to an in-memory dict when Redis is not available (local dev).
"""
from __future__ import annotations
import json
import structlog
import redis.asyncio as aioredis
from backend.config import get_settings

log = structlog.get_logger()
settings = get_settings()


class SessionMemory:
    """Manages per-session state in Redis, with in-memory fallback."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._redis_ok: bool | None = None  # None = untested
        self._store: dict[str, str] = {}    # in-memory fallback

    async def _get_client(self) -> aioredis.Redis | None:
        if self._redis_ok is False:
            return None
        if self._client is None:
            self._client = aioredis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                decode_responses=True,
            )
        if self._redis_ok is None:
            try:
                await self._client.ping()
                self._redis_ok = True
                log.info("session.redis", status="connected")
            except Exception:
                self._redis_ok = False
                self._client = None
                log.warning("session.redis", status="unavailable, using in-memory fallback")
        return self._client if self._redis_ok else None

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get(self, key: str) -> str | None:
        client = await self._get_client()
        if client:
            return await client.get(key)
        return self._store.get(key)

    async def _setex(self, key: str, ttl: int, value: str) -> None:
        client = await self._get_client()
        if client:
            await client.setex(key, ttl, value)
        else:
            self._store[key] = value  # no TTL in fallback — acceptable for dev

    async def _delete(self, key: str) -> None:
        client = await self._get_client()
        if client:
            await client.delete(key)
        else:
            self._store.pop(key, None)

    # ── Conversation history ──────────────────────────────────────────────────

    async def get_history(self, session_id: str) -> list[dict]:
        raw = await self._get(f"session:{session_id}:history")
        if raw is None:
            return []
        return json.loads(raw)

    async def append_history(
        self, session_id: str, role: str, content: str | list
    ) -> None:
        history = await self.get_history(session_id)
        history.append({"role": role, "content": content})
        history = history[-20:]
        await self._setex(
            f"session:{session_id}:history",
            settings.redis_ttl_seconds,
            json.dumps(history),
        )

    async def clear_history(self, session_id: str) -> None:
        await self._delete(f"session:{session_id}:history")

    # ── Dataset context ───────────────────────────────────────────────────────

    async def set_dataset_context(self, session_id: str, context: dict) -> None:
        await self._setex(
            f"session:{session_id}:dataset",
            settings.redis_ttl_seconds,
            json.dumps(context),
        )

    async def get_dataset_context(self, session_id: str) -> dict | None:
        raw = await self._get(f"session:{session_id}:dataset")
        if raw is None:
            return None
        return json.loads(raw)

    # ── DuckDB query cache ────────────────────────────────────────────────────

    async def cache_query_result(
        self, cache_key: str, result: list[dict], ttl: int = 300
    ) -> None:
        await self._setex(f"qcache:{cache_key}", ttl, json.dumps(result))

    async def get_cached_query(self, cache_key: str) -> list[dict] | None:
        raw = await self._get(f"qcache:{cache_key}")
        if raw is None:
            return None
        return json.loads(raw)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()


# Singleton
session_memory = SessionMemory()
