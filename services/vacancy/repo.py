from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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
    name: str
    area: str
    employer: str
    experience: str
    url: str


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
        f"SELECT name, area, employer, experience, url "
        f"FROM dev_vacancies{where} "
        f"ORDER BY published_at DESC NULLS LAST "
        f"LIMIT :limit"
    )
    rows = (await session.execute(sql, params)).all()
    return [VacancyRow(*r) for r in rows]
