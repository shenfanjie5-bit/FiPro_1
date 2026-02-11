"""m3 persistence alignment

Revision ID: 20260211_0002
Revises: 20260210_0001
Create Date: 2026-02-11 10:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260211_0002'
down_revision = '20260210_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('memory_notes', sa.Column('report_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_memory_notes_report_id',
        'memory_notes',
        'reports',
        ['report_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index('idx_memory_notes_report_created', 'memory_notes', ['report_id', 'created_at'], unique=False)

    op.create_table(
        'event_docs',
        sa.Column('doc_id', sa.Text(), nullable=False),
        sa.Column('query', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('uri', sa.Text(), nullable=True),
        sa.Column('snippet', sa.Text(), nullable=False),
        sa.Column('checksum', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('doc_id'),
    )
    op.create_index('idx_event_docs_query_published', 'event_docs', ['query', 'published_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_event_docs_query_published', table_name='event_docs')
    op.drop_table('event_docs')

    op.drop_index('idx_memory_notes_report_created', table_name='memory_notes')
    op.drop_constraint('fk_memory_notes_report_id', 'memory_notes', type_='foreignkey')
    op.drop_column('memory_notes', 'report_id')
