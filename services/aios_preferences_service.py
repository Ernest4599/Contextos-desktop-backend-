"""
AIOS Preferences: controls personalization level and which memory
categories AIOS is allowed to use, plus resetting the AIOS identity
(deleting all memories) - kept clearly separate from Clear All Data,
which wipes the entire account's application data.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from services.models import AiosPreferences, AiosMemory

VALID_LEVELS = ["minimal", "balanced", "maximum"]
ALL_CATEGORIES = [
    "personality", "preference", "goal", "interest",
    "knowledge", "writing_style", "important_fact", "context",
]


class AiosPreferencesError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _serialize(prefs: AiosPreferences) -> Dict[str, Any]:
    return {
        "personalization_level": prefs.personalization_level,
        "enabled_categories": prefs.enabled_categories.split(",") if prefs.enabled_categories else [],
    }


def get_preferences(db: Session, user_id: int) -> Dict[str, Any]:
    prefs = db.query(AiosPreferences).filter(AiosPreferences.user_id == user_id).first()
    if not prefs:
        prefs = AiosPreferences(user_id=user_id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return _serialize(prefs)


def update_preferences(db: Session, user_id: int, personalization_level: str, enabled_categories: List[str]) -> Dict[str, Any]:
    if personalization_level not in VALID_LEVELS:
        raise AiosPreferencesError("Invalid personalization level")

    invalid = [c for c in enabled_categories if c not in ALL_CATEGORIES]
    if invalid:
        raise AiosPreferencesError(f"Unknown categories: {', '.join(invalid)}")

    prefs = db.query(AiosPreferences).filter(AiosPreferences.user_id == user_id).first()
    if not prefs:
        prefs = AiosPreferences(user_id=user_id)

    prefs.personalization_level = personalization_level
    prefs.enabled_categories = ",".join(enabled_categories)
    db.add(prefs)
    db.commit()
    db.refresh(prefs)

    return _serialize(prefs)


def reset_aios_identity(db: Session, user_id: int) -> None:
    """Deletes all AIOS memories for this user - not the account itself."""
    db.query(AiosMemory).filter(AiosMemory.user_id == user_id).delete()
    db.commit()
