"""LLM 프로바이더 포트, 교체 가능 지점 ① (비기능: 확장성)

에이전트 코드는 이 인터페이스에만 의존
구체 프로바이더(Anthropic 등)는 현재 패키지에 어댑터 파일로 추가, 교체 시 에이전트 코드 수정 불필요
"""

from typing import Any, Protocol


class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        """메시지 목록으로 LLM 호출 후 응답(텍스트 또는 tool_call) 반환"""
        ...


def get_llm_client() -> LLMClient:
    """설정(LLM_MODEL)에 따라 프로바이더 어댑터 반환

    TODO(주희정): Anthropic 어댑터 구현 후 연결
    """
    raise NotImplementedError
