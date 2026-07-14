from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения.

    Значения читаются из переменных окружения (и файла .env).
    Секреты в коде не хранятся — только имена переменных.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str

    # Подключение к PostgreSQL. Дефолт — локальный compose-postgres (хост-порт 5433).
    # В docker значение переопределяется через env DATABASE_URL (хост = postgres:5432).
    database_url: str = "postgresql+asyncpg://vacancy:vacancy@localhost:5433/vacancy"

    # Celery: брокер задач — RabbitMQ (amqp), backend результатов — Redis.
    # В docker хосты переопределяются на rabbitmq / redis.
    celery_broker_url: str = "amqp://vacancy:vacancy@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/0"

    # Redis для приложения (кэш, далее — rate-limit, FSM, память диалога).
    redis_url: str = "redis://localhost:6379/1"
    # TTL кэша топа технологий, секунды.
    stack_cache_ttl: int = 3600

    # Курс валют ЦБ РФ (для аналитики зарплат по грейдам). JSON-зеркало,
    # рублей за единицу валюты. ЦБ обновляет курс раз в сутки → кэшируем на 6 ч.
    cbr_fx_url: str = "https://www.cbr-xml-daily.ru/daily_json.js"
    fx_cache_ttl: int = 21600

    # RabbitMQ для прямых уведомлений (aio-pika producer/consumer).
    rabbitmq_url: str = "amqp://vacancy:vacancy@localhost:5672/"

    # Rate-limit команд бота: не более N сообщений за окно (сек) на пользователя.
    rate_limit_messages: int = 20
    rate_limit_window: int = 60

    # MinIO / S3 (объектное хранилище для Dewarp). В docker endpoint = http://minio:9000.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "vacancy"
    s3_secret_key: str = "vacancy123"
    s3_bucket_input: str = "dewarp-input"
    s3_bucket_output: str = "dewarp-output"

    # Путь к весам YOLO для деварпинга (в git не коммитится, см. weights/).
    dewarp_weights_path: str = "weights/best_26_1024_150_x_full_fix.pt"

    # --- Ask (Q&A с LLM) ---
    # Провайдер-агностично: любой OpenAI-совместимый endpoint. По умолчанию —
    # Google Gemini (бесплатный tier). Ключ — только из env/.env; пустой = /ask
    # ответит, что не настроен. Смена провайдера (Groq/OpenAI/Claude) = 3 строки в .env.
    llm_api_key: str = ""
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # gemini-2.5-flash — рабочая бесплатная модель (у 2.0-flash free-tier quota=0 в ряде регионов).
    llm_model: str = "gemini-2.5-flash"
    ask_max_tokens: int = 1024              # потолок длины ответа (контроль стоимости)
    # «Думающие» модели (Gemini 2.5) тратят бюджет токенов на рассуждения и режут
    # ответ. Для Q&A рассуждения не нужны → отключаем ("none"). "" = не передавать.
    ask_reasoning_effort: str = "none"
    ask_system_prompt: str = (
        "Ты — ассистент по программированию в стиле StackOverflow. "
        "Отвечай кратко и по делу: конкретный ответ на вопрос, при необходимости — "
        "короткий пример кода. Не генерируй крупные проекты или файлы целиком. "
        "Если вопрос неоднозначен — уточни. Отвечай на языке вопроса."
    )
    # Память диалога в Redis: сколько последних реплик хранить и как долго.
    ask_dialog_max_turns: int = 6           # реплик (user+assistant) в контексте
    ask_dialog_ttl: int = 3600              # TTL сессии диалога, секунды
    # Отдельный, более жёсткий лимит на /ask (дорогая команда): N запросов за окно.
    ask_rate_limit: int = 10
    ask_rate_limit_window: int = 3600

    # --- Match (подбор вакансий по резюме) ---
    match_candidates_limit: int = 25       # сколько кандидатов из БД уходит в LLM (не вся база)
    match_max_tokens: int = 1024           # потолок ответа-ранжирования
    match_rate_limit: int = 5              # /match дороже /ask (БД+LLM) → строже
    match_rate_limit_window: int = 3600

    # Спенд-лимит: суммарный бюджет токенов LLM на пользователя за сутки (in+out).
    # Ограничивает СТОИМОСТЬ (в дополнение к лимиту по числу запросов). 0 — без лимита.
    llm_daily_token_budget: int = 100000


settings = Settings()
