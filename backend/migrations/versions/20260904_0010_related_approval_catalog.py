"""Persist travel-profile catalogs and verified related-approval snapshots.

Revision ID: 20260904_0010
Revises: 20260904_0009
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0010"
down_revision: str | None = "20260904_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "oa_template_profiles",
        sa.Column(
            "travel_profiles_json",
            sa.Text(),
            server_default="[]",
            nullable=False,
        ),
    )

    op.create_table(
        "reimbursement_draft_related_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("corp_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("process_instance_id", sa.String(length=128), nullable=False),
        sa.Column("travel_profile_key", sa.String(length=64), nullable=False),
        sa.Column("process_code", sa.String(length=128), nullable=False),
        sa.Column("catalog_config_version", sa.Integer(), nullable=False),
        sa.Column("travel_schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("listed_from_ms", sa.BigInteger(), nullable=False),
        sa.Column("listed_to_ms", sa.BigInteger(), nullable=False),
        sa.Column("travel_start_date", sa.Date(), nullable=False),
        sa.Column("travel_end_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("business_id", sa.String(length=128), nullable=False),
        sa.Column("instance_created_at", sa.DateTime(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_reimbursement_draft_related_approvals_sort_order",
        ),
        sa.CheckConstraint(
            "catalog_config_version > 0",
            name="ck_reimbursement_draft_related_approvals_catalog_version_positive",
        ),
        sa.CheckConstraint(
            "length(travel_schema_fingerprint) = 64",
            name="ck_reimbursement_draft_related_approvals_fingerprint_length",
        ),
        sa.CheckConstraint(
            "listed_from_ms >= 0 AND listed_to_ms >= listed_from_ms",
            name="ck_reimbursement_draft_related_approvals_listing_window",
        ),
        sa.CheckConstraint(
            "travel_end_date >= travel_start_date",
            name="ck_reimbursement_draft_related_approvals_travel_dates",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id", "corp_id", "owner_user_id"],
            [
                "reimbursement_drafts.id",
                "reimbursement_drafts.corp_id",
                "reimbursement_drafts.owner_user_id",
            ],
            ondelete="CASCADE",
            name="fk_reimbursement_draft_related_approvals_draft_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_id",
            "process_instance_id",
            name="uq_reimbursement_draft_related_approvals_draft_instance",
        ),
        sa.UniqueConstraint(
            "draft_id",
            "sort_order",
            name="uq_reimbursement_draft_related_approvals_draft_sort_order",
        ),
    )
    op.create_index(
        "ix_reimbursement_draft_related_approvals_owner_instance",
        "reimbursement_draft_related_approvals",
        ["corp_id", "owner_user_id", "process_instance_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reimbursement_draft_related_approvals_owner_instance",
        table_name="reimbursement_draft_related_approvals",
    )
    op.drop_table("reimbursement_draft_related_approvals")
    with op.batch_alter_table("oa_template_profiles") as batch_op:
        batch_op.drop_column("travel_profiles_json")
