"""SQLAlchemy 2.0 database layer for Aegis Exchange (Part 4).

Tables owned by Part 4:
- transactions  : every scored payment transaction
- alerts        : fraud alerts (risk_score >= ALERT_THRESHOLD)
- decisions     : analyst approve / dismiss records
- audit_log     : RBAC-sensitive access events

Configuration:
    AEGIS_DB_PATH   path to the SQLite file (default: data/aegis.db)
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# Resolve database path
# ---------------------------------------------------------------------------

def _resolve_db_path() -> str:
    """Return the absolute path to the SQLite file, creating its parent dirs."""
    raw = os.getenv("AEGIS_DB_PATH", "data/aegis.db")
    path = Path(raw)
    if not path.is_absolute():
        # Anchor relative paths to the repository root (two levels up from this file)
        root = Path(__file__).resolve().parent.parent
        path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def _make_engine(db_url: str | None = None):
    """Create a SQLAlchemy engine with WAL mode and a safe busy timeout."""
    if db_url is None:
        db_path = _resolve_db_path()
        db_url = f"sqlite:///{db_path}"

    engine = create_engine(
        db_url,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,          # seconds to wait when DB is locked
        },
        pool_pre_ping=True,
    )

    # Enable WAL mode and set busy_timeout once per connection
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")   # milliseconds
        cursor.execute("PRAGMA synchronous=NORMAL")    # safe with WAL
        cursor.close()

    return engine


# ---------------------------------------------------------------------------
# ORM Base and models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Shared declarative base for all Part 4 tables."""


class TransactionRow(Base):
    """One scored payment transaction."""

    __tablename__ = "transactions"

    txn_id = Column(String, primary_key=True, nullable=False)
    timestamp = Column(String, nullable=False)
    sender_token = Column(String, nullable=False)
    receiver_token = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    channel = Column(String, nullable=False)
    device_token = Column(String, nullable=False)
    risk_score = Column(Float, nullable=False)
    risk_level = Column(String, nullable=False)
    truth_is_scam = Column(Integer, nullable=True)   # 0 / 1 / NULL
    truth_typology = Column(String, nullable=True)
    reasons_json = Column(Text, nullable=False, default="[]")
    processing_ms = Column(Float, nullable=False, default=0.0)
    features_json = Column(Text, nullable=False, default="{}")


class AlertRow(Base):
    """Fraud alert generated when risk_score >= ALERT_THRESHOLD."""

    __tablename__ = "alerts"

    alert_id = Column(Integer, primary_key=True, autoincrement=True)
    txn_id = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    status = Column(String, nullable=False, default="OPEN")
    risk_score = Column(Float, nullable=False)
    risk_level = Column(String, nullable=False)
    reasons_json = Column(Text, nullable=False, default="[]")
    features_json = Column(Text, nullable=False, default="{}")
    sender_token = Column(String, nullable=False)
    receiver_token = Column(String, nullable=False)
    amount = Column(Float, nullable=False)


class DecisionRow(Base):
    """Analyst decision (APPROVE / DISMISS) for an alert."""

    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(Integer, nullable=False)
    decision = Column(String, nullable=False)
    analyst = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    decided_at = Column(String, nullable=False)


class AuditLogRow(Base):
    """Immutable audit record for sensitive access events."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    role = Column(String, nullable=False)
    action = Column(String, nullable=False)
    target = Column(String, nullable=False)
    detail_json = Column(Text, nullable=False, default="{}")


# ---------------------------------------------------------------------------
# Indexes for frequent query patterns
# ---------------------------------------------------------------------------

Index("ix_alerts_status", AlertRow.status)
Index("ix_alerts_risk_score", AlertRow.risk_score)
Index("ix_alerts_created_at", AlertRow.created_at)
Index("ix_alerts_txn_id", AlertRow.txn_id)
Index("ix_decisions_alert_id", DecisionRow.alert_id)
Index("ix_audit_log_ts", AuditLogRow.ts)
Index("ix_audit_log_target", AuditLogRow.target)
Index("ix_transactions_sender", TransactionRow.sender_token)
Index("ix_transactions_ts", TransactionRow.timestamp)


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

# Module-level engine / session factory, created lazily on first access.
_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine(db_url: str | None = None):
    """Return (or create) the module-level engine.

    Pass *db_url* explicitly only in tests so the module-level singleton is not
    polluted by test databases.
    """
    global _engine, _SessionLocal
    if _engine is None or db_url is not None:
        _engine = _make_engine(db_url)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session_factory(db_url: str | None = None) -> sessionmaker:
    """Return the session factory, initialising the engine first if necessary."""
    get_engine(db_url)
    assert _SessionLocal is not None
    return _SessionLocal


def init_db(db_url: str | None = None) -> None:
    """Create all tables idempotently.  Safe to call on every application startup."""
    engine = get_engine(db_url)
    Base.metadata.create_all(engine, checkfirst=True)
    # Additive, data-preserving migration for databases created before the
    # transaction explanations field was introduced.
    with engine.begin() as connection:
        columns = {row["name"] for row in connection.execute(text("PRAGMA table_info(transactions)")).mappings()}
        if "reasons_json" not in columns:
            connection.execute(text("ALTER TABLE transactions ADD COLUMN reasons_json TEXT NOT NULL DEFAULT '[]'"))
        if "processing_ms" not in columns:
            connection.execute(text("ALTER TABLE transactions ADD COLUMN processing_ms FLOAT NOT NULL DEFAULT 0.0"))
        if "features_json" not in columns:
            connection.execute(text("ALTER TABLE transactions ADD COLUMN features_json TEXT NOT NULL DEFAULT '{}'"))


def get_db() -> Session:
    """FastAPI dependency: yield a Session and close it after the request."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Test-only helpers
# ---------------------------------------------------------------------------

def make_test_engine(db_url: str = "sqlite:///:memory:"):
    """Create a fresh in-memory engine for isolated unit tests."""
    engine = _make_engine(db_url)
    Base.metadata.create_all(engine)
    return engine


def make_test_session(engine) -> Session:
    """Return a bound session for the given test engine."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory()
