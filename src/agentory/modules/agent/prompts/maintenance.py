"""Maintenance 워커 프롬프트 로더 (AI_AGENT01_PROMPT01)

프롬프트 본문·버전은 같은 폴더 maintenance.yaml이 단일 소스
조회 전략·보고 규칙 수정은 YAML에서 진행하고 version·changelog 갱신
"""

from pathlib import Path

import yaml

_PROMPT_FILE = Path(__file__).with_name("maintenance.yaml")
_DATA = yaml.safe_load(_PROMPT_FILE.read_text(encoding="utf-8"))

MAINTENANCE_PROMPT: str = _DATA["prompt"]
MAINTENANCE_PROMPT_VERSION: int = _DATA["version"]
