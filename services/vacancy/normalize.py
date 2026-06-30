from __future__ import annotations

# Синоним-карта: варианты написания → каноническое имя технологии.
# Без неё одна технология «размазывается» по вариантам и топ врёт.
SYNONYMS = {
    "django framework": "django",
    "python 3": "python",
    "apache kafka": "kafka",
    "apache airflow": "airflow",
    "golang": "go",
    "vuejs": "vue",
    "vue.js": "vue",
    "nodejs": "node.js",
    "postgres": "postgresql",
    "rest": "rest api",
    "restful api": "rest api",
    "async": "asyncio",
    "gitlab ci/cd": "gitlab ci",
    "gitlab-ci": "gitlab ci",
    "k8s": "kubernetes",
    "docker-compose": "docker",
}

# Стоп-лист: общие/мусорные термины, которые не несут сигнала о стеке.
# python — намеренно (искать python в python-вакансиях бессмысленно, он почти везде).
STOP = {
    "ооп", "алгоритмы и структуры данных", "оптимизация кода", "рефакторинг кода",
    "управление командой", "постановка задач разработчикам", "обучение и развитие",
    "backend", "frontend", "fullstack", "devops", "api", "базы данных", "субд",
    "тестирование баз данных", "анализ тестирования", "apache", "python",
}


def normalize(token: str) -> str | None:
    """Приводит «сырой» термин к канонической технологии.

    Возвращает каноническое имя или None, если термин в стоп-листе/пустой.
    """
    t = (token or "").strip().lower()
    if not t:
        return None
    t = SYNONYMS.get(t, t)
    if t in STOP or t.startswith("английск"):
        return None
    return t