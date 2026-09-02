from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def utc_now() -> datetime:
    """Return UTC without tzinfo, the project convention for SQLite timestamps."""

    return datetime.now(UTC).replace(tzinfo=None)


class UserSession(Base):
    __tablename__ = "sessions"

    session_id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    dingtalk_user_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    corp_id: Mapped[str] = mapped_column(String(128), default="", server_default="")
    departments_json: Mapped[str] = mapped_column(Text)
    current_department_id: Mapped[str | None] = mapped_column(String(128))
    current_department_name: Mapped[str | None] = mapped_column(String(255))
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
