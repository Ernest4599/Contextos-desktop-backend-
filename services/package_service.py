"""
Context Package storage - signed-in accounts only. Saved automatically
whenever Import or Quick Prompt (regular or AIOS) finishes successfully.
Never created for license-only (no-account) access - per the standalone
license architecture, their output is shown once and never persisted.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from services.models import ContextPackage

MAX_TITLE_LENGTH = 80
MAX_PREVIEW_LENGTH = 160


class PackageError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _preview(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:MAX_PREVIEW_LENGTH] + ("…" if len(text) > MAX_PREVIEW_LENGTH else "")


def _serialize(p: ContextPackage) -> Dict[str, Any]:
    return {
        "id": p.id,
        "source": p.source,
        "title": p.title,
        "preview": p.preview,
        "content": p.content,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def save_package(db: Session, user_id: int, source: str, title: str, content: str) -> Dict[str, Any]:
    package = ContextPackage(
        user_id=user_id,
        source=source,
        title=title[:MAX_TITLE_LENGTH],
        preview=_preview(content),
        content=content,
    )
    db.add(package)
    db.commit()
    db.refresh(package)
    return _serialize(package)


def list_packages(db: Session, user_id: int) -> List[Dict[str, Any]]:
    packages = (
        db.query(ContextPackage)
        .filter(ContextPackage.user_id == user_id)
        .order_by(ContextPackage.created_at.desc())
        .all()
    )
    return [_serialize(p) for p in packages]


def delete_package(db: Session, user_id: int, package_id: int) -> None:
    package = (
        db.query(ContextPackage)
        .filter(ContextPackage.id == package_id, ContextPackage.user_id == user_id)
        .first()
    )
    if not package:
        raise PackageError("Package not found")
    db.delete(package)
    db.commit()


def clear_packages(db: Session, user_id: int) -> None:
    db.query(ContextPackage).filter(ContextPackage.user_id == user_id).delete()
    db.commit()
