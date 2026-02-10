from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StrategyVersion(Base):
    __tablename__ = 'strategy_versions'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    weights: Mapped[dict] = mapped_column(JSON, nullable=False)
    risk_constraints: Mapped[dict] = mapped_column(JSON, nullable=False)
    weights_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Report(Base):
    __tablename__ = 'reports'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    run_mode: Mapped[str] = mapped_column(String, default='LIVE')
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Snapshot(Base):
    __tablename__ = 'daily_snapshots'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(String, nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String, default='FACTS')
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    data_quality_json: Mapped[dict] = mapped_column(JSON, default={})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class MemoryNote(Base):
    __tablename__ = 'memory_notes'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default='')
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=[])
    importance: Mapped[int] = mapped_column(Integer, default=50)
    links: Mapped[list[str]] = mapped_column(ARRAY(String), default=[])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Watchlist(Base):
    __tablename__ = 'watchlist'

    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default='ACTIVE')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ToolTrace(Base):
    __tablename__ = 'tool_traces'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    report_id: Mapped[str] = mapped_column(String, default='')
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    input_digest: Mapped[str] = mapped_column(String, default='')
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    error_code: Mapped[str] = mapped_column(String, default='')
    ok: Mapped[str] = mapped_column(String, default='true')


class DecisionLog(Base):
    __tablename__ = 'decision_logs'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    report_id: Mapped[str] = mapped_column(String, nullable=False)
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    snapshot_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=[])
    model_primary: Mapped[str] = mapped_column(String, default='mock-primary')
    model_reviewer: Mapped[str] = mapped_column(String, default='NONE')
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
