from services.vacancy.salary_stats import (
    GRADE_JUNIOR,
    GRADE_MIDDLE,
    GRADE_NON_DEV,
    GRADE_NONE,
    GRADE_OTHER,
    GRADE_SENIOR,
    aggregate_by_grade,
    detect_grade,
    is_developer,
    representative_rub,
)

DEV = ["Программист, разработчик"]  # роль-разработчик для тестовых кортежей

RATES = {"RUB": 1.0, "RUR": 1.0, "USD": 80.0, "EUR": 90.0}


def test_detect_grade_basic():
    assert detect_grade("Senior Python Developer") == GRADE_SENIOR
    assert detect_grade("Миддл backend-разработчик") == GRADE_MIDDLE
    assert detect_grade("Junior QA") == GRADE_JUNIOR
    assert detect_grade("Стажёр аналитик") == GRADE_JUNIOR
    assert detect_grade("Python разработчик") == GRADE_NONE


def test_detect_grade_lead_is_other_not_senior():
    # лид/архитектор не должны попадать в Senior, даже если слово Senior есть
    assert detect_grade("Team Lead") == GRADE_OTHER
    assert detect_grade("Senior/Lead Engineer") == GRADE_OTHER
    assert detect_grade("Архитектор данных") == GRADE_OTHER


def test_representative_rub_midpoint_and_currency():
    # середина вилки в рублях
    assert representative_rub(100_000, 200_000, "RUR", RATES) == 150_000
    # только одна граница
    assert representative_rub(None, 120_000, "RUR", RATES) == 120_000
    # конвертация валюты
    assert representative_rub(1_000, 3_000, "USD", RATES) == 2_000 * 80.0
    # нет зарплаты / нет курса → None (вакансия исключается)
    assert representative_rub(None, None, "RUR", RATES) is None
    assert representative_rub(1_000, None, "GBP", RATES) is None


def test_aggregate_by_grade_mean_median():
    rows = [
        ("Junior Python", 100_000, 100_000, "RUR", DEV),
        ("Junior QA", 200_000, 200_000, "RUR", DEV),
        ("Middle Dev", 250_000, 250_000, "RUR", DEV),
        ("Vacancy without salary", None, None, "RUR", DEV),
    ]
    stats = {s.grade: s for s in aggregate_by_grade(rows, RATES)}

    assert stats[GRADE_JUNIOR].count == 2
    assert stats[GRADE_JUNIOR].mean_rub == 150_000
    assert stats[GRADE_JUNIOR].median_rub == 150_000
    # центрированная вилка median ± MAD; для [100k, 200k]: med=150k, MAD=50k → 100k–200k
    assert stats[GRADE_JUNIOR].band_lo_rub == 100_000
    assert stats[GRADE_JUNIOR].band_hi_rub == 200_000
    assert stats[GRADE_MIDDLE].count == 1
    # вакансия без зарплаты в статистику не попала → грейда Middle одна, Senior нет
    assert GRADE_SENIOR not in stats


def test_is_developer_by_role():
    assert is_developer(["Программист, разработчик"]) is True
    assert is_developer(["Руководитель группы разработки"]) is True
    assert is_developer(["96"]) is True                 # сырой id = «Программист, разработчик»
    assert is_developer(["Дата-сайентист"]) is False
    assert is_developer(["DevOps-инженер", "Другое"]) is False
    assert is_developer(None) is False                  # роль неизвестна → не разработчик
    # мульти-роль: есть роль разработки → считаем разработчиком
    assert is_developer(["Дата-сайентист", "Программист, разработчик"]) is True


def test_non_developers_go_to_separate_group():
    rows = [
        ("Python Developer", 200_000, 200_000, "RUR", ["Программист, разработчик"]),
        ("Python Developer", 250_000, 250_000, "RUR", ["96"]),       # сырой id → разработчик
        ("Data Scientist Python", 300_000, 300_000, "RUR", ["Дата-сайентист"]),
        ("DevOps Python", 400_000, 400_000, "RUR", ["DevOps-инженер"]),
        ("Python без роли", 150_000, 150_000, "RUR", None),          # роль неизвестна → не dev
    ]
    stats = {s.grade: s for s in aggregate_by_grade(rows, RATES)}

    # два разработчика без грейд-слова (в т.ч. сырой id 96) → «Грейд не указан»
    assert stats[GRADE_NONE].count == 2
    # data scientist + devops + вакансия без роли → отдельная группа «Не разработчик»
    assert stats[GRADE_NON_DEV].count == 3
