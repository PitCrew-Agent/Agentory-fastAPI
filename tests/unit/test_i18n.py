"""i18n 카탈로그·로케일 해석 단위 테스트 (INFRA_I18N01)"""

from agentory.common.i18n import resolve_locale, translate


def test_translate_ko_and_en():
    assert translate("error.work_log.not_found", "ko", id=15) == "작업 로그 없음: 15"
    assert translate("error.work_log.not_found", "en", id=15) == "Work log not found: 15"


def test_translate_unknown_code_falls_back_to_code():
    # 미등록 코드는 코드 문자열 그대로(외부 오류 리터럴 통과용)
    assert translate("some raw external error", "en") == "some raw external error"


def test_translate_unknown_locale_uses_default_ko():
    assert translate("error.work_log.update_forbidden", "fr") == "본인 작업 로그만 수정 가능"


def test_translate_missing_param_keeps_template():
    # 보간 키 불일치 시 원문 유지(메시지 유실 방지)
    assert "{id}" in translate("error.work_log.not_found", "en")


def test_resolve_locale_variants():
    assert resolve_locale("en-US,en;q=0.9,ko;q=0.8") == "en"
    assert resolve_locale("ko-KR,ko;q=0.9") == "ko"
    assert resolve_locale("fr-FR") == "ko"  # 미지원은 기본 ko
    assert resolve_locale(None) == "ko"
    assert resolve_locale("") == "ko"
