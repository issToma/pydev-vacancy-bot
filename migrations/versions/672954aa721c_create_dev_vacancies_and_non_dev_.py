"""create dev_vacancies and non_dev_vacancies views

Revision ID: 672954aa721c
Revises: 4c7c0b0a4ce4
Create Date: 2026-06-30 14:08:33.837250

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "672954aa721c"
down_revision: Union[str, Sequence[str], None] = "4c7c0b0a4ce4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Предикат «не относится к разработке» (QA / аналитики / преподаватели и т.п.).
# Сигнал по названию сильнее, чем роль (hh иногда ошибочно тегирует роль).
# COALESCE(..., false) — NULL-безопасность для массива professional_role.
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


def upgrade() -> None:
    """Upgrade schema."""
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


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS non_dev_vacancies")
    op.execute("DROP VIEW IF EXISTS dev_vacancies")