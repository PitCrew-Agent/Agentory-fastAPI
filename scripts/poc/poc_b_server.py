"""POC-B 보조: 고정값 tool 1개짜리 최소 mcp-realtime 서버 (검증 후 폐기)

DB 의존 없는 최소 tool로 streamable-http 트랜스포트만 검증
poc_b_client.py가 서브프로세스로 기동
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("poc-realtime", host="127.0.0.1", port=8199)


@mcp.tool()
async def ping(name: str) -> dict:
    """고정값 반환 tool, 트랜스포트 왕복 검증용"""
    return {"pong": name, "source": "poc-realtime"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
