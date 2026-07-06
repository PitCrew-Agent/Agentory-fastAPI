"""?섍꼍 蹂??湲곕컲 ?ㅼ젙 (DEV_CONFIG)

.env ?뚯씪?먯꽌 濡쒕뱶, 鍮꾨??뺣낫 肄붾뱶 ?섎뱶肄붾뵫 湲덉? (?붽뎄?ы빆 짠11)
???ㅼ젙 異붽? ??.env.example?먮룄 ?숈씪 ??ぉ 異붽?
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"  # local | dev | prod
    log_level: str = "INFO"

    # Database (PostgreSQL + pgvector)
    database_url: str = "postgresql+asyncpg://agentory:agentory@localhost:5432/agentory"

    # Redis cache
    redis_url: str = "redis://localhost:6379/0"

    # LLM (OpenAI), 역할별 모델 선택: 비우면 llm_model 사용
    openai_api_key: str = ""
    llm_model: str = "gpt-5-mini"  # 기본(워커) 모델
    llm_router_model: str = ""  # Supervisor 라우팅용 경량 모델
    llm_finalizer_model: str = ""  # 최종 답변 합성용 고성능 모델 (3단계)

    # Agent
    agent_max_steps: int = 10  # 전역 반복 예산 (AI_AGENT03_FALLBACK01)
    agent_grounding_enabled: bool = True  # Grounding 자가 검증 on/off (NEW_TRUST02)

    # ?꾨쿋??
    embedding_model: str = ""
    embedding_dim: int = 1536

    # MCP ?쒕쾭
    mcp_realtime_url: str = "http://localhost:8101/mcp"
    mcp_knowledge_url: str = "http://localhost:8102/mcp"

    # ?몄쬆 / OIDC
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    # Azure AD / Microsoft Entra ID SSO
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    azure_redirect_uri: str = ""
    azure_authority: str = ""
    admin_emails: str = ""
    password_reset_help_url: str = "https://passwordreset.microsoftonline.com/"

    # JWT
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14


@lru_cache
def get_settings() -> Settings:
    return Settings()
