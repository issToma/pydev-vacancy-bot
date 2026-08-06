from fastapi import FastAPI

from bot.registry import SERVICES, validate_registry

# Порядок и описания групп ручек в Swagger UI. Теги проставляются на самих
# роутах (tags=[...]); здесь — только человекочитаемые заголовки и очерёдность.
TAGS_METADATA = [
    {"name": "Вакансии", "description": "Поиск вакансий по фильтрам."},
    {"name": "Аналитика", "description": "Агрегаты по вакансиям (топ технологий)."},
    {"name": "Подписки", "description": "Подписки пользователя на фильтры вакансий."},
    {"name": "Служебное", "description": "Технические эндпоинты (проверка живости)."},
]

app = FastAPI(title="vacancy-bot API", openapi_tags=TAGS_METADATA)


@app.get("/health", tags=["Служебное"])
async def health() -> dict[str, str]:
    """Проверка живости сервиса (для docker healthcheck и мониторинга)."""
    return {"status": "ok"}


# Монтируем REST-роутеры сервисов из реестра (ядро не знает конкретных сервисов).
validate_registry(SERVICES)
for service in SERVICES:
    router = service.api_router()
    if router is not None:
        app.include_router(router)