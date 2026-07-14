from services.vacancy.salary_stats import (
    GRADE_JUNIOR,
    GRADE_MIDDLE,
    GRADE_NONE,
    GRADE_OTHER,
    GRADE_SENIOR,
    aggregate_by_grade,
    detect_grade,
    representative_rub,
)

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
        ("Junior Python", 100_000, 100_000, "RUR"),
        ("Junior QA", 200_000, 200_000, "RUR"),
        ("Middle Dev", 250_000, 250_000, "RUR"),
        ("Vacancy without salary", None, None, "RUR"),
    ]
    stats = {s.grade: s for s in aggregate_by_grade(rows, RATES)}

    assert stats[GRADE_JUNIOR].count == 2
    assert stats[GRADE_JUNIOR].mean_rub == 150_000
    assert stats[GRADE_JUNIOR].median_rub == 150_000
    assert stats[GRADE_MIDDLE].count == 1
    # вакансия без зарплаты в статистику не попала → грейда Middle одна, Senior нет
    assert GRADE_SENIOR not in stats
