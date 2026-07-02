FROM python:3.13-slim

# uv — быстрый менеджер зависимостей (берём бинарь из официального образа)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Сначала только манифесты: слой с зависимостями кешируется,
# пока pyproject.toml / uv.lock не меняются (быстрые пересборки)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Затем исходный код
COPY . .
RUN uv sync --frozen --no-dev

# Playwright Firefox + системные зависимости (нужны воркеру для скрапинга hh.ru).
# Единый образ для api/bot/worker — проще; при желании воркер выносится в отдельный
# Dockerfile (оптимизация размера api/bot). libgl1/libglib2.0-0 — для OpenCV (dewarp).
RUN uv run playwright install --with-deps firefox \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Команда задаётся в docker-compose (api / bot / worker); дефолт — API
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]