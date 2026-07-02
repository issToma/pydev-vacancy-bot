from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import LlmUsage


def _since_24h() -> datetime.datetime:
    # llm_usage.created_at — наивный UTC (server now()); сравниваем с наивным UTC
    now_utc = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    return now_utc - datetime.timedelta(days=1)


async def tokens_last_24h(session: AsyncSession, user_id: int) -> int:
    """Сумма токенов (in+out) пользователя за последние сутки."""
    total = (await session.execute(
        select(func.coalesce(func.sum(LlmUsage.tokens_in + LlmUsage.tokens_out), 0))
        .where(LlmUsage.user_id == user_id, LlmUsage.created_at >= _since_24h())
    )).scalar_one()
    return int(total)


async def over_budget(session: AsyncSession, user_id: int, budget: int) -> bool:
    """Исчерпан ли суточный бюджет токенов (спенд-лимит). budget=0 — без лимита."""
    if not budget:
        return False
    return await tokens_last_24h(session, user_id) >= budget


async def usage_summary(session: AsyncSession, user_id: int) -> list[tuple[str, int, int, int]]:
    """Расход за сутки по сервисам: [(service, запросов, tokens_in, tokens_out), ...]."""
    rows = (await session.execute(
        select(
            LlmUsage.service,
            func.count(),
            func.coalesce(func.sum(LlmUsage.tokens_in), 0),
            func.coalesce(func.sum(LlmUsage.tokens_out), 0),
        )
        .where(LlmUsage.user_id == user_id, LlmUsage.created_at >= _since_24h())
        .group_by(LlmUsage.service)
        .order_by(LlmUsage.service)
    )).all()
    return [(s, int(n), int(ti), int(to)) for s, n, ti, to in rows]
