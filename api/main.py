from fastapi import FastAPI

from bot.registry import SERVICES, validate_registry

app = FastAPI(title="vacancy-bot API")


@app.get("/health")
async def health() -> dict[str, str]:
    """Проверка живости сервиса (для docker healthcheck и мониторинга)."""
    return {"status": "ok"}


# Монтируем REST-роутеры сервисов из реестра (ядро не знает конкретных сервисов).
validate_registry(SERVICES)
for service in SERVICES:
    router = service.api_router()
    if router is not None:
        app.include_router(router)