"""vacancy_status default new to available

Revision ID: 8b800dddb4f4
Revises: d7ce98324ac1
Create Date: 2026-08-05 10:35:12.057065

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8b800dddb4f4'
down_revision: Union[str, Sequence[str], None] = 'd7ce98324ac1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """vacancy_status: убираем мёртвое 'new'. Скрапер теперь пишет 'available'
    (vacancy_status убран из APP_OWNED), тулза откликов ставит 'archived'.
    Дефолт 'new' → 'available' + бэкфилл существующих строк."""
    op.execute("UPDATE vacancies_scraped SET vacancy_status = 'available' WHERE vacancy_status = 'new'")
    op.execute("ALTER TABLE vacancies_scraped ALTER COLUMN vacancy_status SET DEFAULT 'available'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE vacancies_scraped ALTER COLUMN vacancy_status SET DEFAULT 'new'")
    op.execute("UPDATE vacancies_scraped SET vacancy_status = 'new' WHERE vacancy_status = 'available'")
