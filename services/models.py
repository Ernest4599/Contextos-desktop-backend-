from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.sql import func

from services.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    is_admin = Column(Boolean, nullable=False, default=False, server_default="false")
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    email_verified = Column(Boolean, nullable=False, default=False, server_default="false")
    verification_token_hash = Column(String, nullable=True)
    verification_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    reset_token_hash = Column(String, nullable=True)
    reset_token_expires_at = Column(DateTime(timezone=True), nullable=True)


class AiosMemory(Base):
    __tablename__ = "aios_memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    content = Column(String, nullable=False)
    category = Column(String, index=True, nullable=False)
    source = Column(String, default="user_input")
    confidence = Column(String, default="medium")  # high | medium | low - set by the classify LLM call, see aios_service.py
    temporal_state = Column(String, default="unknown")  # permanent | current | temporary | historical | unknown
    status = Column(String, default="active")  # active | outdated | needs_review
    batch_id = Column(String, index=True, nullable=True)  # groups memories created/touched by one /aios/tell call
    last_confirmed_at = Column(DateTime(timezone=True), server_default=func.now())  # bumped when the user restates/reconfirms this fact
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    license_key = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=True)
    plan = Column(String, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    installation_id = Column(String, index=True, nullable=True)
    ip_hash = Column(String, nullable=True)
    credits_remaining = Column(Integer, nullable=True)
    aios_actions_today = Column(Integer, nullable=True, default=0)
    last_aios_reset_date = Column(DateTime(timezone=True), nullable=True)
    aios_credits_remaining = Column(Integer, nullable=True)


class LicenseRecoveryCode(Base):
    __tablename__ = "license_recovery_codes"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, index=True, nullable=False)
    code_hash = Column(String, nullable=False)
    status = Column(String, default="unused")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    used_at = Column(DateTime(timezone=True), nullable=True)


class LicenseRecoveryEvent(Base):
    __tablename__ = "license_recovery_events"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, index=True, nullable=True)
    event_type = Column(String, nullable=False)
    success = Column(String, nullable=False)
    ip_hash = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())


class AiosPreferences(Base):
    __tablename__ = "aios_preferences"

    user_id = Column(Integer, primary_key=True)
    personalization_level = Column(String, default="balanced")
    enabled_categories = Column(String, default="personality,preference,goal,interest,knowledge,writing_style,important_fact,context")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TermsAcceptance(Base):
    __tablename__ = "terms_acceptances"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=True)
    anon_id = Column(String, index=True, nullable=True)
    accepted_at = Column(DateTime(timezone=True), server_default=func.now())


class ContextPackage(Base):
    __tablename__ = "context_packages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    source = Column(String, nullable=False)
    title = Column(String, nullable=False)
    preview = Column(String, nullable=False)
    content = Column(String, nullable=False)
    project_id = Column(Integer, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False)
    user_id = Column(Integer, index=True, nullable=True)
    success = Column(Boolean, nullable=False)
    ip_hash = Column(String, nullable=True)
    detail = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LLMProviderEvent(Base):
    __tablename__ = "llm_provider_events"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, nullable=False)
    success = Column(Boolean, nullable=False)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class FreeTierLicense(Base):
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


class AiosEntity(Base):
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
