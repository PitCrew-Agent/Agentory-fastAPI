"""Request and response schemas for authentication APIs."""

from pydantic import BaseModel, Field, field_validator


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., examples=["refresh-token-value"])


class PasswordHelpRequest(BaseModel):
    email: str = Field(..., examples=["engineer@example.com"])

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("Invalid email")
        return normalized


class PasswordHelpResponse(BaseModel):
    message: str
    reset_url: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    status: str
    user_type: str


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., examples=["refresh-token-value"])


class AuditLogCreate(BaseModel):
    user_id: int | None = None
    action: str
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    success: bool
    error_message: str | None = None
