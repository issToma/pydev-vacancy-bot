from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.contracts import Infra
from core.db import get_session
from services.vacancy.repo import VacancyFilters, VacancyRow, search_vacancies


def _format_vacancies(rows: list[VacancyRow]) -> str:
    if not rows:
        return "Ничего не найдено."
    lines = [
        f"• {v.name} — {v.employer}\n  {v.area} | {v.experience}\n  {v.url}"
        for v in rows
    ]
    return "\n\n".join(lines)


class VacancyService:
    """Vacancy Radar: поиск вакансий (REST + команда бота)."""

    name = "vacancy"
    commands = ["/vacancies"]
    help_text = (
        "/vacancies [запрос] — поиск вакансий по разработке.\n"
        "  Запрос (необязательно) ищется в названии вакансии; "
        "без него — последние вакансии.\n"
        "  Пример: /vacancies senior python"
    )

    def register(self, router: Router, infra: Infra) -> None:
        @router.message(Command("vacancies"))
        async def vacancies(message: Message) -> None:
            # текст после команды → поисковый запрос по названию
            query = message.text.partition(" ")[2].strip() or None
            async with infra.session_factory() as session:
                rows = await search_vacancies(
                    session, VacancyFilters(text=query, limit=10)
                )
            await message.answer(_format_vacancies(rows), disable_web_page_preview=True)

    def api_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/vacancies")
        async def list_vacancies(
            text: str | None = None,
            area: str | None = None,
            work_format: str | None = None,
            accredited: bool | None = None,
            country: str | None = None,
            limit: int = 10,
            session: AsyncSession = Depends(get_session),
        ) -> list[dict]:
            rows = await search_vacancies(
                session,
                VacancyFilters(
                    text=text,
                    area=area,
                    work_format=work_format,
                    accredited=accredited,
                    country=country,
                    limit=limit,
                ),
            )
            return [vars(r) for r in rows]

        return router