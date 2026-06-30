from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB
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