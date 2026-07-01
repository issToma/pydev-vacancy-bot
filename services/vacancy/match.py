from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.llm import llm_client
from services.vacancy.scraper.keywords import extract_technologies

logger = logging.getLogger(__name__)

MATCH_SYSTEM_PROMPT = (
    "Ты — карьерный ассистент по подбору вакансий. На вход даётся резюме кандидата "
    "и пронумерованный список вакансий-кандидатов. Выбери топ-5 наиболее подходящих. "
    "Для каждой кратко объясни: почему подходит и чего кандидату не хватает. "
    "Ссылайся на вакансии по номеру из списка. НЕ придумывай вакансии вне списка. "
    "Отвечай по делу, без воды."
)


async def extract_area(session: AsyncSession, resume: str) -> str | None:
    """Найти в тексте упоминание города/региона, сверяя с реальными area из dev_vacancies.

    Берём только существующие в данных значения (иначе фильтр ничего не найдёт).
    При нескольких совпадениях — самое длинное (точнее, напр. «Санкт-Петербург»).
    """
    low = resume.lower()
    rows = (await session.execute(
        text("SELECT DISTINCT area FROM dev_vacancies WHERE area IS NOT NULL")
    )).scalars().all()
    matches = [a for a in rows if a and a.lower() in low]
    return max(matches, key=len) if matches else None


async def prefilter_candidates(
    session: AsyncSession, technologies: list[str], limit: int, area: str | None = None
) -> list[dict]:
    """Дешёвый SQL-предфильтр кандидатов из dev_vacancies.

    Условия (AND): пересечение стека (&&) и/или город (ILIKE). Если ничего не
    задано — просто свежие. В LLM уходят ТОЛЬКО эти кандидаты (не вся база) —
    так контролируем контекст и стоимость.
    """
    conds: list[str] = []
    params: dict = {"limit": limit}
    if technologies:
        conds.append(
            "(COALESCE(description_key_words, CAST('{}' AS text[])) "
            "|| COALESCE(key_skills, CAST('{}' AS text[]))) && CAST(:techs AS text[])"
        )
        params["techs"] = technologies
    if area:
        conds.append("area ILIKE :area")
        params["area"] = f"%{area}%"
    where = ("WHERE " + " AND ".join(conds) + " ") if conds else ""
    sql = text(
        "SELECT id, name, employer, area, experience, "
        "COALESCE(key_skills, CAST('{}' AS text[])) AS key_skills, "
        "COALESCE(description_key_words, CAST('{}' AS text[])) AS techs "
        "FROM dev_vacancies "
        f"{where}"
        "ORDER BY published_at DESC NULLS LAST LIMIT :limit"
    )
    rows = (await session.execute(sql, params)).mappings().all()
    return [dict(r) for r in rows]


def _candidates_block(cands: list[dict]) -> str:
    """Компактное представление кандидатов для промпта (без полных описаний — экономим токены)."""
    lines = []
    for i, c in enumerate(cands, 1):
        skills = (c.get("key_skills") or []) or (c.get("techs") or [])
        skills_str = ", ".join(skills[:12]) or "—"
        lines.append(
            f"{i}. [{c['id']}] {c['name']} — {c['employer']} | {c['area']} | "
            f"опыт: {c['experience']} | навыки: {skills_str}"
        )
    return "\n".join(lines)


async def rank_with_llm(resume: str, cands: list[dict]) -> tuple[str, object]:
    """LLM-ранжирование: (текст ответа, usage). Не-стрим — задача фоновая (Celery)."""
    user_prompt = (
        f"РЕЗЮМЕ КАНДИДАТА:\n{resume}\n\n"
        f"ВАКАНСИИ-КАНДИДАТЫ:\n{_candidates_block(cands)}\n\n"
        "Выбери топ-5 подходящих и для каждой поясни: почему подходит и чего не хватает. "
        "Укажи ссылку вида https://hh.ru/vacancy/<id>."
    )
    kwargs: dict = {
        "model": settings.llm_model,
        "max_tokens": settings.match_max_tokens,
        "messages": [
            {"role": "system", "content": MATCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    if settings.ask_reasoning_effort:
        kwargs["reasoning_effort"] = settings.ask_reasoning_effort
    resp = await llm_client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or "", resp.usage


def extract_stack(resume: str) -> list[str]:
    """Извлечь стек из текста резюме (переиспользуем нормализацию скрапера)."""
    return extract_technologies(resume)