from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.database.session import get_db
from app.models.project import Project
from app.schemas.common import success
from app.services.sessions import (
    CurrentSession,
    get_current_session,
    require_admin,
    require_admin_csrf,
)

router = APIRouter(tags=["projects"])


class ProjectWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_code: str | None = Field(default=None, alias="projectCode", max_length=64)
    project_name: str = Field(alias="projectName", min_length=1, max_length=255)
    enabled: bool = True

    @field_validator("project_code")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("project_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("project name must not be blank")
        return normalized


def project_data(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "projectCode": project.project_code,
        "projectName": project.project_name,
        "enabled": project.enabled,
    }


def _find_duplicate(
    database: Session,
    body: ProjectWrite,
    *,
    exclude_id: int | None = None,
) -> Project | None:
    conditions = [func.lower(Project.project_name) == body.project_name.casefold()]
    if body.project_code:
        conditions.append(func.lower(Project.project_code) == body.project_code.casefold())
    statement = select(Project).where(or_(*conditions))
    if exclude_id is not None:
        statement = statement.where(Project.id != exclude_id)
    return database.scalar(statement.limit(1))


def _save(database: Session, project: Project) -> None:
    try:
        database.add(project)
        database.commit()
        database.refresh(project)
    except IntegrityError:
        database.rollback()
        raise ApiError("PROJECT_ALREADY_EXISTS", "项目编号或名称已存在", 409) from None


@router.get("/projects")
def search_projects(
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(get_current_session)],
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, object]:
    statement = select(Project).where(Project.enabled.is_(True))
    term = q.strip().casefold()
    if term:
        statement = statement.where(
            or_(
                func.lower(Project.project_code).contains(term, autoescape=True),
                func.lower(Project.project_name).contains(term, autoescape=True),
            )
        )
    projects = database.scalars(statement.order_by(Project.id).limit(limit)).all()
    return success([project_data(project) for project in projects])


@router.get("/admin/projects")
def list_admin_projects(
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(require_admin)],
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict[str, object]:
    statement = select(Project)
    term = q.strip().casefold()
    if term:
        statement = statement.where(
            or_(
                func.lower(Project.project_code).contains(term, autoescape=True),
                func.lower(Project.project_name).contains(term, autoescape=True),
            )
        )
    projects = database.scalars(statement.order_by(Project.id).limit(limit)).all()
    return success([project_data(project) for project in projects])


@router.post("/admin/projects", status_code=201)
def create_project(
    body: ProjectWrite,
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(require_admin_csrf)],
) -> dict[str, object]:
    if _find_duplicate(database, body):
        raise ApiError("PROJECT_ALREADY_EXISTS", "项目编号或名称已存在", 409)
    project = Project(
        project_code=body.project_code,
        project_name=body.project_name,
        enabled=body.enabled,
    )
    _save(database, project)
    return success(project_data(project))


@router.put("/admin/projects/{project_id}")
def update_project(
    project_id: int,
    body: ProjectWrite,
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(require_admin_csrf)],
) -> dict[str, object]:
    project = database.get(Project, project_id)
    if project is None:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在", 404)
    if _find_duplicate(database, body, exclude_id=project_id):
        raise ApiError("PROJECT_ALREADY_EXISTS", "项目编号或名称已存在", 409)
    project.project_code = body.project_code
    project.project_name = body.project_name
    project.enabled = body.enabled
    _save(database, project)
    return success(project_data(project))


@router.delete("/admin/projects/{project_id}")
def delete_project(
    project_id: int,
    database: Annotated[Session, Depends(get_db)],
    _current: Annotated[CurrentSession, Depends(require_admin_csrf)],
) -> dict[str, object]:
    project = database.get(Project, project_id)
    if project is None:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在", 404)
    database.delete(project)
    database.commit()
    return success({})
