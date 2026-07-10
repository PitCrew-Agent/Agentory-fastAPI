from pydantic import BaseModel, Field

from agentory.modules.admin.schemas import LineRef


class AuthUrlResponse(BaseModel):
    authorization_url: str
    state: str | None = None
    flow: str
    provider: str


class AuthUserResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str
    status: str
    lines: list[LineRef] = Field(default_factory=list)  # 담당 라인, 부서 대신 사용


class AuthTokenResponse(BaseModel):
    token_type: str
    access_token: str
    expires_in: int | None = None
    id_token: str | None = None
    refresh_token: str | None = None
    access_token_expires_at: int | None = None
    id_token_expires_at: int | None = None
    refresh_token_cached: bool = False
    refresh_token_handle: str | None = None
    user: AuthUserResponse


class RefreshTokenRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=1)
    refresh_token_handle: str | None = Field(default=None, min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None
    refresh_token_handle: str | None = None


class LogoutResponse(BaseModel):
    logout_url: str | None = None
    refresh_token_revoked: bool = False


class MessageResponse(BaseModel):
    message: str
