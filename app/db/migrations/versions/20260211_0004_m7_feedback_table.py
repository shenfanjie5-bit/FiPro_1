"""m7 feedback table

Revision ID: 20260211_0004
Revises: 20260211_0003
Create Date: 2026-02-11 18:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260211_0004'
down_revision = '20260211_0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if 'report_feedback' not in table_names:
        op.create_table(
            'report_feedback',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column('report_id', sa.Text(), nullable=False),
            sa.Column('feedback_label', sa.Text(), nullable=False),
            sa.Column('comment', sa.Text(), nullable=False, server_default=sa.text("''")),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.CheckConstraint("feedback_label in ('USEFUL','USELESS','FALSE_POSITIVE')", name='ck_report_feedback_label'),
        )

    op.execute(
        'create index if not exists idx_report_feedback_report_created '
        'on report_feedback(report_id, created_at)'
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if 'report_feedback' not in table_names:
        return

    index_names = {index['name'] for index in inspector.get_indexes('report_feedback')}
    if 'idx_report_feedback_report_created' in index_names:
        op.drop_index('idx_report_feedback_report_created', table_name='report_feedback')
    op.drop_table('report_feedback')
