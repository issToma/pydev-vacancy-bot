from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Subscription, User


async def upsert_user(session: AsyncSession, user_id: int, username: str | None) -> None:
    """Добавить/обновить пользователя (idempotent по telegram id)."""
    stmt = pg_insert(User).values(id=user_id, username=username)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"], set_={"username": stmt.excluded.username}
    )
    await session.execute(stmt)


async def create_subscription(
    session: AsyncSession, user_id: int, filter_: dict
) -> Subscription:
    """Создать подписку пользователя на фильтр вакансий."""
    sub = Subscription(user_id=user_id, filter=filter_)
    session.add(sub)
    await session.flush()
    return sub


async def list_user_subscriptions(
    session: AsyncSession, user_id: int
) -> list[Subscription]:
    """Активные подписки конкретного пользователя."""
    rows = await session.scalars(
        select(Subscription).where(
            Subscription.user_id == user_id, Subscription.is_active.is_(True)
        )
    )
    return list(rows)


async def deactivate_subscription(session: AsyncSession, sub_id: int, user_id: int) -> bool:
    """Мягко удалить подписку (is_active=false). Проверяем, что она принадлежит юзеру."""
    sub = await session.get(Subscription, sub_id)
    if sub is None or sub.user_id != user_id:
        return False
    sub.is_active = False
    await session.commit()
    return True


async def list_active_subscriptions(session: AsyncSession) -> list[Subscription]:
    """Все активные подписки (для матчинга новых вакансий)."""
    rows = await session.scalars(
        select(Subscription).where(Subscription.is_active.is_(True))
    )
    return list(rows)


def subscription_matches(filter_: dict, vacancy: dict) -> bool:
    """Матчинг: подходит ли вакансия под фильтр подписки.

    По словам (AND): каждое слово из фильтра должно встречаться в названии.
    Так «middle-python» → слова [middle, python] → ловит «Middle Python Developer»,
    а «python» ловит «Python Developer». Расширяемо: area/technologies и т.д.
    """
    text = (filter_.get("text") or "").strip().lower()
    if not text:
        return True
    name = (vacancy.get("name") or "").lower()
    tokens = [t for t in re.split(r"[^\w]+", text) if t]
    return all(t in name for t in tokens)