"""DB_PASSWORD 주입 시 DATABASE_URL 비밀번호 교체 단위 테스트 (DEV_DATABASE)

RDS 관리형 마스터 비번 로테이션 후 재배포만으로 새 비번이 반영되는지 검증
"""

from urllib.parse import urlsplit

from agentory.core.config import _with_password

BASE_URL = (
    "postgresql+asyncpg://agentory:old-pw@agentory-postgres-dev.rds.amazonaws.com:5432/agentory"
)


def test_replaces_password_only():
    parts = urlsplit(_with_password(BASE_URL, "new-pw"))
    assert parts.password == "new-pw"
    assert parts.username == "agentory"
    assert parts.hostname == "agentory-postgres-dev.rds.amazonaws.com"
    assert parts.port == 5432
    assert parts.path == "/agentory"
    assert parts.scheme == "postgresql+asyncpg"


def test_encodes_special_characters():
    # RDS 자동 생성 비번에 포함되는 예약문자가 URL 파싱을 깨지 않아야 함
    parts = urlsplit(_with_password(BASE_URL, "p@ss:w/rd#1"))
    assert parts.password == "p%40ss%3Aw%2Frd%231"
    assert parts.hostname == "agentory-postgres-dev.rds.amazonaws.com"
    assert parts.path == "/agentory"


def test_keeps_url_without_credentials():
    # 유저 정보가 없는 URL(로컬 소켓 등)은 그대로 유지
    url = "postgresql+asyncpg:///agentory"
    assert _with_password(url, "new-pw") == url
