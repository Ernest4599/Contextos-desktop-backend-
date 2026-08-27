"""
Projects: user-created containers for organizing Context Packages.
Real, user-named projects only - no mock/hardcoded data.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session
from sqlalchemy import desc

from services.models import Project

MAX_NAME_LENGTH = 100


class ProjectError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _serialize(project: Project) -> Dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def list_projects(db: Session, user_id: int) -> List[Dict[str, Any]]:
    projects = (
        db.query(Project)
        .filter(Project.user_id == user_id)
        .order_by(desc(Project.updated_at))
        .all()
    )
    return [_serialize(p) for p in projects]


def create_project(db: Session, user_id: int, name: str) -> Dict[str, Any]:
    name = (name or "").strip()

    if not name:
        raise ProjectError("Enter a project name")

    if len(name) > MAX_NAME_LENGTH:
        raise ProjectError("Project name is too long")

    existing = (
        db.query(Project)
        .filter(Project.user_id == user_id, Project.name == name)
        .first()
    )
    if existing:
        raise ProjectError("A project with this name already exists")

    project = Project(user_id=user_id, name=name)
    db.add(project)
    db.commit()
    db.refresh(project)

    return _serialize(project)


def get_project(db: Session, user_id: int, project_id: int) -> Dict[str, Any]:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == user_id)
        .first()
    )
    if not project:
        raise ProjectError("Project not found")
    return _serialize(project)


def delete_project(db: Session, user_id: int, project_id: int) -> None:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == user_id)
        .first()
    )
    if not project:
        raise ProjectError("Project not found")

    db.delete(project)
    db.commit()
