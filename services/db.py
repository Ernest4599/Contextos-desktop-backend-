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
        conn.execute(text("ALTER TABLE aios_memories ADD COLUMN IF NOT EXISTS temporal_state VARCHAR DEFAULT 'unknown'"))
        conn.execute(text("ALTER TABLE aios_memories ADD COLUMN IF NOT EXISTS last_confirmed_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS installation_id VARCHAR"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS ip_hash VARCHAR"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS credits_remaining INTEGER"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS aios_actions_today INTEGER DEFAULT 0"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS last_aios_reset_date TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS aios_credits_remaining INTEGER"))
        conn.execute(text("ALTER TABLE context_packages ADD COLUMN IF NOT EXISTS project_id INTEGER"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS aios_edit_patterns (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                pattern_key VARCHAR NOT NULL,
                occurrences INTEGER NOT NULL DEFAULT 0,
                last_seen_at TIMESTAMPTZ DEFAULT now(),
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_edit_patterns_user_id ON aios_edit_patterns (user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_edit_patterns_pattern_key ON aios_edit_patterns (pattern_key)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS aios_entities (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                entity_type VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                description VARCHAR,
                is_user_owned BOOLEAN NOT NULL DEFAULT true,
                confidence VARCHAR DEFAULT 'medium',
                source VARCHAR DEFAULT 'user_input',
                status VARCHAR DEFAULT 'active',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_entities_user_id ON aios_entities (user_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS aios_relationships (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                from_entity_id INTEGER,
                relationship_type VARCHAR NOT NULL,
                to_entity_id INTEGER NOT NULL,
                confidence VARCHAR DEFAULT 'medium',
                temporal_state VARCHAR DEFAULT 'unknown',
                status VARCHAR DEFAULT 'active',
                source VARCHAR DEFAULT 'user_input',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_relationships_user_id ON aios_relationships (user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_relationships_from_entity_id ON aios_relationships (from_entity_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_relationships_to_entity_id ON aios_relationships (to_entity_id)"))
