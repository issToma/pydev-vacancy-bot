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
    # include — модули с задачами, которые воркер импортирует при старте,
    # чтобы зарегистрировать их в реестре задач.
    include=["services.vacancy.tasks", "services.dewarp.tasks"],
)

# Базовая конфигурация.
celery_app.conf.update(
    task_track_started=True,        # статус STARTED (а не только PENDING→SUCCESS)
    task_time_limit=600,            # жёсткий лимит 10 мин на задачу (защита от зависаний)
    timezone="UTC",
)

# Расписание Beat: периодически запускать скрапинг вакансий.
# Beat — отдельный процесс-планировщик; он лишь КЛАДЁТ задачу в брокер по
# расписанию, а исполняет её обычный воркер. Интервал задаётся в секундах.
celery_app.conf.beat_schedule = {
    "scrape-vacancies-periodic": {
        "task": "services.vacancy.tasks.scrape_vacancies",
        "schedule": 5 * 60,                 # каждые 5 минут
        # если задача провисела в очереди дольше интервала — выбросить (не копить backlog)
        "options": {"expires": 5 * 60},
    },
}