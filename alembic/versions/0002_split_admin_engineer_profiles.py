"""Split admin and field engineer profiles.

Revision ID: 0002_split_profiles
Revises: 0001_auth_azure_sso
Create Date: 2026-07-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_split_profiles"
down_revision: Union[str, None] = "0001_auth_azure_sso"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "field_engineers",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.execute(
        """
        insert into admins (user_id, display_name)
        select distinct u.id, u.name
        from users u
        join user_roles ur on ur.user_id = u.id
        join roles r on r.id = ur.role_id
        where r.code = 'ADMIN'
        on conflict (user_id) do nothing
        """
    )
    op.execute(
        """
        insert into field_engineers (user_id, display_name)
        select distinct u.id, u.name
        from users u
        join user_roles ur on ur.user_id = u.id
        join roles r on r.id = ur.role_id
        where r.code = 'FIELD_ENGINEER'
          and not exists (
              select 1
              from admins a
              where a.user_id = u.id
          )
        on conflict (user_id) do nothing
        """
    )

    op.drop_table("user_roles")
    op.drop_table("roles")


def downgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    roles_table = sa.table(
        "roles",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
    )
    op.bulk_insert(
        roles_table,
        [
            {"code": "ADMIN", "name": "Administrator"},
            {"code": "FIELD_ENGINEER", "name": "Field Engineer"},
        ],
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )
    op.execute(
        """
        insert into user_roles (user_id, role_id)
        select a.user_id, r.id
        from admins a
        join roles r on r.code = 'ADMIN'
        """
    )
    op.execute(
        """
        insert into user_roles (user_id, role_id)
        select f.user_id, r.id
        from field_engineers f
        join roles r on r.code = 'FIELD_ENGINEER'
        """
    )
    op.drop_table("field_engineers")
    op.drop_table("admins")
