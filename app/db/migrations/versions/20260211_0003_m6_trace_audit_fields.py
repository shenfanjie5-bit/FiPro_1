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
    op.add_column('tool_traces', sa.Column('degraded', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('tool_traces', sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('1')))
    op.add_column('tool_traces', sa.Column('retry_count', sa.Integer(), nullable=False, server_default=sa.text('0')))
    op.add_column('tool_traces', sa.Column('retry_wait_ms', sa.Integer(), nullable=False, server_default=sa.text('0')))
    op.add_column('tool_traces', sa.Column('rate_limited_wait_ms', sa.Integer(), nullable=False, server_default=sa.text('0')))
    op.add_column(
        'tool_traces',
        sa.Column('policy_version', sa.String(), nullable=False, server_default=sa.text("'tool_wrapper_m6_v1'")),
    )


def downgrade() -> None:
    op.drop_column('tool_traces', 'policy_version')
    op.drop_column('tool_traces', 'rate_limited_wait_ms')
    op.drop_column('tool_traces', 'retry_wait_ms')
    op.drop_column('tool_traces', 'retry_count')
    op.drop_column('tool_traces', 'attempts')
    op.drop_column('tool_traces', 'degraded')
