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
GRADE_NON_DEV = "Не разработчик"  # роль не про разработку (Data/ML/DevOps, аналитика, прочее)
GRADE_ORDER = [GRADE_JUNIOR, GRADE_MIDDLE, GRADE_SENIOR, GRADE_OTHER, GRADE_NONE, GRADE_NON_DEV]

# Роли hh, которые считаем разработкой. "96" — тот же «Программист, разработчик»,
# но иногда лежит сырым id (скрапер не всегда домапливает id→название). Всё
# остальное (Дата-сайентист=165, DevOps, «Другое»=40, аналитика…) — не разработка.
_DEV_ROLES = {"Программист, разработчик", "Руководитель группы разработки", "96"}


def is_developer(roles: list[str] | None) -> bool:
    """True, если среди professional_role есть роль разработки. Пустая роль → не
    разработчик (не можем подтвердить — не портим чистоту грейдовых групп)."""
    return bool(roles) and any(r in _DEV_ROLES for r in roles)

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


def centered_band(vals: list[float]) -> tuple[float, float]:
    """Симметричная вокруг медианы вилка, покрывающая ~50% значений: median ± MAD.

    MAD (median absolute deviation) — медиана модулей отклонений от медианы. По
    построению половина значений лежит внутри [median−MAD, median+MAD], поэтому
    вилка всегда центрирована на медиане (в отличие от p25–p75, которую скос
    зарплат уводит вбок) и устойчива к выбросам.
    """
    med = statistics.median(vals)
    mad = statistics.median([abs(x - med) for x in vals])
    return med - mad, med + mad


@dataclass(frozen=True)
class GradeSalary:
    grade: str
    count: int          # вакансий с зарплатой в группе
    mean_rub: int
    median_rub: int
    band_lo_rub: int    # нижняя граница центрированной вилки (median − MAD)
    band_hi_rub: int    # верхняя граница центрированной вилки (median + MAD)


def aggregate_by_grade(
    rows: list[tuple[str, int | None, int | None, str | None, list[str] | None]],
    rates: dict[str, float],
) -> list[GradeSalary]:
    """Считает среднюю/медианную зарплату и вилку (в рублях) по каждой группе.

    rows — кортежи (name, salary_min, salary_max, currency, professional_role).
    Не-разработчики (по роли) идут в отдельную группу GRADE_NON_DEV; разработчики
    делятся по грейду из названия — так «Грейд не указан» содержит только девелоперов.
    Вакансии без зарплаты/без курса валюты пропускаются. Пустые группы не возвращаются.
    """
    buckets: dict[str, list[float]] = {g: [] for g in GRADE_ORDER}
    for name, salary_min, salary_max, currency, roles in rows:
        rub = representative_rub(salary_min, salary_max, currency, rates)
        if rub is None:
            continue
        grade = detect_grade(name) if is_developer(roles) else GRADE_NON_DEV
        buckets[grade].append(rub)

    result: list[GradeSalary] = []
    for grade in GRADE_ORDER:
        vals = buckets[grade]
        if not vals:
            continue
        band_lo, band_hi = centered_band(vals)
        result.append(
            GradeSalary(
                grade=grade,
                count=len(vals),
                mean_rub=round(statistics.mean(vals)),
                median_rub=round(statistics.median(vals)),
                band_lo_rub=round(band_lo),
                band_hi_rub=round(band_hi),
            )
        )
    return result
