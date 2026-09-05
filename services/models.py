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


class AiosMemory(Base):
    __tablename__ = "aios_memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    content = Column(String, nullable=False)
    category = Column(String, index=True, nullable=False)
    source = Column(String, default="user_input")
    confidence = Column(String, default="high")
    status = Column(String, default="active")  # active | outdated
    batch_id = Column(String, index=True, nullable=True)  # groups memories created/touched by one /aios/tell call
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
    user_id = Column(Integer, index=True, nullable=True)  # null until linked to an account
    plan = Column(String, nullable=False)
    status = Column(String, default="pending")  # pending | active | expired | revoked
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)


class LicenseRecoveryCode(Base):
    __tablename__ = "license_recovery_codes"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, index=True, nullable=False)
    code_hash = Column(String, nullable=False)
    status = Column(String, default="unused")  # unused | used
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    used_at = Column(DateTime(timezone=True), nullable=True)


class LicenseRecoveryEvent(Base):
    __tablename__ = "license_recovery_events"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, index=True, nullable=True)  # null if no matching license found
    event_type = Column(String, nullable=False)  # RECOVERY_ATTEMPT | RECOVERY_SUCCESS | RECOVERY_FAILURE | RECOVERY_LOCKED | CODE_USED | CODE_ROTATED
    success = Column(String, nullable=False)  # "true" | "false"
    ip_hash = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())


class AiosPreferences(Base):
    __tablename__ = "aios_preferences"

    user_id = Column(Integer, primary_key=True)
    personalization_level = Column(String, default="balanced")  # minimal | balanced | maximum
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
    source = Column(String, nullable=False)  # import | quick_prompt | aios_quick_prompt
    title = Column(String, nullable=False)
    preview = Column(String, nullable=False)
    content = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False)  # LOGIN_SUCCESS | LOGIN_FAILURE | RATE_LIMIT_EXCEEDED
    user_id = Column(Integer, index=True, nullable=True)  # null when the actor isn't known
    success = Column(Boolean, nullable=False)
    ip_hash = Column(String, nullable=True)
    detail = Column(String, nullable=True)  # optional context, e.g. the rate-limited route
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LLMProviderEvent(Base):
    __tablename__ = "llm_provider_events"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, nullable=False)  # anthropic | openai | gemini
    success = Column(Boolean, nullable=False)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
