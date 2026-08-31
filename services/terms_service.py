"""
Terms & Conditions acceptance tracking. Supports both anonymous visitors
(tracked via a random cookie id) and signed-in accounts (tracked via
user_id). When an anonymous visitor who already accepted later signs up
or logs in, their acceptance is linked to the new account so they
aren't asked again.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from services.models import TermsAcceptance


def has_accepted(db: Session, user_id: int | None, anon_id: str | None) -> bool:
    if user_id is not None:
        record = db.query(TermsAcceptance).filter(TermsAcceptance.user_id == user_id).first()
        if record:
            return True
    if anon_id:
        record = db.query(TermsAcceptance).filter(TermsAcceptance.anon_id == anon_id).first()
        if record:
            return True
    return False


def accept(db: Session, user_id: int | None, anon_id: str | None) -> None:
    if user_id is not None:
        existing = db.query(TermsAcceptance).filter(TermsAcceptance.user_id == user_id).first()
        if not existing:
            db.add(TermsAcceptance(user_id=user_id))
            db.commit()
        return

    if anon_id:
        existing = db.query(TermsAcceptance).filter(TermsAcceptance.anon_id == anon_id).first()
        if not existing:
            db.add(TermsAcceptance(anon_id=anon_id))
            db.commit()


def link_anon_to_user(db: Session, anon_id: str | None, user_id: int) -> None:
    """Called right after a successful signup/login. If the anonymous
    session already accepted the terms, mark the account as accepted
    too so the user isn't prompted again."""
    if not anon_id:
        return

    anon_record = db.query(TermsAcceptance).filter(TermsAcceptance.anon_id == anon_id).first()
    if not anon_record:
        return

    user_record = db.query(TermsAcceptance).filter(TermsAcceptance.user_id == user_id).first()
    if not user_record:
        db.add(TermsAcceptance(user_id=user_id))
        db.commit()
