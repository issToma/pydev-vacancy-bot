from __future__ import annotations

from celery import Celery

from core.config import settings

# Экземпляр Celery — центральный объект приложения задач.
# - первый аргумент "vacancy_bot" — это main name: префикс имён задач
#   (задача add станет "vacancy_bot.add") и точка отсчёта при автоиспользовании.
# - broker  → RabbitMQ: куда КЛАСТЬ задачи (транспорт сообщений).
# - backend → Redis:    где ХРАНИТЬ статусы и результаты выполнения.
celery_app = Celery(
    "vacancy_bot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

# Базовая конфигурация.
celery_app.conf.update(
    task_track_started=True,        # статус STARTED (а не только PENDING→SUCCESS)
    task_time_limit=600,            # жёсткий лимит 10 мин на задачу (защита от зависаний)
    timezone="UTC",
)


@celery_app.task
def add(x: int, y: int) -> int:
    """Демо-задача: проверяем сквозной пайплайн producer → RabbitMQ → worker → Redis.

    На подшаге 3 заменим её на реальную scrape_vacancies.
    """
    return x + y