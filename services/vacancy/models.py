from __future__ import annotations

import datetime

from sqlalchemy import ARRAY, DateTime, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class VacancyScraped(Base):
    """Вакансия, собранная скрейпером (таблица vacancies_scraped).

    id — идентификатор вакансии с hh (не автоинкремент).
    vacancy_status — состояние вакансии на hh: available (пишет скрапер) / archived
    (ставит тулза откликов, встретив архив). apply_status — статус НАШЕГО отклика:
    pending / applied / manual / error (им владеет тулза, см. APP_OWNED в tasks.py).
    """

    __tablename__ = "vacancies_scraped"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(256))
    area: Mapped[str] = mapped_column(String(256))
    salary_min: Mapped[int | None]
    salary_max: Mapped[int | None]
    currency: Mapped[str | None] = mapped_column(String(8))
    address: Mapped[str | None] = mapped_column(String(256))
    published_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    url: Mapped[str] = mapped_column(String(256))
    apply_alternate_url: Mapped[str] = mapped_column(String(256))
    employer: Mapped[str] = mapped_column(String(256))
    accredited_it_employer: Mapped[bool | None]
    work_format: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    professional_role: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    experience: Mapped[str] = mapped_column(String(256))
    employment_format: Mapped[str | None] = mapped_column(String(256))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    description: Mapped[str | None] = mapped_column(Text)
    description_key_words: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    has_test: Mapped[bool | None]
    key_skills: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    languages: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    vacancy_status: Mapped[str] = mapped_column(String(16), server_default=text("'available'"))
    archived: Mapped[bool | None]
    apply_status: Mapped[str | None] = mapped_column(String(20), server_default=text("'pending'"))


class RuArea(Base):
    """Справочник названий регионов/городов РФ (поддерево России из hh /areas).

    Нужен для фильтра РФ/зарубеж: в vacancies_scraped есть только город (area),
    страны нет — принадлежность к РФ определяем по вхождению area в этот справочник.
    """

    __tablename__ = "ru_areas"

    name: Mapped[str] = mapped_column(Text, primary_key=True)