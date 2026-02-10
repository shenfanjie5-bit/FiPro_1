"""m1 contract baseline

Revision ID: 20260210_0001
Revises: 
Create Date: 2026-02-10 11:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20260210_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('create extension if not exists pgcrypto')
    op.execute('create extension if not exists vector')

    op.create_table(
        'strategies',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.Text(), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    op.create_table(
        'strategy_versions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('strategy_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('strategies.id'), nullable=False),
        sa.Column('version', sa.Text(), nullable=False),
        sa.Column('weights', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('risk_constraints', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('weights_hash', sa.Text(), nullable=False),
        sa.Column('is_published', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('strategy_id', 'version', name='uq_strategy_versions_strategy_version'),
    )

    op.create_table(
        'tickers',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('ticker', sa.Text(), nullable=False, unique=True),
        sa.Column('market', sa.Text()),
        sa.Column('name', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    op.create_table(
        'daily_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('ticker', sa.Text(), nullable=False),
        sa.Column('asof', sa.DateTime(timezone=True), nullable=False),
        sa.Column('strategy_version_id', sa.Text(), nullable=False),
        sa.Column('snapshot_type', sa.Text(), nullable=False, server_default=sa.text("'FACTS'")),
        sa.Column('snapshot_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('data_quality_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.execute('create index if not exists idx_snapshots_ticker_asof on daily_snapshots(ticker, asof desc)')

    op.create_table(
        'reports',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('ticker', sa.Text(), nullable=False),
        sa.Column('asof', sa.DateTime(timezone=True), nullable=False),
        sa.Column('strategy_version_id', sa.Text(), nullable=False),
        sa.Column('tier', sa.Text(), nullable=False),
        sa.Column('run_mode', sa.Text(), nullable=False, server_default=sa.text("'LIVE'")),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('schema_version', sa.Text(), nullable=False),
        sa.Column('report_json', postgresql.JSONB(astext_type=sa.Text())),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("tier in ('TIER0','TIER1','TIER2')", name='ck_reports_tier'),
        sa.CheckConstraint("run_mode in ('LIVE','SHADOW','BACKTEST')", name='ck_reports_run_mode'),
        sa.CheckConstraint("status in ('QUEUED','RUNNING','DONE','FAILED')", name='ck_reports_status'),
    )
    op.execute('create index if not exists idx_reports_ticker_asof on reports(ticker, asof desc)')

    op.create_table(
        'decision_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('report_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('reports.id'), nullable=False),
        sa.Column('ticker', sa.Text(), nullable=False),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('confidence', sa.Numeric(4, 3), nullable=False),
        sa.Column('snapshot_ids', postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('model_primary', sa.Text(), nullable=False, server_default=sa.text("'mock-primary'")),
        sa.Column('model_reviewer', sa.Text(), nullable=False, server_default=sa.text("'NONE'")),
        sa.Column('cost_usd', sa.Numeric(10, 6), nullable=False, server_default=sa.text('0')),
        sa.Column('latency_ms', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("action in ('BUY','WATCH','AVOID')", name='ck_decision_logs_action'),
        sa.CheckConstraint('score between 0 and 100', name='ck_decision_logs_score'),
        sa.CheckConstraint('confidence between 0 and 1', name='ck_decision_logs_confidence'),
    )
    op.execute('create index if not exists idx_decision_logs_ticker_created on decision_logs(ticker, created_at desc)')

    op.create_table(
        'watchlist',
        sa.Column('ticker', sa.Text(), primary_key=True),
        sa.Column('tier', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("tier in ('TIER0','TIER1','TIER2')", name='ck_watchlist_tier'),
        sa.CheckConstraint("status in ('ACTIVE','PAUSED','ARCHIVED')", name='ck_watchlist_status'),
    )
    op.execute('create index if not exists idx_watchlist_tier_updated on watchlist(tier, updated_at desc)')

    op.create_table(
        'memory_notes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('ticker', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tags', postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('importance', sa.Integer(), nullable=False, server_default=sa.text('50')),
        sa.Column('links', postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint('importance between 0 and 100', name='ck_memory_notes_importance'),
    )

    op.execute('create table if not exists memory_embeddings (note_id uuid primary key references memory_notes(id) on delete cascade, embedding vector(1536) not null)')

    op.create_table(
        'tool_traces',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('report_id', postgresql.UUID(as_uuid=True)),
        sa.Column('tool_name', sa.Text(), nullable=False),
        sa.Column('input_digest', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Numeric(10, 6), nullable=False, server_default=sa.text('0')),
        sa.Column('error_code', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )


def downgrade() -> None:
    op.drop_table('tool_traces')
    op.execute('drop table if exists memory_embeddings')
    op.drop_table('memory_notes')
    op.execute('drop index if exists idx_watchlist_tier_updated')
    op.drop_table('watchlist')
    op.execute('drop index if exists idx_decision_logs_ticker_created')
    op.drop_table('decision_logs')
    op.execute('drop index if exists idx_reports_ticker_asof')
    op.drop_table('reports')
    op.execute('drop index if exists idx_snapshots_ticker_asof')
    op.drop_table('daily_snapshots')
    op.drop_table('tickers')
    op.drop_table('strategy_versions')
    op.drop_table('strategies')
