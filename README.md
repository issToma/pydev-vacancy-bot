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
| **Ask** | `/ask`, `/usage` | Q&A по программированию через LLM со **streaming**-ответом и памятью диалога; `/usage` — свой расход токенов |

## Навигация

Вместо «всё вперемешку» в одном чате — **разделы**: `/start` или `/menu` открывают главное
меню (reply-клавиатура), выбор раздела (Вакансии / Ask) показывает **кнопки-действия
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
   │            │     ├─ match    (LLM-ранжирование) → результат
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
Celery + Beat · RabbitMQ (aio-pika) · Redis · Playwright ·
LLM через OpenAI-совместимый API (Google Gemini по умолчанию, провайдер-агностично).

## Запуск (локальная разработка)

Требуется [uv](https://docs.astral.sh/uv/) и Docker (для инфраструктуры). Пользователь должен
иметь доступ к docker без sudo (`sudo usermod -aG docker $USER`, затем перелогиниться). Инфра-
контейнеры помечены `restart: unless-stopped` — после перезагрузки поднимаются сами.

**Старт/стоп одной командой:**
- `./run.sh` — поднять инфру (docker), прогнать миграции, запустить бот и воркер
  (логи в этом терминале; Ctrl+C останавливает оба).
- `./stop.sh` — остановить бот, воркер и инфра-контейнеры (остаются выключенными до `./run.sh`).

Скрипты дёргают docker через `sudo` (спросит пароль — это норм). Перед первым запуском один раз
заполни `.env` (ниже). Ниже — те же шаги вручную.

**Инфраструктура общая с другими ботами.** Postgres, Redis и RabbitMQ — одни
контейнеры на все проекты автора (боты выпрямления документов и скачивания живут
отдельными репозиториями). Сделано общим именем compose-проекта: `name: infra` в
начале `docker-compose.yml`, поэтому `docker compose up -d postgres` из любого
каталога управляет теми же контейнерами. Следствия: определения инфра-сервисов в
проектах обязаны совпадать, а `docker compose down` делать нельзя — он удалит
контейнеры всего проекта `infra`, включая чужие. `./stop.sh` использует `stop` с
перечислением сервисов и останавливает инфру, так что соседних ботов перед этим
лучше погасить.

```bash
# 1. Зависимости
uv sync

# 2. Инфраструктура в Docker (Postgres на хост-порту 5433, Redis, RabbitMQ)
docker compose up -d postgres redis rabbitmq

# 3. Конфигурация: создать .env из примера (-n не перезапишет существующий!) и
#    заполнить TELEGRAM_BOT_TOKEN и LLM_API_KEY
cp -n .env.example .env

# 4. Миграции БД
uv run alembic upgrade head

# 5. Запустить в двух отдельных терминалах — бот и воркер:
uv run python -m bot.main                                        # бот (long polling)
uv run celery -A core.celery_app:celery_app worker -B -l info -n vacancy@%h   # воркер + Beat
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
- **Воркер нужен** для `/match` и периодического скрапинга; бот сам обслуживает вакансии, `/stack`, `/ask` и навигацию.
- **Один бот на токен:** не запускай второй экземпляр с тем же токеном (в т.ч. docker-контейнер `bot`) параллельно локальному — Telegram отдаёт long-poll только одному (иначе `409 Conflict`).
- **Скрап hh.ru** работает с российского IP; из-за границы/через VPN hh может отдавать заглушку (см. журнал).

## Конфигурация

Все настройки — через переменные окружения (`pydantic-settings`), см. **`.env.example`** с описанием каждой.
Обязательны только `TELEGRAM_BOT_TOKEN` и `LLM_API_KEY`; у остального — рабочие дефолты под локальный compose.

## Доступ и безопасность

Дефолты в репозитории рассчитаны на **локальную разработку на одной машине**. Прежде чем
поднимать это где-то ещё, нужно сделать три вещи — иначе наружу смотрит слишком многое:

1. **`ALLOWED_USER_IDS`** — список Telegram-id, которым разрешён бот (свой покажет
   `@userinfobot`). Пустое значение пускает всех, а `/ask` и `/match` тратят квоту
   LLM. `AllowlistMiddleware` стоит первым в цепочке, чтобы посторонний не доходил
   даже до rate-limit.
2. **Порты инфраструктуры.** `docker-compose.yml` публикует Postgres, Redis и RabbitMQ
   на всех интерфейсах с паролями-заглушками (`vacancy:vacancy`, у Redis пароля нет
   вовсе). Для машины в общей сети добавь префикс `127.0.0.1:` к портам и смени пароли.
   Учти: публикация портов docker'ом пишет правила в `nat/DOCKER` мимо `INPUT`, поэтому
   ufw такие порты **не закрывает**.
3. **Основной API наружу не выставляем:** у него есть Swagger и `POST /subscriptions`
   без авторизации. Для локальной разработки это нормально, для чего-либо ещё — нет.

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
api/         FastAPI: main (/health + api_router сервисов)
core/        config, contracts (Infra/ServiceModule), db, redis, celery, llm, tg_html, models, ratelimit
services/    vacancy/ (скрапер, поиск, подписки, match), ask/
migrations/  Alembic
tests/       оффлайн-набор (pytest)
```

## Вне MVP (осознанно)

Kafka, ClickHouse, Elasticsearch, Prometheus/Grafana, Kubernetes, RAG/pgvector — на текущих
объёмах и одной ноде не оправданы. Умение объяснить, *почему не взято*, — часть ценности проекта.

## Лицензия

MIT — см. [LICENSE](LICENSE).

Скрапинг hh.ru используйте в рамках правил сервиса: ответственность за применение
лежит на пользователе.