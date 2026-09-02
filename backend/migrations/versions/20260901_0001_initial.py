"""Create Phase 1 configuration and session tables.

Revision ID: 20260901_0001
Revises:
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_code", sa.String(length=64), nullable=True),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        # SQLite stores all persisted timestamps as UTC-naive values. Application
        # defaults use the same convention for reliable round-trip comparisons.
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_project_code", "projects", ["project_code"], unique=False)
    op.create_index("ix_projects_project_name", "projects", ["project_name"], unique=False)

    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "sessions",
        sa.Column("session_id_hash", sa.String(length=64), nullable=False),
        sa.Column("dingtalk_user_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("departments_json", sa.Text(), nullable=False),
        sa.Column("current_department_id", sa.String(length=128), nullable=True),
        sa.Column("current_department_name", sa.String(length=255), nullable=True),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        # SQLite stores session timestamps as UTC-naive values. Application code
        # uses the same convention so expiry comparisons never mix aware/naive.
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("session_id_hash"),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("settings")
    op.drop_index("ix_projects_project_name", table_name="projects")
    op.drop_index("ix_projects_project_code", table_name="projects")
    op.drop_table("projects")
