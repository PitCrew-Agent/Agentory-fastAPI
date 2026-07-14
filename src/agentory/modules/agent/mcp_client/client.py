"""MCP 클라이언트, 서버별 도구를 LangChain 도구로 로드 (BE_MCP01_SERVER01 연동)

langchain-mcp-adapters로 realtime·knowledge 서버의 도구 목록을 발견·변환
워커 레지스트리의 WorkerSpec.server 키와 서버명이 매핑됨
"""

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from agentory.core.config import get_settings

SERVER_NAMES = ("realtime", "knowledge", "maintenance")


def _build_client() -> MultiServerMCPClient:
    # 설정의 서버 URL로 MCP 멀티서버 클라이언트 구성
    settings = get_settings()
    return MultiServerMCPClient(
        {
            "realtime": {"url": settings.mcp_realtime_url, "transport": "streamable_http"},
            "knowledge": {"url": settings.mcp_knowledge_url, "transport": "streamable_http"},
            "maintenance": {"url": settings.mcp_maintenance_url, "transport": "streamable_http"},
        }
    )


async def load_tools_by_server() -> dict[str, list[BaseTool]]:
    # 서버별 도구 목록 로드, 서버 미기동 시 해당 서버만 빈 목록으로 격리
    client = _build_client()
    tools: dict[str, list[BaseTool]] = {}
    for name in SERVER_NAMES:
        try:
            tools[name] = await client.get_tools(server_name=name)
        except Exception:
            tools[name] = []
    return tools


async def load_tools_for_server(name: str) -> list[BaseTool]:
    # 단일 MCP 서버 도구 로드 (NEW_INCIDENT01_PLAN01)
    if name not in SERVER_NAMES:
        raise ValueError(f"지원하지 않는 MCP 서버: {name}")
    client = _build_client()
    try:
        return await client.get_tools(server_name=name)
    except Exception:
        return []
