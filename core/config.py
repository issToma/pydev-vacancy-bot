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
    # Кому разрешено пользоваться ботом: "123,456" (свой id покажет @userinfobot).
    # Пусто = пускаем всех; так можно только локально и только пока бот ничего
    # не качает на диск — см. AllowlistMiddleware.
    allowed_user_ids: str = ""

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

    # --- Download (скачивание видео по ссылке через yt-dlp) ---
    # Готовые файлы остаются на диске машины (в Telegram не отправляются: лимит 50 МБ).
    # Относительный путь считается от рабочего каталога процесса (корень проекта).
    download_dir: str = "downloads"
    # Прокси ТОЛЬКО для yt-dlp (напр. socks5://127.0.0.1:1080). Скрапер hh.ru его не
    # видит и ходит напрямую — у них противоположные требования к VPN.
    ytdlp_proxy: str = ""
    # Браузер, из которого брать cookies (chrome/firefox/...): обход антибота YouTube.
    ytdlp_cookies_from_browser: str = ""
    # Готовый файл до этого размера отправляем в чат, что больше — остаётся на
    # диске. Потолок задан не нами: Bot API разрешает боту отправить максимум 50 МБ.
    download_inline_max_mb: int = 48
    # Ссылка на файлы, не влезающие в чат. Пусто = ссылок не выдаём (в чат идёт
    # только «файл в папке загрузок»). Дома — http://<ip-в-локальной-сети>:8080,
    # на сервере — https://<домен>; порт должен совпадать с запуском api.files.
    public_base_url: str = ""
    download_link_ttl_hours: int = 48       # сколько живёт ссылка
    # Предохранитель от выкачивания трафика по утёкшей ссылке. Считаются все
    # запросы, включая Range-куски при перемотке, поэтому порог высокий.
    download_link_max_hits: int = 1000
    download_time_limit: int = 7200         # потолок одной загрузки, секунды (2 ч)
    download_progress_interval: int = 15    # как часто править сообщение с прогрессом
    download_min_free_gb: int = 5           # ниже этого порога загрузку не начинаем
    # Потолок размера папки загрузок. Превышен — удаляем самые старые файлы,
    # пока не влезем. Самый свежий не удаляется никогда (см. _enforce_dir_limit).
    download_max_dir_gb: int = 80

    # Спенд-лимит: суммарный бюджет токенов LLM на пользователя за сутки (in+out).
    # Ограничивает СТОИМОСТЬ (в дополнение к лимиту по числу запросов). 0 — без лимита.
    llm_daily_token_budget: int = 100000

    @property
    def inline_max_bytes(self) -> int:
        """Порог отправки файла в чат, байты (сравнивать с размером файла удобнее так)."""
        return self.download_inline_max_mb * 1024 ** 2

    @property
    def allowed_ids(self) -> set[int]:
        """ALLOWED_USER_IDS ("123, 456") → множество id. Пустая строка → пустое.

        Опечатка валится на старте, а не молча: тихо отброшенный id означал бы
        либо запертого хозяина, либо открытого всем бота — оба случая хуже падения.
        """
        raw = [part.strip() for part in self.allowed_user_ids.split(",") if part.strip()]
        bad = [part for part in raw if not part.lstrip("-").isdigit()]
        if bad:
            raise ValueError(f"ALLOWED_USER_IDS: не числа — {bad}")
        return {int(part) for part in raw}


settings = Settings()
