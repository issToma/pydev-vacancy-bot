from __future__ import annotations

import datetime
import html
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.vacancy.normalize import normalize


@dataclass(frozen=True)
class VacancyFilters:
    """Фильтры поиска вакансий (всё опционально, применяется поверх dev_vacancies)."""

    text: str | None = None          # поиск по названию (ILIKE)
    area: str | None = None          # город/регион (ILIKE)
    technologies: list[str] | None = None  # нужные технологии (пересечение массивов)
    work_format: str | None = None   # напр. 'Удалённо'
    accredited: bool | None = None   # только аккредитованные IT-компании
    country: str | None = None       # 'ru' | 'foreign' (по справочнику ru_areas)
    limit: int = 10


@dataclass(frozen=True)
class VacancyRow:
    id: int
    name: str
    area: str
    employer: str
    experience: str
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    published_at: datetime.datetime | None = None


_CURRENCY = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸", "BYN": "Br", "UAH": "₴"}


def _salary_line(v: VacancyRow) -> str | None:
    """Строка зарплаты или None, если не указана."""
    def num(n: int) -> str:
        return f"{n:,}".replace(",", " ")     # 250000 → 250 000

    if v.salary_min and v.salary_max:
        amount = f"{num(v.salary_min)}–{num(v.salary_max)}"
    elif v.salary_min:
        amount = f"от {num(v.salary_min)}"
    elif v.salary_max:
        amount = f"до {num(v.salary_max)}"
    else:
        return None
    cur = _CURRENCY.get(v.currency or "", v.currency or "")
    return f"{amount} {cur}".strip()


def format_vacancy_card(v: VacancyRow) -> str:
    """Карточка вакансии в Telegram-HTML.

    Название / Компания / Зарплата (если есть) — по строке;
    Город, Дата, кликабельная «Ссылка» — одной строкой через запятую.
    Отправлять с parse_mode=HTML.
    """
    lines = [html.escape(v.name), html.escape(v.employer)]
    salary = _salary_line(v)
    if salary:
        lines.append(html.escape(salary))
    meta = []
    if v.area:
        meta.append(html.escape(v.area))
    if v.published_at:
        meta.append(v.published_at.strftime("%d.%m.%Y"))
    meta.append(f'<a href="https://hh.ru/vacancy/{v.id}">Ссылка</a>')
    lines.append(", ".join(meta))
    return "\n".join(lines)


async def search_vacancies(session: AsyncSession, f: VacancyFilters) -> list[VacancyRow]:
    """Поиск вакансий в dev_vacancies с динамическими фильтрами.

    Фильтры собираются как набор AND-условий; параметры биндятся (защита от
    SQL-инъекций). Технологии — пересечение массивов (&&) по description_key_words
    и key_skills.
    """
    conditions: list[str] = []
    params: dict = {}

    if f.text:
        conditions.append("name ILIKE :text")
        params["text"] = f"%{f.text}%"
    if f.area:
        conditions.append("area ILIKE :area")
        params["area"] = f"%{f.area}%"
    if f.work_format:
        conditions.append(":work_format = ANY(work_format)")
        params["work_format"] = f.work_format
    if f.accredited:
        conditions.append("COALESCE(accredited_it_employer, false)")
    if f.country == "ru":
        conditions.append("area IN (SELECT name FROM ru_areas)")
    elif f.country == "foreign":
        conditions.append("area NOT IN (SELECT name FROM ru_areas)")
    if f.technologies:
        # вакансия подходит, если её ключевые слова/навыки пересекаются с запрошенными.
        # CAST(... AS text[]) вместо ::text[] — в text() двоеточие = бинд-параметр.
        conditions.append(
            "(COALESCE(description_key_words, CAST('{}' AS text[])) "
            "|| COALESCE(key_skills, CAST('{}' AS text[]))) && CAST(:techs AS text[])"
        )
        params["techs"] = f.technologies

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    params["limit"] = f.limit

    sql = text(
        f"SELECT id, name, area, employer, experience, "
        f"salary_min, salary_max, currency, published_at "
        f"FROM dev_vacancies{where} "
        f"ORDER BY published_at DESC NULLS LAST "
        f"LIMIT :limit"
    )
    rows = (await session.execute(sql, params)).all()
    return [VacancyRow(*r) for r in rows]


@dataclass(frozen=True)
class TechStat:
    technology: str
    vacancy_count: int
    pct: float


async def top_technologies(session: AsyncSession, min_pct: float = 0.5) -> list[TechStat]:
    """Топ технологий по dev-вакансиям.

    Для каждой вакансии берём объединение description_key_words + key_skills,
    нормализуем и дедуплицируем (технология считается раз на вакансию — это
    «доля вакансий, где она встречается», а не суммарные упоминания).
    pct = доля dev-вакансий; отсекаем шум ниже min_pct.
    """
    rows = (
        await session.execute(
            text("SELECT description_key_words, key_skills FROM dev_vacancies")
        )
    ).all()
    total = len(rows)
    if total == 0:
        return []

    counter: Counter[str] = Counter()
    for description_key_words, key_skills in rows:
        techs = set()
        for raw in (description_key_words or []) + (key_skills or []):
            canonical = normalize(raw)
            if canonical:
                techs.add(canonical)
        counter.update(techs)

    stats = [
        TechStat(tech, count, round(100 * count / total, 1))
        for tech, count in counter.most_common()
    ]
    return [s for s in stats if s.pct >= min_pct]
