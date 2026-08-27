"""
Signup/login logic: validation, password hashing (bcrypt), and stateless
session tokens (JWT). JWT_SECRET must be set in the environment - any
long random string works. Tokens expire after 30 days.
"""
from __future__ import annotations

import datetime
import os
import re

import bcrypt
import jwt
from sqlalchemy.orm import Session

from services.models import User

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email")
    return email


def _validate_password(password: str) -> None:
    if not password or len(password) < 8:
        raise AuthError("Password must be at least 8 characters")


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _create_session_token(user_id: int, email: str) -> str:
    if not JWT_SECRET:
        raise AuthError("Sign-in isn't available right now. Please try again later.")
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=JWT_EXPIRY_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> dict:
    if not JWT_SECRET:
        raise AuthError("Sign-in isn't available right now. Please try again later.")
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired, please sign in again")
    except jwt.InvalidTokenError:
        raise AuthError("Invalid session")


def signup(db: Session, email: str, password: str, confirm_password: str) -> tuple[str, str]:
    email = _validate_email(email)
    _validate_password(password)
    if password != confirm_password:
        raise AuthError("Passwords don't match")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise AuthError("Account already exists")

    user = User(email=email, password_hash=_hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)

    return _create_session_token(user.id, user.email), user.email


def login(db: Session, email: str, password: str) -> tuple[str, str]:
    email = _validate_email(email)
    if not password:
        raise AuthError("Enter your password")

    user = db.query(User).filter(User.email == email).first()
    if not user or not _verify_password(password, user.password_hash):
        raise AuthError("Incorrect email or password")

    return _create_session_token(user.id, user.email), user.email
