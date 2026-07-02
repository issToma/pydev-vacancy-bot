from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.llm import llm_client
from services.vacancy.scraper.keywords import extract_technologies

logger = logging.getLogger(__name__)

MATCH_SYSTEM_PROMPT = (
    "Ты — ассистент по подбору вакансий. На вход: резюме кандидата и список вакансий. "
    "Выбери топ-5 по УБЫВАНИЮ соответствия резюме (1 — подходит лучше всего). "
    "Пиши максимально тезисно, без вступлений и воды. Для каждой вакансии строго формат:\n"
    "<n>. <Название> — <компания>\n"
    "https://hh.ru/vacancy/<id>\n"
    "➕ <чем подходит: одна короткая строка>\n"
    "➖ <чего не хватает: одна короткая строка>\n"
    "Не придумывай вакансии вне списка. Числовой id пиши только внутри ссылки, не в тексте."
)

# Числовой id в квадратных скобках (напр. «[134182839]») — служебный, пользователю не нужен.
_BRACKET_ID = re.compile(r"\s*\[\d+\]")


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
    """Компактное представление кандидатов для промпта (без полных описаний — экономим токены).

    Без порядковых номеров — иначе модель echo-ит их в ответ. id нужен для ссылки.
    """
    lines = []
    for c in cands:
        skills = (c.get("key_skills") or []) or (c.get("techs") or [])
        skills_str = ", ".join(skills[:12]) or "—"
        lines.append(
            f"[{c['id']}] {c['name']} — {c['employer']} | {c['area']} | "
            f"опыт: {c['experience']} | навыки: {skills_str}"
        )
    return "\n".join(lines)


async def rank_with_llm(resume: str, cands: list[dict]) -> tuple[str, object]:
    """LLM-ранжирование: (текст ответа, usage). Не-стрим — задача фоновая (Celery)."""
    user_prompt = (
        f"РЕЗЮМЕ КАНДИДАТА:\n{resume}\n\n"
        f"ВАКАНСИИ-КАНДИДАТЫ:\n{_candidates_block(cands)}\n\n"
        "Выбери топ-5 по соответствию резюме и оформи строго в заданном тезисном формате."
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
    # подчищаем [id] из текста (id остаётся в ссылке hh.ru/vacancy/<id>)
    answer = _BRACKET_ID.sub("", resp.choices[0].message.content or "")
    return answer, resp.usage


def extract_stack(resume: str) -> list[str]:
    """Извлечь стек из текста резюме (переиспользуем нормализацию скрапера)."""
    return extract_technologies(resume)