from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class User(Base):
    """Пользователь Telegram. id = telegram user_id (не автоинкремент)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None]
    created_at: Mapped[datetime.datetime] = mapped_column(
        server_default=text("now()")
    )


class Subscription(Base):
    """Подписка пользователя на фильтр вакансий.

    filter — jsonb с теми же ключами, что и фильтры поиска
    (text/area/technologies/work_format/accredited/country).
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    filter: Mapped[dict] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        server_default=text("now()")
    )


class Job(Base):
    """История фоновых задач (scrape/dewarp/match) и их статусов."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    service: Mapped[str] = mapped_column(Text)          # "vacancy" | "dewarp" | ...
    kind: Mapped[str] = mapped_column(Text)             # "dewarp" | "match" | ...
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))  # queued/running/done/failed
    params: Mapped[dict | None] = mapped_column(JSONB)
    result_ref: Mapped[str | None] = mapped_column(Text)  # S3-ключ результата
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        server_default=text("now()"), onupdate=datetime.datetime.utcnow
    )