from core.ratelimit import check_rate_limit


class FakeRedis:
    """Мини-фейк Redis для проверки логики INCR+EXPIRE без живого сервера."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, window: int) -> None:
        self.expires[key] = window


async def test_allows_within_limit_then_blocks():
    r = FakeRedis()
    results = [await check_rate_limit(r, "k", 3, 60) for _ in range(4)]
    assert results == [True, True, True, False]


async def test_expire_set_only_on_first_hit():
    r = FakeRedis()
    await check_rate_limit(r, "k", 3, 60)
    await check_rate_limit(r, "k", 3, 60)
    assert r.expires == {"k": 60}     # TTL выставлен один раз (при count==1)
