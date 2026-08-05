"""메시지 국제화 카탈로그 (INFRA_I18N01)

메시지 코드 -> 로케일별 문자열, translate()가 params 보간해 반환
지원 로케일은 ko·en, 미등록 코드·로케일은 안전 폴백(코드 문자열 그대로 통과)
Accept-Language 헤더 파싱으로 요청 로케일 결정
새 사용자 대면 메시지는 리터럴 대신 여기 코드로 추가
"""

from agentory.common.context import DEFAULT_LOCALE, get_locale

SUPPORTED_LOCALES = ("ko", "en")

# 메시지 코드 -> {로케일: 템플릿}, 템플릿은 str.format 보간 사용
MESSAGES: dict[str, dict[str, str]] = {
    # 성공
    "success.ok": {
        "ko": "성공입니다",
        "en": "OK",
    },
    # 공통·검증
    "error.validation": {
        "ko": "요청 값이 올바르지 않습니다",
        "en": "Invalid request",
    },
    "error.internal": {
        "ko": "서버 오류가 발생했습니다",
        "en": "Internal server error",
    },
    "error.forbidden": {
        "ko": "권한이 없습니다",
        "en": "Forbidden",
    },
    "error.cursor.invalid": {
        "ko": "잘못된 커서: {cursor}",
        "en": "Invalid cursor: {cursor}",
    },
    # 설비·라인·유저·알림·대화 (전 모듈 롤아웃)
    "error.equipment.not_found": {
        "ko": "설비 없음: {id}",
        "en": "Equipment not found: {id}",
    },
    "error.line.not_found": {
        "ko": "라인 없음: {id}",
        "en": "Line not found: {id}",
    },
    "error.line.code_conflict": {
        "ko": "이미 존재하는 라인 코드: {code}",
        "en": "Line code already exists: {code}",
    },
    "error.line.unknown": {
        "ko": "존재하지 않는 라인: {ids}",
        "en": "Unknown line: {ids}",
    },
    "error.user.not_found": {
        "ko": "유저 없음: {id}",
        "en": "User not found: {id}",
    },
    "error.user.unknown": {
        "ko": "존재하지 않는 유저: {id}",
        "en": "Unknown user: {id}",
    },
    "error.notification.not_found": {
        "ko": "알림 없음: {id}",
        "en": "Notification not found: {id}",
    },
    "error.notification.invalid_range": {
        "ko": "조회 시작 시각이 종료 시각보다 앞서야 함",
        "en": "Range start must be earlier than end",
    },
    "error.chat_session.not_found": {
        "ko": "대화 세션 없음: {id}",
        "en": "Chat session not found: {id}",
    },
    # 작업 로그 (Phase 1 파일럿)
    "error.work_log.not_found": {
        "ko": "작업 로그 없음: {id}",
        "en": "Work log not found: {id}",
    },
    "error.work_log.update_forbidden": {
        "ko": "본인 작업 로그만 수정 가능",
        "en": "You can only edit your own work log",
    },
    "error.work_log.delete_forbidden": {
        "ko": "본인 작업 로그만 삭제 가능",
        "en": "You can only delete your own work log",
    },
}


def translate(code: str, locale: str | None = None, **params: object) -> str:
    # 코드 -> 요청 로케일 메시지, 미등록 코드는 코드 문자열 폴백(외부 오류 리터럴 등 통과)
    # locale 미지정 시 현재 요청 컨텍스트 로케일 사용
    loc = locale or get_locale()
    if loc not in SUPPORTED_LOCALES:
        loc = DEFAULT_LOCALE
    entry = MESSAGES.get(code)
    if entry is None:
        template = code
    else:
        template = entry.get(loc) or entry.get(DEFAULT_LOCALE) or code
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        # 보간 키 불일치 시 원문 유지 (메시지 유실 방지)
        return template


def resolve_locale(accept_language: str | None) -> str:
    # Accept-Language에서 지원 로케일 best match, 없으면 기본 (q 가중치는 순서로 근사)
    if not accept_language:
        return DEFAULT_LOCALE
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip().lower()
        primary = tag.split("-")[0]
        if primary in SUPPORTED_LOCALES:
            return primary
    return DEFAULT_LOCALE
