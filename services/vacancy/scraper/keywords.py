import re

# Курируемый набор технологий/фраз для поля description_key_words.
SEARCH_KEYS = {
    # Core Python
    "python", "python 3", "asyncio", "async", "threading", "multiprocessing",
    # Web Frameworks (Backend)
    "django", "django rest", "django rest framework", "drf",
    "flask", "fastapi", "aiohttp", "tornado", "pyramid",
    "sqlalchemy", "alembic",
    # Frontend для Full-stack
    "react", "vue", "angular", "javascript", "typescript", "html", "css",
    # Облачные платформы
    "yandex cloud", "aws", "azure", "google cloud", "gcp",
    "heroku", "digitalocean", "cloud",
    # Базы данных
    "postgresql", "postgres", "mysql", "mongodb", "redis",
    "sqlite", "elasticsearch", "clickhouse", "cassandra",
    "sql", "nosql", "orm",
    # DevOps и инфраструктура
    "docker", "docker-compose", "kubernetes", "k8s",
    "ci/cd", "jenkins", "gitlab ci", "github actions",
    "ansible", "terraform", "vagrant",
    # Message Brokers
    "celery", "rabbitmq", "kafka", "redis queue", "rq",
    # Web серверы
    "nginx", "gunicorn", "uwsgi", "apache",
    # API
    "restful api", "rest api", "graphql", "grpc", "soap",
    "swagger", "openapi",
    # Тестирование
    "pytest", "unittest", "selenium", "playwright",
    "tdd", "bdd", "integration testing", "unit testing",
    # Data Science & ML
    "pandas", "numpy", "scikit-learn", "tensorflow",
    "pytorch", "keras", "jupyter", "matplotlib", "seaborn",
    "machine learning", "deep learning", "data science",
    "computer vision", "nlp", "ml",
    # Парсинг и краулинг
    "beautifulsoup", "scrapy", "requests",
    "парсинг", "web scraping",
    # ОС и системное администрирование
    "linux", "unix", "bash", "shell", "windows",
    # Системы контроля версий
    "git", "github", "gitlab", "bitbucket", "svn",
    # Архитектура и паттерны
    "ооп", "solid", "mvc", "mvp", "mvvm",
    "микросервисы", "монолит", "clean architecture",
    # Методологии
    "agile", "scrum", "kanban", "waterfall",
    # Безопасность
    "oauth", "jwt", "ssl", "безопасность", "security",
    # Другие библиотеки Python
    "pydantic", "marshmallow", "click", "typer",
    "pillow", "opencv", "httpx",
}


def extract_technologies(job_description: str) -> list[str]:
    r"""Ищет технологии из SEARCH_KEYS в тексте описания как отдельные слова.

    \b — граница слова (чтобы 'java' не находилось внутри 'javascript');
    re.escape — экранирует спецсимволы в названии (например 'c++', 'ci/cd').
    """
    found = []
    text_lower = (job_description or "").lower()
    for tech in SEARCH_KEYS:
        if re.search(r"\b" + re.escape(tech.lower()) + r"\b", text_lower):
            found.append(tech)
    return found