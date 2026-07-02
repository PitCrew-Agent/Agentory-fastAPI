"""환경 변수 기반 설정 (DEV_CONFIG)

.env 파일에서 로드, 비밀정보 코드 하드코딩 금지 (요구사항 §11)
새 설정 추가 시 .env.example에도 동일 항목 추가
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"  # local | dev | prod
    log_level: str = "INFO"

    # Database (PostgreSQL + pgvector)
    database_url: str = "postgresql+asyncpg://agentory:agentory@localhost:5432/agentory"

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5"

    # 임베딩
    embedding_model: str = ""
    embedding_dim: int = 1536

    # MCP 서버
    mcp_realtime_url: str = "http://localhost:8101/mcp"
    mcp_knowledge_url: str = "http://localhost:8102/mcp"

    # 인증 / OIDC
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
