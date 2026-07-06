"""Auth database models for Azure AD SSO and role-based access control."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentory.core.db import Base, TimestampMixin


class User(TimestampMixin, Base):
    """Internal user account created from Azure AD SSO."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sso_accounts: Mapped[list["SsoAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    admin: Mapped["Admin | None"] = relationship(back_populates="user", cascade="all, delete-orphan")
    field_engineer: Mapped["FieldEngineer | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")


class SsoAccount(TimestampMixin, Base):
    """External SSO identity mapped to an internal user."""

    __tablename__ = "sso_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_sso_provider_user"),
        Index("ix_sso_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[str] = mapped_column(String(255), nullable=False)

    user: Mapped[User] = relationship(back_populates="sso_accounts")


class Admin(TimestampMixin, Base):
    """Administrator profile separated from field engineer users."""

    __tablename__ = "admins"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)

    user: Mapped[User] = relationship(back_populates="admin")


class FieldEngineer(TimestampMixin, Base):
    """Field engineer profile separated from administrator users."""

    __tablename__ = "field_engineers"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)

    user: Mapped[User] = relationship(back_populates="field_engineer")


class AuditLog(TimestampMixin, Base):
    """Request and auth event log written by middleware/service code."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_user_created_at", "user_id", "created_at"),
        Index("ix_audit_logs_action_created_at", "action", "created_at"),
        Index("ix_audit_logs_success_created_at", "success", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str | None] = mapped_column(String(10))
    path: Mapped[str | None] = mapped_column(String(500))
    status_code: Mapped[int | None]
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User | None] = relationship(back_populates="audit_logs")
