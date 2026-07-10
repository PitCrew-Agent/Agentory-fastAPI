from pydantic import BaseModel, Field

from agentory.modules.admin.schemas import LineRef


class AuthUrlResponse(BaseModel):
    authorization_url: str = Field(
        description="프론트가 리다이렉트할 IdP 인가 URL",
        examples=["https://login.microsoftonline.com/.../authorize?..."],
    )
    state: str | None = Field(default=None, description="CSRF 방지 state 값")
    flow: str = Field(description="플로우 종류 (login·signup·password_reset)", examples=["login"])
    provider: str = Field(description="IdP 식별자", examples=["azure-ad"])


class AuthUserResponse(BaseModel):
    id: int = Field(description="유저 id", examples=[7])
    email: str = Field(description="이메일", examples=["hong@example.com"])
    name: str = Field(description="이름", examples=["홍길동"])
    role: str = Field(description="권한 (admin·field_engineer)", examples=["field_engineer"])
    status: str = Field(description="계정 상태", examples=["active"])
    # 담당 라인, 부서 대신 사용
    lines: list[LineRef] = Field(default_factory=list, description="담당 라인 목록 (부서 대체)")


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
