"""Add persisted OA template profiles and schema compatibility state.

Revision ID: 20260903_0007
Revises: 20260902_0006
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0007"
down_revision: str | None = "20260902_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oa_template_profiles",
        sa.Column("profile_key", sa.String(length=64), nullable=False),
        sa.Column("process_code", sa.String(length=128), nullable=False),
        sa.Column("template_name", sa.String(length=255), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confirmed_schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("schema_json", sa.Text(), nullable=False),
        sa.Column("mapping_json", sa.Text(), nullable=False),
        sa.Column("config_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("allowed_travel_process_codes_json", sa.Text(), nullable=False),
        sa.Column(
            "related_approval_smoke_test_confirmed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("compatibility_status", sa.String(length=32), nullable=False),
        sa.Column("confirmed_by_user_id", sa.String(length=128), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "compatibility_status IN ('COMPATIBLE', 'DRIFTED')",
            name="ck_oa_template_profiles_compatibility_status",
        ),
        sa.CheckConstraint(
            "config_version > 0",
            name="ck_oa_template_profiles_config_version_positive",
        ),
        sa.PrimaryKeyConstraint("profile_key"),
    )
    op.create_index(
        "ix_oa_template_profiles_process_code",
        "oa_template_profiles",
        ["process_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_oa_template_profiles_process_code", table_name="oa_template_profiles")
    op.drop_table("oa_template_profiles")
