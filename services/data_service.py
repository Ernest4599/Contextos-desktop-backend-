"""
Clear All Data: permanently deletes a user's locally-synced application
data (AIOS memories, projects). Deliberately does NOT touch the user's
account or license - those are separate concepts per the architecture.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from services.models import AiosMemory, Project, AiosPreferences


def clear_all_data(db: Session, user_id: int) -> None:
    db.query(AiosMemory).filter(AiosMemory.user_id == user_id).delete()
    db.query(Project).filter(Project.user_id == user_id).delete()
    db.query(AiosPreferences).filter(AiosPreferences.user_id == user_id).delete()
    db.commit()
