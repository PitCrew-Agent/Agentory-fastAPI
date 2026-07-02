"""MCP 클라이언트, 에이전트가 MCP 서버 도구를 로드·호출하는 진입점

연결 대상 (core.config 참조):
  - MCP_REALTIME_URL  (mcp-realtime 서버, streamable-http)
  - MCP_KNOWLEDGE_URL (mcp-knowledge 서버, streamable-http)
"""


async def load_tools() -> list:
    """두 MCP 서버 접속 후 사용 가능 도구 목록 로드

    TODO(주희정): mcp SDK streamablehttp_client + ClientSession으로
    list_tools/call_tool 래핑 후 LangGraph 도구 형태로 변환
    """
    raise NotImplementedError
