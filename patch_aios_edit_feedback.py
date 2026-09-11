# --- 1. services/models.py: new tracking table ---
p = "services/models.py"
s = open(p).read()
old = '''class FreeTierLicense(Base):
    __tablename__ = "free_tier_licenses"

    id = Column(Integer, primary_key=True, index=True)
    installation_id = Column(String, unique=True, index=True, nullable=False)
    ip_hash = Column(String, index=True, nullable=True)
    status = Column(String, default="active")
    credits_remaining = Column(Integer, nullable=False, default=50)
    imports_used_today = Column(Integer, nullable=False, default=0)
    failed_attempts_today = Column(Integer, nullable=False, default=0)
    last_reset_date = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())'''
new = '''class FreeTierLicense(Base):
    __tablename__ = "free_tier_licenses"

    id = Column(Integer, primary_key=True, index=True)
    installation_id = Column(String, unique=True, index=True, nullable=False)
    ip_hash = Column(String, index=True, nullable=True)
    status = Column(String, default="active")
    credits_remaining = Column(Integer, nullable=False, default=50)
    imports_used_today = Column(Integer, nullable=False, default=0)
    failed_attempts_today = Column(Integer, nullable=False, default=0)
    last_reset_date = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AiosEditPattern(Base):
    """
    Tracks how many times a user's Quick Prompt edits have matched each
    fixed pattern_key (see aios_service.EDIT_PATTERN_KEYS), across
    separate Quick Prompt sessions. Intermediate counter only - never
    read as an AIOS memory itself. Once occurrences reaches the repeat
    threshold, aios_service writes a real low-confidence AiosMemory row
    and resets occurrences back to 0 here, per the "repeated behavior,
    not a single edit" rule in the Quick Prompt algorithm.
    """
    __tablename__ = "aios_edit_patterns"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    pattern_key = Column(String, index=True, nullable=False)
    occurrences = Column(Integer, nullable=False, default=0)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())'''
assert s.count(old) == 1, "models.py: FreeTierLicense anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("models.py patched")


# --- 2. services/db.py: migration for the new table ---
p = "services/db.py"
s = open(p).read()
old = '        conn.execute(text("ALTER TABLE context_packages ADD COLUMN IF NOT EXISTS project_id INTEGER"))'
new = old + '''
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
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_edit_patterns_pattern_key ON aios_edit_patterns (pattern_key)"))'''
assert s.count(old) == 1, "db.py: migration anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("db.py patched")
