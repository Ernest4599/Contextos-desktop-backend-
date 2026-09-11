# --- 1. services/models.py: AiosEntity + AiosRelationship ---
p = "services/models.py"
s = open(p).read()
old = '''class AiosEditPattern(Base):'''
new = '''class AiosEntity(Base):
    """
    A node in the AIOS Brain graph - either a fact ABOUT the user
    (is_user_owned=True, e.g. "Software Engineer" as their occupation,
    "TechBit" as a page they run) or supporting knowledge AIOS needed TO
    UNDERSTAND something the user said (is_user_owned=False, e.g. what
    "Instagram" or "software engineer" generally means). The second kind
    is never surfaced to the user as one of "their" memories - see
    aios_service.py's docstring on the two kinds of knowledge.
    """
    __tablename__ = "aios_entities"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    entity_type = Column(String, nullable=False)  # person | organization | product | platform | project | concept | other
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)  # supporting knowledge, mainly for concept entities
    is_user_owned = Column(Boolean, nullable=False, default=True)
    confidence = Column(String, default="medium")  # high | medium | low
    source = Column(String, default="user_input")
    status = Column(String, default="active")  # active | historical | needs_review
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiosRelationship(Base):
    """
    A typed edge in the AIOS Brain graph. from_entity_id is nullable -
    null means "the user" is the source (e.g. USER -occupation-> Software
    Engineer), matching the algorithm's examples where most relationships
    originate from the user directly rather than from another entity.
    """
    __tablename__ = "aios_relationships"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    from_entity_id = Column(Integer, index=True, nullable=True)  # null = the user
    relationship_type = Column(String, nullable=False)  # verb phrase, e.g. "occupation", "runs", "platform", "topic"
    to_entity_id = Column(Integer, index=True, nullable=False)
    confidence = Column(String, default="medium")
    temporal_state = Column(String, default="unknown")  # permanent | current | temporary | historical | unknown
    status = Column(String, default="active")  # active | historical | needs_review
    source = Column(String, default="user_input")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiosEditPattern(Base):'''
assert s.count(old) == 1, "models.py: AiosEditPattern anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("models.py patched")


# --- 2. services/db.py: migration ---
p = "services/db.py"
s = open(p).read()
old = '''        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_edit_patterns_pattern_key ON aios_edit_patterns (pattern_key)"))'''
new = old + '''
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
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_aios_relationships_to_entity_id ON aios_relationships (to_entity_id)"))'''
assert s.count(old) == 1, "db.py: migration anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("db.py patched")
