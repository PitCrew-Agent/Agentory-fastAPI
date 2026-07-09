"""컨텍스트 장부: Observation에서 핵심 엔티티 추출·누적 (AI_AGENT02_CHAIN01)

도구 결과에서 설비 ID·알람 코드를 추출해 상태 entities에 유지하고
다음 워커의 시스템 프롬프트에 "현재 파악된 컨텍스트" 블록으로 주입
"""

import re
from typing import Any

# 도메인 식별자 패턴 (요구사항 정의서 §8 샘플 기준)
# 알람 코드는 급성(ERR-\d{3})과 드리프트/PM·SPC 확장(WRN-\d{3}) 모두 인식 (시뮬레이터 참고서 §10)
# 설비 ID는 베이 형식(EQP-A01)과 숫자 형식(EQP-002) 모두 인식 (시뮬레이터 scenarios 기준)
EQUIPMENT_PATTERN = re.compile(r"EQP-[A-Z0-9]{3}")
ALARM_PATTERN = re.compile(r"(?:ERR|WRN)-\d{3}")


def extract_entities(text: str) -> dict[str, Any]:
    # 텍스트에서 설비 ID·알람 코드 추출, 중복 제거 후 정렬
    found: dict[str, Any] = {}
    if equipment := sorted(set(EQUIPMENT_PATTERN.findall(text))):
        found["equipment_ids"] = equipment
    if alarms := sorted(set(ALARM_PATTERN.findall(text))):
        found["alarm_codes"] = alarms
    return found


def merge_entities(current: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    # 기존 장부에 신규 추출분 병합, 리스트 값은 합집합 유지
    merged = dict(current)
    for key, value in new.items():
        if isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = sorted(set(merged[key]) | set(value))
        else:
            merged[key] = value
    return merged


def format_entities(entities: dict[str, Any]) -> str:
    # 워커 시스템 프롬프트에 주입할 컨텍스트 블록 생성
    if not entities:
        return "아직 파악된 컨텍스트 없음"
    lines = [f"- {key}: {value}" for key, value in entities.items()]
    return "\n".join(lines)
