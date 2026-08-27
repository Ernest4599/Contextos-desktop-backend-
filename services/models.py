from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

from services.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
