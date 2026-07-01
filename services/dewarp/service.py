from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import BotCommand, Message

from core.config import settings
from core.contracts import Infra
from core.models import Job


class DewarpService:
    """Dewarp: пользователь присылает фото документа → выпрямленное изображение."""

    name = "dewarp"
    commands = [BotCommand(command="dewarp", description="выпрямить фото документа (пришли фото)")]
    help_text = "/dewarp — пришли фото документа, верну выпрямленную версию"

    def register(self, router: Router, infra: Infra) -> None:
        @router.message(F.photo)
        async def on_photo(message: Message) -> None:
            # самое крупное разрешение фото
            photo = message.photo[-1]
            buf = await message.bot.download(photo.file_id)
            data = buf.read()

            job_id = uuid.uuid4()
            input_key = f"{job_id}.jpg"
            await infra.s3.put_object(settings.s3_bucket_input, input_key, data, "image/jpeg")

            async with infra.session_factory() as session:
                session.add(Job(
                    id=job_id, user_id=message.from_user.id,
                    service="dewarp", kind="dewarp", status="queued",
                    params={"input_key": input_key},
                ))
                await session.commit()

            # ставим задачу в Celery (по имени — без импорта worker-кода в бот)
            infra.celery.send_task(
                "services.dewarp.tasks.dewarp_task",
                args=[str(job_id), message.from_user.id, input_key],
            )
            await message.answer("Фото получил, обрабатываю — пришлю результат.")

    def api_router(self) -> None:
        return None