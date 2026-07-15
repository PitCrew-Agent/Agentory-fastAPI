"""LLM 챗 모델 팩토리, 역할별 모델 선택 지원 (설계 문서 §4.7)

에이전트 역할마다 필요한 성능이 달라 모델을 분리 선택
  router: 짧은 구조화 판단, 매 턴 호출되어 경량·저지연 모델 적합
  worker: 도구 인자 구성·데이터 해석, 균형형 모델
  finalizer: 최종 답변 합성, 고성능 모델 (3단계에서 사용)
환경 변수 미설정 역할은 LLM_MODEL로 폴백
"""

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from agentory.core.config import get_settings


def get_chat_model(role: str = "worker") -> BaseChatModel:
    # role: worker | router | planner | finalizer
    settings = get_settings()
    by_role = {
        "router": settings.llm_router_model,
        "finalizer": settings.llm_finalizer_model,
    }
    model = by_role.get(role) or settings.llm_model
    kwargs: dict = {"model": model, "api_key": settings.openai_api_key}
    # 역할별 reasoning 강도, 추론 토큰 지연을 줄여 응답 속도 단축 (gpt-5 계열)
    effort = {
        "router": settings.llm_router_reasoning_effort,
        "worker": settings.llm_worker_reasoning_effort,
        "planner": settings.llm_planner_reasoning_effort,
        "finalizer": settings.llm_finalizer_reasoning_effort,
    }.get(role)
    if effort:
        kwargs["reasoning_effort"] = effort
    # 최종 답변만 토큰 상한 적용(0=무제한), 장황한 답변 backstop
    if role == "finalizer" and settings.llm_finalizer_max_tokens:
        kwargs["max_tokens"] = settings.llm_finalizer_max_tokens
    return ChatOpenAI(**kwargs)
