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


settings = Settings()
