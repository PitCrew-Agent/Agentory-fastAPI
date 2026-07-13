"""통일 응답 래퍼 ApiResponse (INFRA_AOP01)

성공·실패 응답을 {success, code, message, result} 단일 구조로 통일
성공은 엔드포인트가 ApiResponse.ok(result) 반환, 실패는 전역 예외 핸들러가 생성
HTTP status는 실제값 유지, SSE 스트림·리다이렉트는 래핑 제외
"""

from pydantic import BaseModel, Field

from agentory.common.context import get_locale
from agentory.common.i18n import translate

# 성공 응답 공통 코드 (mediforme 컨벤션 통일)
SUCCESS_CODE = "COMMON200"


class ApiResponse[T](BaseModel):
    # 전 엔드포인트 공통 응답 래퍼, 필드 순서는 success·code·message·result 고정
    success: bool = Field(description="성공 여부", examples=[True])
    code: str = Field(description="응답 코드", examples=[SUCCESS_CODE])
    message: str = Field(description="응답 메시지 (요청 로케일)", examples=["성공입니다"])
    result: T | None = Field(default=None, description="성공 시 payload, 실패 시 null")

    @classmethod
    def ok(cls, result: T | None = None) -> "ApiResponse[T]":
        # 성공 응답, 메시지는 요청 로케일로 번역
        return cls(
            success=True,
            code=SUCCESS_CODE,
            message=translate("success.ok", get_locale()),
            result=result,
        )
