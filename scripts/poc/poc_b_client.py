"""POC-B: MCP realtime streamable-http 연결·tool 호출·SSE 계약 검증 (검증 후 폐기)

성공 기준: 서버 기동·세션 연결 + tool 1회 호출 결과 수신 + 결과를 events.py 계약 이벤트로 표현 가능
poc_b_server.py를 서브프로세스로 띄우고 streamable-http로 붙어 ping tool 호출
실행: uv run python scripts/poc/poc_b_client.py
"""

import asyncio
import os
import subprocess
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from agentory.common.events import ActionEvent, ObservationEvent, sse_event_adapter

URL = "http://127.0.0.1:8199/mcp"
SERVER = os.path.join(os.path.dirname(__file__), "poc_b_server.py")


async def wait_ready() -> None:
    # 서버 기동 대기, 연결될 때까지 짧게 재시도
    for _ in range(30):
        try:
            async with streamablehttp_client(URL) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError("서버 기동 대기 실패")


async def main() -> None:
    proc = subprocess.Popen([sys.executable, SERVER])
    try:
        print("[POC-B] 최소 mcp-realtime 서버 기동 대기...")
        await wait_ready()

        async with streamablehttp_client(URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                print(f"  연결 성공, 발견 tool: {names}")

                # tool 1회 호출 (streamable-http 왕복)
                result = await session.call_tool("ping", {"name": "line-A"})
                payload = result.structuredContent or {"text": result.content[0].text}
                print(f"  tool 호출 결과: {payload}")

                # 수신 결과를 events.py 계약 이벤트로 표현·검증
                action = ActionEvent(step=1, agent="data_analysis", tool="ping",
                                     tool_input={"name": "line-A"}, reason="PoC 왕복")
                obs = ObservationEvent(step=1, agent="data_analysis", tool="ping", content=payload)
                # discriminated union 어댑터로 역직렬화 성공 = 계약 일치
                for ev in (action, obs):
                    sse_event_adapter.validate_python(ev.model_dump())
                print("  events.py 계약 검증: action·observation 이벤트 직렬화·역직렬화 OK")
                print("\n성공: streamable-http 왕복 + SSE 계약 표현 확인")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
