from __future__ import annotations

import statistics
from dataclasses import dataclass

# Грейды. Порядок = порядок вывода. «Другое» — лиды/хеды/архитекторы,
# «Без указания» — в названии нет ни одного грейд-слова.
GRADE_JUNIOR = "Junior"
GRADE_MIDDLE = "Middle"
GRADE_SENIOR = "Senior"
GRADE_OTHER = "Лиды и выше"
GRADE_NONE = "Грейд не указан"
GRADE_ORDER = [GRADE_JUNIOR, GRADE_MIDDLE, GRADE_SENIOR, GRADE_OTHER, GRADE_NONE]

# Ключевые слова в названии (регистр игнорируем). Название паддим пробелами,
# поэтому " sr"/" jr" ловятся как отдельные токены, а не внутри слов (usr, ...).
_OTHER_KW = ("lead", "лид", "тимлид", "head", "principal", "принципал", "staff",
             "архитектор", "architect")
_SENIOR_KW = ("senior", "сеньор", "синьор", "старший", " sr")
_MIDDLE_KW = ("middle", "миддл", "мидл")
_JUNIOR_KW = ("junior", "джуниор", "джун", "младший", "trainee", "intern",
              "стажер", "стажёр", " jr")


def detect_grade(name: str) -> str:
    """Грейд по названию вакансии.

    Приоритет: лид/хед (→ «Другое») → Senior → Middle → Junior → «Без указания».
    «Другое» проверяем первым, чтобы тимлид/архитектор не попал в Senior.
    """
    n = f" {(name or '').lower()} "
    if any(k in n for k in _OTHER_KW):
        return GRADE_OTHER
    if any(k in n for k in _SENIOR_KW):
        return GRADE_SENIOR
    if any(k in n for k in _MIDDLE_KW):
        return GRADE_MIDDLE
    if any(k in n for k in _JUNIOR_KW):
        return GRADE_JUNIOR
    return GRADE_NONE


def representative_rub(
    salary_min: int | None,
    salary_max: int | None,
    currency: str | None,
    rates: dict[str, float],
) -> float | None:
    """Одно число зарплаты в рублях: середина вилки min–max, конвертированная по курсу.

    None, если зарплата не указана вовсе или валюта без известного курса
    (такую вакансию из расчёта исключаем, а не считаем нулём).
    """
    vals = [x for x in (salary_min, salary_max) if x]
    if not vals:
        return None
    rate = rates.get((currency or "RUR").upper())
    if not rate:
        return None
    midpoint = sum(vals) / len(vals)
    return midpoint * rate


@dataclass(frozen=True)
class GradeSalary:
    grade: str
    count: int          # вакансий с зарплатой в группе
    mean_rub: int
    median_rub: int


def aggregate_by_grade(
    rows: list[tuple[str, int | None, int | None, str | None]],
    rates: dict[str, float],
) -> list[GradeSalary]:
    """Считает среднюю и медианную зарплату (в рублях) по каждому грейду.

    rows — кортежи (name, salary_min, salary_max, currency). Вакансии без
    зарплаты/без курса валюты пропускаются. Пустые группы не возвращаются.
    """
    buckets: dict[str, list[float]] = {g: [] for g in GRADE_ORDER}
    for name, salary_min, salary_max, currency in rows:
        rub = representative_rub(salary_min, salary_max, currency, rates)
        if rub is None:
            continue
        buckets[detect_grade(name)].append(rub)

    result: list[GradeSalary] = []
    for grade in GRADE_ORDER:
        vals = buckets[grade]
        if not vals:
            continue
        result.append(
            GradeSalary(
                grade=grade,
                count=len(vals),
                mean_rub=round(statistics.mean(vals)),
                median_rub=round(statistics.median(vals)),
            )
        )
    return result
