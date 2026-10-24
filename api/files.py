"""Раздача скачанных файлов по ссылке с токеном.

ОТДЕЛЬНОЕ приложение, а не ручка в api.main — и это осознанно: основной API
держит `POST /subscriptions` без авторизации и Swagger, его наружу выставлять
нельзя. Здесь наружу смотрит ровно один эндпоинт, умеющий одно действие.

Запуск (см. run.sh):
    uv run uvicorn api.files:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from core.config import settings
from core.db import async_session_factory
from core.models import Job
from core.redis import redis_client
from services.download.formats import download_dir

logger = logging.getLogger(__name__)

# Content-Type по расширению: нужен, чтобы браузер открыл видео плеером,
# а не предложил сохранить файл. mkv браузеры не играют — он просто скачается.
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
}

# Swagger и схему отключаем: документировать нечего, а лишняя поверхность не нужна.
app = FastAPI(title="vacancy-bot files", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    """Проверка живости (для мониторинга и быстрой проверки, что раздача поднялась)."""
    return {"status": "ok"}


async def _job_by_token(token: str) -> Job | None:
    """Найти завершённую загрузку по токену ссылки."""
    async with async_session_factory() as session:
        return (await session.execute(
            select(Job)
            .where(
                Job.service == "download",
                Job.status == "done",
                Job.params["token"].astext == token,
            )
            .limit(1)
        )).scalar_one_or_none()


def _expired(job: Job) -> bool:
    """Истёк ли срок ссылки. Нет метки или она битая — считаем истёкшей."""
    raw = (job.params or {}).get("expires_at")
    if not raw:
        return True
    try:
        return datetime.now(UTC) > datetime.fromisoformat(raw)
    except ValueError:
        return True


async def _over_limit(token: str) -> bool:
    """Счётчик обращений в Redis, а не в БД: при перемотке плеер делает десятки
    Range-запросов, и писать на каждый в Postgres было бы расточительно."""
    key = f"dl:hits:{token}"
    hits = await redis_client.incr(key)
    if hits == 1:
        await redis_client.expire(key, settings.download_link_ttl_hours * 3600)
    return hits > settings.download_link_max_hits


@app.get("/d/{token}")
async def download(token: str) -> FileResponse:
    """Отдать файл по токену.

    Любая неудача — 404 без подробностей: отвечать по-разному на «нет такого
    токена» и «токен истёк» значит подтверждать, что токен существовал.
    """
    job = await _job_by_token(token)
    if job is None or _expired(job) or not job.result_ref:
        raise HTTPException(status_code=404, detail="Ссылка недействительна")

    # путь берём ИЗ БАЗЫ, из запроса — только токен. Проверка вложенности в папку
    # загрузок — страховка на случай, если в result_ref однажды попадёт мусор:
    # без неё ошибка в записи превратилась бы в чтение произвольного файла.
    path = Path(job.result_ref).resolve()
    if not path.is_relative_to(download_dir()) or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл недоступен")

    if await _over_limit(token):
        logger.warning("Превышен лимит обращений по ссылке: job=%s", job.id)
        raise HTTPException(status_code=429, detail="Слишком много обращений по ссылке")

    title = (job.params or {}).get("title") or path.stem
    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        filename=f"{title[:150]}{path.suffix}",
        # inline — чтобы в браузере телефона открылся плеер с перемоткой,
        # а не началось скачивание. Range-запросы FileResponse обслуживает сам.
        content_disposition_type="inline",
    )
