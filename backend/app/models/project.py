from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def utc_now() -> datetime:
    """Return UTC without tzinfo, the project convention for SQLite timestamps."""

    return datetime.now(UTC).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_code: Mapped[str | None] = mapped_column(String(64), index=True)
    project_name: Mapped[str] = mapped_column(String(255), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now)


Index(
    "uq_projects_project_code_nocase",
    func.lower(Project.project_code),
    unique=True,
    sqlite_where=Project.project_code.is_not(None),
)
Index(
    "uq_projects_project_name_nocase",
    func.lower(Project.project_name),
    unique=True,
)
