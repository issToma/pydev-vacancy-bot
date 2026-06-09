from fastapi import FastAPI

app = FastAPI(title="vacancy-bot API")


@app.get("/health")
async def health() -> dict[str, str]:
    """Проверка живости сервиса (для docker healthcheck и мониторинга)."""
    return {"status": "ok"}