# vacancy_bot

Telegram-бот-гейтвей (gateway) к набору бэкенд-сервисов. Учебный pet-проект для практики
современного Python-стека, где каждая технология взята под конкретную задачу.

Единая точка входа — Telegram-бот (aiogram). Он вызывает REST-слой (FastAPI); тяжёлые задачи
уходят в фон (Celery + RabbitMQ), интерактивные — исполняются напрямую со стримингом.

## Сервисы

| Сервис | Команды | Что делает |
|---|---|---|
| **Vacancy Radar** | `/vacancies`, `/filter`, `/stack`, `/subscribe`, `/subscriptions`, `/match` | Поиск/аналитика вакансий (скрапинг hh.ru), подписки с уведомлениями, топ технологий, подбор по резюме через LLM |
| **Dewarp** | `/dewarp` | Выпрямление фото документов (OpenCV/YOLO) → результат из S3/MinIO |
| **Ask** | `/ask`, `/usage` | Q&A по программированию через LLM со **streaming**-ответом и памятью диалога; `/usage` — свой расход токенов |

## Навигация

Вместо «всё вперемешку» в одном чате — **разделы**: `/start` или `/menu` открывают главное
меню (reply-клавиатура), выбор раздела (Вакансии / Dewarp / Ask) показывает **кнопки-действия
этого раздела понятными словами** («🔎 Поиск», «📊 Топ стек», «🤖 Задать вопрос» …). Кнопки,
требующие ввода (поиск, вопрос, резюме), по нажатию спрашивают текст. `/`-меню при этом пустое —
навигация идёт кнопками; сами команды (`/vacancies`, `/ask` …) продолжают работать по вводу.
Наборы кнопок берутся из реестра (`ServiceModule.menu_buttons`), логика сервисов не дублируется.

> Telegram не даёт раздельных историй в одном ЛС с ботом (нет тредов) — поэтому «категории»
> реализованы как режимы одного бота, а не отдельные чаты.

## Архитектура

```
Telegram → aiogram-бот ──HTTP──► FastAPI (REST-слой сервисов)
   ▲            │
   │            ├─ тяжёлое → RabbitMQ → Celery-воркеры:
   │            │     ├─ scrape (Playwright) → PostgreSQL   (по расписанию, Beat)
   │            │     ├─ dewarp (OpenCV/YOLO) → S3/MinIO
   │            │     └─ match  (LLM-ранжирование) → результат
   │            │
   │            └─ /ask: прямой async + streaming к LLM (НЕ Celery)
   │
   └── notify: Celery/скрап publish → RabbitMQ → прямой consumer в боте → сообщение
```

Ключевые решения:
- **Бот — клиент, сервисы — REST на FastAPI**: сервисы переиспользуемы вне Telegram.
- **Sync/async разделение нагрузки**: тяжёлое и долгое → Celery; интерактивное (`/ask`) → прямой async со стримингом.
- **RabbitMQ используется и напрямую** (producer/consumer уведомлений с DLQ), не только за Celery.
- **Расширяемость**: сервис = класс по контракту `ServiceModule` + одна строка в реестр (`bot/registry.py`); ядро не трогается.

## Стек

Python 3.13 · **uv** · aiogram 3 · FastAPI · SQLAlchemy 2.0 (async, asyncpg) + Alembic ·
Celery + Beat · RabbitMQ (aio-pika) · Redis · MinIO/S3 (aioboto3) · Playwright · OpenCV/YOLO ·
LLM через OpenAI-совместимый API (Google Gemini по умолчанию, провайдер-агностично).

## Запуск (локальная разработка)

Требуется [uv](https://docs.astral.sh/uv/) и Docker (для инфраструктуры). Пользователь должен
иметь доступ к docker без sudo (`sudo usermod -aG docker $USER`, затем перелогиниться). Инфра-
контейнеры помечены `restart: unless-stopped` — после перезагрузки поднимаются сами.

```bash
# 1. Зависимости
uv sync

# 2. Инфраструктура в Docker (Postgres на хост-порту 5433, Redis, RabbitMQ, MinIO)
docker compose up -d postgres redis rabbitmq minio

# 3. Конфигурация: скопировать пример и заполнить TELEGRAM_BOT_TOKEN и LLM_API_KEY
cp .env.example .env

# 4. Миграции БД
uv run alembic upgrade head

# 5. Запустить в двух отдельных терминалах — бот и воркер:
uv run python -m bot.main                                        # бот (long polling)
uv run celery -A core.celery_app:celery_app worker -B -l info    # воркер + Beat (задачи, скрап)
```

REST-слой FastAPI **поднимать не обязательно** — бот ходит в инфраструктуру напрямую, а не через HTTP. Если нужен API/`/health`:
```bash
uv run uvicorn api.main:app --reload
```

- **Ключи** (`TELEGRAM_BOT_TOKEN`, `LLM_API_KEY`) — только в `.env` (в git не коммитится).
- `LLM_API_KEY` бесплатно берётся в Google AI Studio (`aistudio.google.com`). Без ключа `/ask` и `/match` ответят «LLM не настроен», остальное работает.
- **Воркер нужен** для `/dewarp`, `/match` и периодического скрапинга; бот сам обслуживает вакансии, `/stack`, `/ask` и навигацию.
- **Один бот на токен:** не запускай второй экземпляр с тем же токеном (в т.ч. docker-контейнер `bot`) параллельно локальному — Telegram отдаёт long-poll только одному (иначе `409 Conflict`).
- **Скрап hh.ru** работает с российского IP; из-за границы/через VPN hh может отдавать заглушку (см. журнал).

## Конфигурация

Все настройки — через переменные окружения (`pydantic-settings`), см. **`.env.example`** с описанием каждой.
Обязательны только `TELEGRAM_BOT_TOKEN` и `LLM_API_KEY`; у остального — рабочие дефолты под локальный compose.

## Миграции

```bash
uv run alembic upgrade head                          # применить
uv run alembic revision --autogenerate -m "msg"      # сгенерировать по моделям
```

## Тесты

```bash
uv run pytest
```

Оффлайн-набор (без живых сервисов): контракт реестра, `to_telegram_html`, матчинг подписок,
rate-limit (на фейк-Redis), хелперы `/match`, эндпоинт `GET /health` (httpx + ASGITransport).

## Структура

```
bot/        aiogram: main (polling + notifier), registry, middlewares, help
api/         FastAPI: /health + api_router сервисов
core/        config, contracts (Infra/ServiceModule), db, redis, s3, celery, llm, tg_html, models, ratelimit
services/    vacancy/ (скрапер, поиск, подписки, match), dewarp/ (алгоритм+задача), ask/
migrations/  Alembic
docs/        journal.md — журнал решений и «собесные» вопросы
```

## Документы

- **`vacancy_bot_spec.md`** — полное ТЗ и обоснование стека.
- **`docs/journal.md`** — журнал архитектурных решений по шагам.

## Вне MVP (осознанно)

Kafka, ClickHouse, Elasticsearch, Prometheus/Grafana, Kubernetes, RAG/pgvector — на текущих
объёмах и одной ноде не оправданы. Умение объяснить, *почему не взято*, — часть ценности проекта.