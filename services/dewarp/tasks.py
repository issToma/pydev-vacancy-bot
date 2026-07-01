from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import s3
from core.celery_app import celery_app
from core.config import settings
from core.models import Job
from services.dewarp.dewarp import dewarp_bytes
from services.vacancy.notifications import publish_notifications

logger = logging.getLogger(__name__)


async def _set_status(session_factory, job_id: uuid.UUID, status: str, result_ref: str | None = None) -> None:
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is not None:
            job.status = status
            if result_ref is not None:
                job.result_ref = result_ref
            await session.commit()


async def _run_dewarp(job_id: str, user_id: int, input_key: str) -> str:
    jid = uuid.UUID(job_id)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _set_status(session_factory, jid, "running")

        data = await s3.get_object(settings.s3_bucket_input, input_key)
        result = dewarp_bytes(data)        # синхронный CPU-этап (OpenCV+YOLO)

        if result is None:
            await _set_status(session_factory, jid, "failed")
            await publish_notifications([{
                "user_id": user_id,
                "text": "Не удалось найти документ на фото. Попробуй снимок поконтрастнее.",
            }])
            return "failed"

        out_key = f"{job_id}.jpg"
        await s3.put_object(settings.s3_bucket_output, out_key, result, "image/jpeg")
        await _set_status(session_factory, jid, "done", result_ref=out_key)
        await publish_notifications([{
            "user_id": user_id,
            "photo_bucket": settings.s3_bucket_output,
            "photo_key": out_key,
            "caption": "Готово — документ выпрямлен",
        }])
        return "done"
    finally:
        await engine.dispose()


@celery_app.task(name="services.dewarp.tasks.dewarp_task")
def dewarp_task(job_id: str, user_id: int, input_key: str) -> str:
    """Celery-задача деварпинга: S3-вход → OpenCV/YOLO → S3-выход → уведомление пользователю."""
    result = asyncio.run(_run_dewarp(job_id, user_id, input_key))
    logger.info("dewarp_task %s: %s", job_id, result)
    return result
