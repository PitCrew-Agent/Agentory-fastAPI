"""테스트 하네스 공통 골격 (TEST_HARNESS)

레이어별 테스트 구현은 각 기능 담당자 작성

디렉토리 규약:
  unit/         모듈 단위 테스트 (MCP 도구 입출력·빈 결과 검증 포함, 1주차)
  integration/  DB·MCP 연동 테스트 (docker-compose DB 필요)
  contracts/    SSE 이벤트 스키마 계약 테스트
  e2e/          골든 시나리오 E2E (시나리오 주입→질의→응답 검증, 3주차)
  golden/       골든 질의 셋 (YAML), 포맷은 golden/README.md 참고
"""

from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from agentory.main import create_app

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.fixture
async def client():
    """FastAPI 앱 인메모리 HTTP 클라이언트 (서버 기동 불필요)"""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def load_golden_cases() -> list[dict]:
    """골든 질의 셋 YAML 전체 로드, e2e 테스트 parametrize용"""
    cases = []
    for path in sorted(GOLDEN_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
            cases.extend(data if isinstance(data, list) else [data])
    return cases
