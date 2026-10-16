"""Unit tests for SessionMemory — no live Redis required."""
import json

import pytest

from backend.memory.session import MAX_HISTORY, SessionMemory


class _Pipeline:
    """Mimics the list semantics of RPUSH/LTRIM/EXPIRE inside one MULTI."""

    def __init__(self, store: "FakeRedis") -> None:
        self._store = store
        self._ops: list[tuple] = []

    def rpush(self, key: str, value: str) -> None:
        self._ops.append(("rpush", key, value))

    def ltrim(self, key: str, start: int, end: int) -> None:
        self._ops.append(("ltrim", key, start, end))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key, ttl))

    async def execute(self) -> None:
        for op in self._ops:
            if op[0] == "rpush":
                self._store.data.setdefault(op[1], []).append(op[2])
            elif op[0] == "ltrim":
                self._store.data[op[1]] = self._store.data[op[1]][op[2]:]
            elif op[0] == "expire":
                self._store.ttl[op[1]] = op[2]
        self._store.pipelines.append([op[0] for op in self._ops])
        self._ops = []


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, list[str]] = {}
        self.ttl: dict[str, int] = {}
        self.pipelines: list[list[str]] = []

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return list(self.data.get(key, []))

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


@pytest.fixture
def redis_memory() -> tuple[SessionMemory, FakeRedis]:
    memory = SessionMemory()
    fake = FakeRedis()
    memory._client = fake
    memory._redis_ok = True
    return memory, fake


@pytest.fixture
def fallback_memory() -> SessionMemory:
    memory = SessionMemory()
    memory._redis_ok = False
    return memory


@pytest.mark.asyncio
async def test_history_trims_to_max_keeping_newest(redis_memory):
    memory, _ = redis_memory
    for i in range(MAX_HISTORY + 5):
        await memory.append_history("s", "user", f"q{i}")

    history = await memory.get_history("s")

    assert len(history) == MAX_HISTORY
    assert history[0]["content"] == "q5"
    assert history[-1]["content"] == f"q{MAX_HISTORY + 4}"


@pytest.mark.asyncio
async def test_append_is_a_single_atomic_pipeline(redis_memory):
    """Guards the read-modify-write race: no separate GET then SET."""
    memory, fake = redis_memory
    await memory.append_history("s", "user", "q")

    assert fake.pipelines == [["rpush", "ltrim", "expire"]]


@pytest.mark.asyncio
async def test_ttl_is_refreshed_on_every_append(redis_memory):
    memory, fake = redis_memory
    await memory.append_history("s", "user", "q")

    assert fake.ttl["session:s:messages"] == 7200


@pytest.mark.asyncio
async def test_uses_messages_key_not_legacy_history_key(redis_memory):
    """A LIST under the old :history string key would raise WRONGTYPE."""
    memory, fake = redis_memory
    await memory.append_history("s", "user", "q")

    assert list(fake.data) == ["session:s:messages"]


@pytest.mark.asyncio
async def test_structured_content_round_trips(redis_memory):
    memory, _ = redis_memory
    blocks = [{"type": "text", "text": "hi"}]
    await memory.append_history("s", "assistant", blocks)

    assert (await memory.get_history("s"))[0]["content"] == blocks


@pytest.mark.asyncio
async def test_sessions_are_isolated(redis_memory):
    memory, _ = redis_memory
    await memory.append_history("a", "user", "qa")

    assert await memory.get_history("b") == []


@pytest.mark.asyncio
async def test_clear_history_empties_the_session(redis_memory):
    memory, _ = redis_memory
    await memory.append_history("s", "user", "q")
    await memory.clear_history("s")

    assert await memory.get_history("s") == []


@pytest.mark.asyncio
async def test_json_payload_shape(redis_memory):
    memory, fake = redis_memory
    await memory.append_history("s", "user", "q")

    assert json.loads(fake.data["session:s:messages"][0]) == {
        "role": "user",
        "content": "q",
    }


@pytest.mark.asyncio
async def test_fallback_trims_and_orders_like_redis(fallback_memory):
    for i in range(MAX_HISTORY + 5):
        await fallback_memory.append_history("s", "user", f"q{i}")

    history = await fallback_memory.get_history("s")

    assert len(history) == MAX_HISTORY
    assert history[0]["content"] == "q5"


@pytest.mark.asyncio
async def test_fallback_get_history_returns_a_copy(fallback_memory):
    await fallback_memory.append_history("s", "user", "q")

    (await fallback_memory.get_history("s")).append({"role": "user", "content": "x"})

    assert len(await fallback_memory.get_history("s")) == 1


@pytest.mark.asyncio
async def test_fallback_clear_history(fallback_memory):
    await fallback_memory.append_history("s", "user", "q")
    await fallback_memory.clear_history("s")

    assert await fallback_memory.get_history("s") == []
