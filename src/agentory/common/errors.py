"""통일 에러 응답 스키마 (INFRA_AOP01)

전역 예외 핸들러가 모든 도메인 예외를 이 형태로 직렬화, 프론트는 code로 분기
message는 요청 로케일(Accept-Language)로 번역된 사용자 표시 문자열
"""

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str = Field(description="에러 코드 (프론트 분기용)", examples=["NOT_FOUND"])
    message: str = Field(
        description="사용자 표시 메시지 (요청 로케일)", examples=["작업 로그 없음: 15"]
    )
