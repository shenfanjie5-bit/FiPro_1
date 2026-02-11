"""m6 trace audit fields

Revision ID: 20260211_0003
Revises: 20260211_0002
Create Date: 2026-02-11 12:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260211_0003'
down_revision = '20260211_0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if 'tool_traces' not in table_names:
        return

    existing_columns = {column['name'] for column in inspector.get_columns('tool_traces')}
    if 'degraded' not in existing_columns:
        op.add_column('tool_traces', sa.Column('degraded', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    if 'attempts' not in existing_columns:
        op.add_column('tool_traces', sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('1')))
    if 'retry_count' not in existing_columns:
        op.add_column('tool_traces', sa.Column('retry_count', sa.Integer(), nullable=False, server_default=sa.text('0')))
    if 'retry_wait_ms' not in existing_columns:
        op.add_column('tool_traces', sa.Column('retry_wait_ms', sa.Integer(), nullable=False, server_default=sa.text('0')))
    if 'rate_limited_wait_ms' not in existing_columns:
        op.add_column('tool_traces', sa.Column('rate_limited_wait_ms', sa.Integer(), nullable=False, server_default=sa.text('0')))
    if 'policy_version' not in existing_columns:
        op.add_column(
            'tool_traces',
            sa.Column('policy_version', sa.String(), nullable=False, server_default=sa.text("'tool_wrapper_m6_v1'")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if 'tool_traces' not in table_names:
        return

    existing_columns = {column['name'] for column in inspector.get_columns('tool_traces')}
    if 'policy_version' in existing_columns:
        op.drop_column('tool_traces', 'policy_version')
    if 'rate_limited_wait_ms' in existing_columns:
        op.drop_column('tool_traces', 'rate_limited_wait_ms')
    if 'retry_wait_ms' in existing_columns:
        op.drop_column('tool_traces', 'retry_wait_ms')
    if 'retry_count' in existing_columns:
        op.drop_column('tool_traces', 'retry_count')
    if 'attempts' in existing_columns:
        op.drop_column('tool_traces', 'attempts')
    if 'degraded' in existing_columns:
        op.drop_column('tool_traces', 'degraded')
