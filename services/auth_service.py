"""
Signup/login logic: validation, password hashing (bcrypt), stateless
session tokens (JWT), and email verification / password reset via
tokens sent through services/email_service.py. JWT_SECRET must be set
in the environment - any long random string works. Tokens expire after
30 days.

Email verification is required before login (hard verification, not
soft) - signup creates the account and sends a verification link but
does NOT return a session token. login() rejects unverified accounts.

Verification and reset tokens are never stored raw - only their SHA-256
hash, same principle as LicenseRecoveryCode. The raw token only ever
exists in the URL sent via email.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import secrets

import bcrypt
import jwt
from sqlalchemy.orm import Session

from services.models import User
from services.email_service import send_verification_email, send_password_reset_email, EmailError

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30

VERIFICATION_TOKEN_EXPIRY_HOURS = 24
RESET_TOKEN_EXPIRY_HOURS = 1

# Comma-separated list of emails that should be treated as admins. Checked
# and applied at login time so no manual DB edit is needed to bootstrap
# the first admin account - just set this env var to your own email.
_ADMIN_EMAILS = {
    e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
}

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
    if not re.search(r"[0-9]", password):
        raise AuthError("Password must include at least one number")
    if not re.search(r"[A-Za-z]", password):
        raise AuthError("Password must include at least one letter")


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_user_password(db: Session, user_id: int, password: str) -> bool:
    """Public entry point for step-up verification (e.g. /license/reveal) -
    reuses the same check as login, just keyed by user_id instead of email."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    return _verify_password(password, user.password_hash)


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


def signup(db: Session, email: str, password: str, confirm_password: str) -> tuple[int, str]:
    """Creates the account and sends a verification email. Does NOT log
    the user in - email verification is required before login (see
    login() below). Returns (user_id, email) - the id lets the caller
    link anonymous terms acceptance without needing a session token."""
    email = _validate_email(email)
    _validate_password(password)
    if password != confirm_password:
        raise AuthError("Passwords don't match")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise AuthError("Account already exists")

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=VERIFICATION_TOKEN_EXPIRY_HOURS)

    user = User(
        email=email,
        password_hash=_hash_password(password),
        email_verified=False,
        verification_token_hash=_hash_token(raw_token),
        verification_token_expires_at=expires_at,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        send_verification_email(user.email, raw_token)
    except EmailError:
        # Account is created either way - the user can request a fresh
        # link later. Don't fail signup just because the email send did.
        print(f"[AUTH] Failed to send verification email to {user.email}")

    return user.id, user.email


def verify_email(db: Session, token: str) -> str:
    """Confirms a verification token and activates the account. Returns
    the user's email on success."""
    token = (token or "").strip()
    if not token:
        raise AuthError("Invalid verification link")

    token_hash = _hash_token(token)
    user = db.query(User).filter(User.verification_token_hash == token_hash).first()

    if not user:
        raise AuthError("Invalid or already-used verification link")

    if user.verification_token_expires_at and user.verification_token_expires_at < datetime.datetime.now(datetime.timezone.utc):
        raise AuthError("This verification link has expired. Please request a new one.")

    user.email_verified = True
    user.verification_token_hash = None
    user.verification_token_expires_at = None
    db.add(user)
    db.commit()

    return user.email


def login(db: Session, email: str, password: str) -> tuple[str, str]:
    email = _validate_email(email)
    if not password:
        raise AuthError("Enter your password")

    user = db.query(User).filter(User.email == email).first()
    if not user or not _verify_password(password, user.password_hash):
        raise AuthError("Incorrect email or password")

    if not user.email_verified:
        raise AuthError("Please verify your email before signing in. Check your inbox for the verification link.")

    should_be_admin = user.email.lower() in _ADMIN_EMAILS
    if should_be_admin and not user.is_admin:
        user.is_admin = True
        db.add(user)
        db.commit()

    return _create_session_token(user.id, user.email), user.email


def request_password_reset(db: Session, email: str) -> None:
    """Always succeeds from the caller's perspective, regardless of
    whether the email exists - prevents leaking which emails are
    registered. Only sends an email if the account actually exists."""
    email = (email or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        return

    user = db.query(User).filter(User.email == email).first()
    if not user:
        return

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=RESET_TOKEN_EXPIRY_HOURS)

    user.reset_token_hash = _hash_token(raw_token)
    user.reset_token_expires_at = expires_at
    db.add(user)
    db.commit()

    try:
        send_password_reset_email(user.email, raw_token)
    except EmailError:
        print(f"[AUTH] Failed to send password reset email to {user.email}")


def reset_password(db: Session, token: str, new_password: str, confirm_password: str) -> None:
    token = (token or "").strip()
    if not token:
        raise AuthError("Invalid reset link")

    _validate_password(new_password)
    if new_password != confirm_password:
        raise AuthError("Passwords don't match")

    token_hash = _hash_token(token)
    user = db.query(User).filter(User.reset_token_hash == token_hash).first()

    if not user:
        raise AuthError("Invalid or already-used reset link")

    if user.reset_token_expires_at and user.reset_token_expires_at < datetime.datetime.now(datetime.timezone.utc):
        raise AuthError("This reset link has expired. Please request a new one.")

    user.password_hash = _hash_password(new_password)
    user.reset_token_hash = None
    user.reset_token_expires_at = None
    db.add(user)
    db.commit()


def get_user_id_from_token(authorization: str) -> int:
    """Extracts and validates the user id from an 'Authorization: Bearer <token>' header."""
    if not authorization.startswith("Bearer "):
        raise AuthError("Not signed in")
    token = authorization[len("Bearer "):]
    payload = decode_session_token(token)
    return int(payload["sub"])
