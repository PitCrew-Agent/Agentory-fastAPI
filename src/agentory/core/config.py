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

    # LLM (OpenAI), 역할별 모델 선택: 비우면 llm_model 사용
    openai_api_key: str = ""
    llm_model: str = "gpt-5-mini"  # 기본(워커) 모델
    llm_router_model: str = ""  # Supervisor 라우팅용 모델
    llm_finalizer_model: str = ""  # 최종 답변 합성용 고성능 모델 (3단계)
    # 라우팅 reasoning 강도, 짧은 구조화 판단이라 최소화로 지연 단축
    llm_router_reasoning_effort: str = "minimal"

    # Agent
    agent_max_steps: int = 10  # 전역 반복 예산 (AI_AGENT03_FALLBACK01)
    agent_grounding_enabled: bool = True  # Grounding 자가 검증 on/off (NEW_TRUST02)
    agent_suggestions_enabled: bool = True  # 후속 추천 질문 생성 on/off (BE_CHAT02_SUGGEST01)
    # 도구 관찰값 프롬프트 재주입 상한, 초과분은 잘라 컨텍스트 초과 방지 (AI_AGENT03_FALLBACK01)
    agent_tool_observation_max_chars: int = 40000

    # 임베딩
    embedding_model: str = ""
    embedding_dim: int = 1536

    # RAG 매뉴얼 검색 (BE_MCP04_RAG01)
    rag_search_top_k: int = 3  # 검색 기본 Top-K
    rag_search_min_score: float = 0.2  # 유사도 임계값, 미달 결과 제외로 환각 방지

    # MCP 서버
    mcp_realtime_url: str = "http://localhost:8101/mcp"
    mcp_knowledge_url: str = "http://localhost:8102/mcp"
    # 센서 로그 단일 조회 최대 행수, 초과 시 최근 행 우선 반환 (BE_MCP02_TELEMETRY01)
    sensor_log_max_rows: int = 500

    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "agentory"
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    audit_log_db_enabled: bool = True
    audit_log_redis_enabled: bool = False
    audit_log_redis_ttl_seconds: int = 60 * 60 * 24 * 30
    audit_log_redis_max_stream_length: int = 10000

    # 인증 / OIDC
    oidc_provider: str = "oidc"
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/v1/auth/callback"
    oidc_post_logout_redirect_uri: str = "http://localhost:8000/docs"
    # 콜백 성공/실패 후 브라우저가 복귀할 프론트 URL (id_token은 fragment로 전달)
    frontend_redirect_uri: str = "http://localhost:5173/dashboard"
    # 프론트 SPA의 API 호출 허용 오리진, 콤마 구분 다중 지정
    cors_allow_origins: str = "http://localhost:5173"
    oidc_scopes: str = "openid profile email offline_access"
    oidc_password_reset_url: str = ""
    azure_ad_tenant_id: str = ""
    azure_ad_client_id: str = ""
    azure_ad_client_secret: str = ""
    auth_state_ttl_seconds: int = 300
    auth_auto_provision_enabled: bool = False
    auth_default_role: str = "field_engineer"
    auth_session_cookie_name: str = "agentory_session"
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 14
    auth_cookie_samesite: str = "lax"
    auth_cookie_secure: bool = False
    auth_cookie_domain: str = ""


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Azure AD 편의: AZURE_AD_* 만 채워도 동작하도록 OIDC_* 로 유도 (BE_AUTH01_OAUTH01)
    if not settings.oidc_issuer_url and settings.azure_ad_tenant_id:
        settings.oidc_issuer_url = (
            f"https://login.microsoftonline.com/{settings.azure_ad_tenant_id}/v2.0"
        )
    if not settings.oidc_client_id:
        settings.oidc_client_id = settings.azure_ad_client_id
    if not settings.oidc_client_secret:
        settings.oidc_client_secret = settings.azure_ad_client_secret
    return settings
