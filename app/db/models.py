from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyVersion(Base):
    __tablename__ = 'strategy_versions'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    strategy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    weights: Mapped[dict] = mapped_column(JSONB, nullable=False)
    risk_constraints: Mapped[dict] = mapped_column(JSONB, nullable=False)
    weights_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Report(Base):
    __tablename__ = 'reports'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    run_mode: Mapped[str] = mapped_column(String, default='LIVE')
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Snapshot(Base):
    __tablename__ = 'daily_snapshots'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(String, nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String, default='FACTS')
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    data_quality_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MemoryNote(Base):
    __tablename__ = 'memory_notes'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('reports.id'), nullable=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default='')
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    importance: Mapped[int] = mapped_column(Integer, default=50)
    links: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Watchlist(Base):
    __tablename__ = 'watchlist'

    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default='ACTIVE')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ToolTrace(Base):
    __tablename__ = 'tool_traces'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    input_digest: Mapped[str] = mapped_column(String, default='')
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6, asdecimal=False), default=0)
    error_code: Mapped[str] = mapped_column(String, default='')
    ok: Mapped[bool] = mapped_column(default=True)
    degraded: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_wait_ms: Mapped[int] = mapped_column(Integer, default=0)
    rate_limited_wait_ms: Mapped[int] = mapped_column(Integer, default=0)
    policy_version: Mapped[str] = mapped_column(String, default='tool_wrapper_m6_v1')


class DecisionLog(Base):
    __tablename__ = 'decision_logs'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('reports.id'), nullable=False)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3, asdecimal=False), nullable=False)
    snapshot_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    model_primary: Mapped[str] = mapped_column(String, default='mock-primary')
    model_reviewer: Mapped[str] = mapped_column(String, default='NONE')
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6, asdecimal=False), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EventDoc(Base):
    __tablename__ = 'event_docs'

    doc_id: Mapped[str] = mapped_column(Text, primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ReportFeedback(Base):
    __tablename__ = 'report_feedback'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    report_id: Mapped[str] = mapped_column(String, nullable=False)
    feedback_label: Mapped[str] = mapped_column(String, nullable=False)
    comment: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
