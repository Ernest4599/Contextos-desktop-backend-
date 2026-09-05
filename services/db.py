"""
Database setup. Reads DATABASE_URL from the environment (Render's
PostgreSQL add-on sets this automatically once provisioned and linked).
Tolerant of it being unset so the rest of the app keeps working before
auth is configured - auth routes return a clear error instead.
"""
from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy 2.x requires the postgresql:// scheme; Render provides postgres://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine else None


def get_db_session():
    if SessionLocal is None:
        raise RuntimeError("Sign-in isn't available right now. Please try again later.")
    return SessionLocal()


def init_db() -> None:
    if engine is not None:
        from services import models  # noqa: F401 - ensures models are registered before create_all
        Base.metadata.create_all(bind=engine)
        _run_migrations()


def _run_migrations() -> None:
    """create_all() only creates missing tables, not missing columns on
    tables that already exist in production. This adds any columns
    introduced after a table was already live."""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE aios_memories ADD COLUMN IF NOT EXISTS batch_id VARCHAR"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE"))
