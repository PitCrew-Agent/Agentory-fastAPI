"""Supervisor + ReAct 공통 루프 (AI_AGENT01_REACT01)

구조:
    Supervisor가 질의 분석 후 워커(data_analysis / knowledge / rediagnosis)에 위임,
    각 워커는 MCP 도구를 ReAct 루프로 호출
    최대 반복 제한·폴백은 AI_AGENT03_FALLBACK01 참고
"""

MAX_STEPS = 10  # 무한 루프 방지 기본값 (AI_AGENT03_FALLBACK01)


def build_supervisor_graph():
    """LangGraph Supervisor 그래프 조립 후 반환

    TODO(주희정):
      1. MCP 클라이언트에서 도구 로드 (agent.mcp_client)
      2. 워커 서브그래프 조립 (agent.workers.*)
      3. Supervisor 라우팅 노드 + 이벤트 스트리밍(SSEEvent 변환) 연결
    """
    raise NotImplementedError("AI_AGENT01_REACT01 미구현")
