from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OaTemplateProfile(Base):
    __tablename__ = "oa_template_profiles"
    __table_args__ = (
        CheckConstraint(
            "compatibility_status IN ('COMPATIBLE', 'DRIFTED')",
            name="ck_oa_template_profiles_compatibility_status",
        ),
        CheckConstraint(
            "config_version > 0",
            name="ck_oa_template_profiles_config_version_positive",
        ),
    )

    profile_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    process_code: Mapped[str] = mapped_column(String(128), index=True)
    template_name: Mapped[str] = mapped_column(String(255))
    schema_fingerprint: Mapped[str] = mapped_column(String(64))
    confirmed_schema_fingerprint: Mapped[str] = mapped_column(String(64))
    schema_json: Mapped[str] = mapped_column(Text)
    mapping_json: Mapped[str] = mapped_column(Text)
    config_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    allowed_travel_process_codes_json: Mapped[str] = mapped_column(Text)
    related_approval_smoke_test_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )
    compatibility_status: Mapped[str] = mapped_column(String(32))
    confirmed_by_user_id: Mapped[str] = mapped_column(String(128))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now)
