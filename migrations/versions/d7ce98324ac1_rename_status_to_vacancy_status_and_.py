"""rename status to vacancy_status and apply_flag to apply_status

Переименование полей vacancies_scraped для ясности:
  status     -> vacancy_status  (состояние вакансии на hh: new/available/archived)
  apply_flag -> apply_status     (статус НАШЕГО отклика; bool -> text)

apply_status: pending / applied / manual / error (по умолчанию pending).
Маппинг данных: apply_flag=true -> 'applied', иначе -> 'pending'.

Вью dev_vacancies/non_dev_vacancies (SELECT *) дропаем и пересоздаём — Postgres не
даёт менять тип колонки, пока от неё зависит вью.

Revision ID: d7ce98324ac1
Revises: ed13726573b8
Create Date: 2026-08-05 10:05:17.536589

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7ce98324ac1"
down_revision: Union[str, Sequence[str], None] = "ed13726573b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Тот же предикат, что в 672954aa721c (вью пересоздаём один-в-один, меняются только
# имена колонок, попадающие через SELECT *).
NON_DEV_PREDICATE = """
    name ~* '(qa|aqa|sdet|тестиров|автотест|test automation|engineer in test|аналитик|analyst|преподаватель|риск-менеджер)'
    OR (
        COALESCE(professional_role && ARRAY[
            'Тестировщик','BI-аналитик, аналитик данных','Аналитик',
            'Финансовый аналитик, инвестиционный аналитик','Бизнес-аналитик',
            'Учитель, преподаватель, педагог','Андеррайтер','Главный инженер проекта'
        ]::text[], false)
        AND NOT COALESCE(professional_role && ARRAY[
            'Программист, разработчик','Руководитель группы разработки'
        ]::text[], false)
    )
"""


def _create_views() -> None:
    op.execute(f"""
        CREATE OR REPLACE VIEW dev_vacancies AS
        SELECT * FROM vacancies_scraped
        WHERE NOT ({NON_DEV_PREDICATE})
        ORDER BY published_at DESC NULLS LAST
    """)
    op.execute(f"""
        CREATE OR REPLACE VIEW non_dev_vacancies AS
        SELECT * FROM vacancies_scraped
        WHERE {NON_DEV_PREDICATE}
        ORDER BY published_at DESC NULLS LAST
    """)


def upgrade() -> None:
    """Upgrade schema."""
    # 1) убрать зависимые вью, чтобы разрешить смену типа колонки
    op.execute("DROP VIEW IF EXISTS non_dev_vacancies")
    op.execute("DROP VIEW IF EXISTS dev_vacancies")

    # 2) status -> vacancy_status (тип не меняется)
    op.execute("ALTER TABLE vacancies_scraped RENAME COLUMN status TO vacancy_status")

    # 3) apply_flag(bool) -> apply_status(text): снять старый default, сменить тип с
    #    приведением значений, поставить новый default, переименовать
    op.execute("ALTER TABLE vacancies_scraped ALTER COLUMN apply_flag DROP DEFAULT")
    op.execute(
        "ALTER TABLE vacancies_scraped ALTER COLUMN apply_flag TYPE varchar(20) "
        "USING CASE WHEN apply_flag THEN 'applied' ELSE 'pending' END"
    )
    op.execute("ALTER TABLE vacancies_scraped ALTER COLUMN apply_flag SET DEFAULT 'pending'")
    op.execute("ALTER TABLE vacancies_scraped RENAME COLUMN apply_flag TO apply_status")

    # 4) пересоздать вью (SELECT * подхватит новые имена колонок)
    _create_views()


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS non_dev_vacancies")
    op.execute("DROP VIEW IF EXISTS dev_vacancies")

    op.execute("ALTER TABLE vacancies_scraped ALTER COLUMN apply_status DROP DEFAULT")
    op.execute(
        "ALTER TABLE vacancies_scraped ALTER COLUMN apply_status TYPE boolean "
        "USING CASE WHEN apply_status = 'applied' THEN true ELSE false END"
    )
    op.execute("ALTER TABLE vacancies_scraped ALTER COLUMN apply_status SET DEFAULT false")
    op.execute("ALTER TABLE vacancies_scraped RENAME COLUMN apply_status TO apply_flag")

    op.execute("ALTER TABLE vacancies_scraped RENAME COLUMN vacancy_status TO status")

    _create_views()
