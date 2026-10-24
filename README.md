# vacancy_bot

<!-- Бейдж заработает после привязки к GitHub: замени OWNER на свой логин. -->
[![tests](https://github.com/OWNER/vacancy_bot/actions/workflows/tests.yml/badge.svg)](https://github.com/OWNER/vacancy_bot/actions/workflows/tests.yml)

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
| **Download** | `/dl`, `/downloads` | Скачивание видео/аудио по ссылке (yt-dlp): выбор качества, прогресс в одном сообщении, автоочистка папки; мелкие файлы — в чат, крупные — ссылкой с токеном |

## Навигация

Вместо «всё вперемешку» в одном чате — **разделы**: `/start` или `/menu` открывают главное
меню (reply-клавиатура), выбор раздела (Вакансии / Dewarp / Ask / Скачать) показывает **кнопки-действия
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
   │            │     ├─ scrape   (Playwright) → PostgreSQL   (по расписанию, Beat)
   │            │     ├─ dewarp   (OpenCV/YOLO) → S3/MinIO
   │            │     ├─ match    (LLM-ранжирование) → результат
   │            │     └─ download (yt-dlp) → диск ──► api.files: /d/{token}
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
yt-dlp + ffmpeg · LLM через OpenAI-совместимый API (Google Gemini по умолчанию,
провайдер-агностично).

Внешние бинарники: **ffmpeg/ffprobe** (склейка дорожек, обложка и метаданные видео) —
`apt install ffmpeg`. Без них работает всё, кроме раздела «Скачать».

## Запуск (локальная разработка)

Требуется [uv](https://docs.astral.sh/uv/) и Docker (для инфраструктуры). Пользователь должен
иметь доступ к docker без sudo (`sudo usermod -aG docker $USER`, затем перелогиниться). Инфра-
контейнеры помечены `restart: unless-stopped` — после перезагрузки поднимаются сами.

**Старт/стоп одной командой:**
- `./run.sh` — поднять инфру (docker), прогнать миграции, запустить бот + воркер + раздачу
  скачанных файлов на `:8080` (логи в этом терминале; Ctrl+C останавливает всё трое).
- `./stop.sh` — остановить бот, воркер и инфра-контейнеры (остаются выключенными до `./run.sh`).

Скрипты дёргают docker через `sudo` (спросит пароль — это норм). Перед первым запуском один раз
заполни `.env` (ниже). Ниже — те же шаги вручную.

```bash
# 1. Зависимости
uv sync

# 2. Инфраструктура в Docker (Postgres на хост-порту 5433, Redis, RabbitMQ, MinIO)
docker compose up -d postgres redis rabbitmq minio

# 3. Конфигурация: создать .env из примера (-n не перезапишет существующий!) и
#    заполнить TELEGRAM_BOT_TOKEN и LLM_API_KEY
cp -n .env.example .env

# 4. Миграции БД
uv run alembic upgrade head

# 5. Запустить в двух отдельных терминалах — бот и воркер:
uv run python -m bot.main                                        # бот (long polling)
uv run celery -A core.celery_app:celery_app worker -B -l info    # воркер + Beat (задачи, скрап)
```

REST-слой FastAPI **поднимать не обязательно** — бот ходит в инфраструктуру напрямую, а не через HTTP. Если нужен API/`/health` или **Swagger UI**:
```bash
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000
# c авто-перезагрузкой при правках кода:
uv run uvicorn api.main:app --reload
```
Документация API (автогенерация FastAPI), пока сервер запущен:
- **Swagger UI:** http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc · OpenAPI JSON: http://127.0.0.1:8000/openapi.json

Погасить сервер (по порту):
```bash
kill $(ss -ltnp 2>/dev/null | grep :8000 | grep -oP 'pid=\K[0-9]+')
```

- **Ключи** (`TELEGRAM_BOT_TOKEN`, `LLM_API_KEY`) — только в `.env` (в git не коммитится).
- `LLM_API_KEY` бесплатно берётся в Google AI Studio (`aistudio.google.com`). Без ключа `/ask` и `/match` ответят «LLM не настроен», остальное работает.
- **Воркер нужен** для `/dewarp`, `/match` и периодического скрапинга; бот сам обслуживает вакансии, `/stack`, `/ask` и навигацию.
- **Один бот на токен:** не запускай второй экземпляр с тем же токеном (в т.ч. docker-контейнер `bot`) параллельно локальному — Telegram отдаёт long-poll только одному (иначе `409 Conflict`).
- **Скрап hh.ru** работает с российского IP; из-за границы/через VPN hh может отдавать заглушку (см. журнал).

## Конфигурация

Все настройки — через переменные окружения (`pydantic-settings`), см. **`.env.example`** с описанием каждой.
Обязательны только `TELEGRAM_BOT_TOKEN` и `LLM_API_KEY`; у остального — рабочие дефолты под локальный compose.

## Доступ и безопасность

Дефолты в репозитории рассчитаны на **локальную разработку на одной машине**. Прежде чем
поднимать это где-то ещё, нужно сделать три вещи — иначе наружу смотрит слишком многое:

1. **`ALLOWED_USER_IDS`** — список Telegram-id, которым разрешён бот (свой покажет
   `@userinfobot`). Пустое значение пускает всех, а бот умеет занимать диск и трафик:
   ссылку на скачивание присылает пользователь. `AllowlistMiddleware` стоит первым в
   цепочке, чтобы посторонний не доходил даже до rate-limit.
2. **Порты инфраструктуры.** `docker-compose.yml` публикует Postgres, Redis, RabbitMQ и
   MinIO на всех интерфейсах с паролями-заглушками (`vacancy:vacancy`, у Redis пароля нет
   вовсе). Для машины в общей сети добавь префикс `127.0.0.1:` к портам и смени пароли.
   Учти: публикация портов docker'ом пишет правила в `nat/DOCKER` мимо `INPUT`, поэтому
   ufw такие порты **не закрывает**.
3. **Раздача файлов** (`api.files`, порт 8080) — отдельное приложение, а не ручка в
   `api.main`: у основного API есть Swagger и `POST /subscriptions` без авторизации.
   Ссылки одноразово-токенные (256 бит, TTL и лимит обращений в настройках), путь берётся
   из БД и проверяется на вложенность в папку загрузок, любая неудача — одинаковый 404.
   Но HTTPS тут нет, так что на публичный адрес её ставить только за реверс-прокси.

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
bot/         aiogram: main (polling + notifier), registry, middlewares, navigation, help
api/         FastAPI: main (/health + api_router сервисов), files (раздача по токену)
core/        config, contracts (Infra/ServiceModule), db, redis, s3, celery, llm, tg_html, models, ratelimit
services/    vacancy/ (скрапер, поиск, подписки, match), dewarp/ (алгоритм+задача),
             ask/, download/ (formats, service, tasks)
migrations/  Alembic
tests/       оффлайн-набор (pytest)
docs/        journal.md — журнал решений и «собесные» вопросы
```

## Документы

- **`vacancy_bot_spec.md`** — полное ТЗ и обоснование стека.
- **`docs/journal.md`** — журнал архитектурных решений по шагам.

## Вне MVP (осознанно)

Kafka, ClickHouse, Elasticsearch, Prometheus/Grafana, Kubernetes, RAG/pgvector — на текущих
объёмах и одной ноде не оправданы. Умение объяснить, *почему не взято*, — часть ценности проекта.

## Лицензия

MIT — см. [LICENSE](LICENSE).

Скрапинг hh.ru и скачивание видео используйте в рамках правил соответствующих сервисов:
ответственность за применение лежит на пользователе.