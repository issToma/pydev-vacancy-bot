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


settings = Settings()
