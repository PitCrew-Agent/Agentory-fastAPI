"""워커 레지스트리 (설계 문서 §4.1)

워커 추가는 WorkerSpec 등록만으로 완료, ReAct 로직은 base.py 팩토리가 담당
server 키는 mcp_client.SERVER_NAMES와 매핑
"""

from dataclasses import dataclass

from agentory.modules.agent.prompts.data_analysis import DATA_ANALYSIS_PROMPT
from agentory.modules.agent.prompts.knowledge import KNOWLEDGE_PROMPT


@dataclass(frozen=True)
class WorkerSpec:
    server: str  # 사용할 MCP 서버 (realtime | knowledge)
    prompt: str  # 워커 시스템 프롬프트


WORKERS: dict[str, WorkerSpec] = {
    "data_analysis": WorkerSpec(server="realtime", prompt=DATA_ANALYSIS_PROMPT),
    "knowledge": WorkerSpec(server="knowledge", prompt=KNOWLEDGE_PROMPT),
}
