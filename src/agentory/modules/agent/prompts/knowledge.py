"""Knowledge 워커 프롬프트 로더 (AI_AGENT01_PROMPT01)

프롬프트 본문·버전은 같은 폴더 knowledge.yaml이 단일 소스
검색 전략·인용 규칙 수정은 YAML에서 진행하고 version·changelog 갱신
"""

from pathlib import Path

import yaml

_PROMPT_FILE = Path(__file__).with_name("knowledge.yaml")
_DATA = yaml.safe_load(_PROMPT_FILE.read_text(encoding="utf-8"))

KNOWLEDGE_PROMPT: str = _DATA["prompt"]
# 평가 리포트에 프롬프트 버전 기록용 (AI_RAG02_EVAL01)
KNOWLEDGE_PROMPT_VERSION: int = _DATA["version"]
