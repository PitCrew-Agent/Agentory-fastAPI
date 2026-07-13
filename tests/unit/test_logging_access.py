"""요청 컨텍스트 로깅·액세스 미들웨어 테스트 (INFRA_AOP01)"""

import logging

from agentory.common.context import set_request_id
from agentory.core.logging import RequestIdFilter


def test_request_id_filter_injects_context_value():
    set_request_id("trace-xyz")
    record = logging.LogRecord("n", logging.INFO, __file__, 1, "msg", None, None)
    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "trace-xyz"


def test_request_id_filter_defaults_to_dash():
    set_request_id(None)
    record = logging.LogRecord("n", logging.INFO, __file__, 1, "msg", None, None)
    RequestIdFilter().filter(record)
    assert record.request_id == "-"


async def test_response_echoes_provided_request_id(client):
    r = await client.get("/health", headers={"X-Request-ID": "trace-abc"})
    assert r.status_code == 200
    assert r.headers["x-request-id"] == "trace-abc"


async def test_response_generates_request_id_when_absent(client):
    r = await client.get("/health")
    assert r.status_code == 200
    rid = r.headers.get("x-request-id")
    assert rid and len(rid) == 32  # uuid4 hex


async def test_access_log_emitted_with_request_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="access"):
        await client.get("/health", headers={"X-Request-ID": "trace-log"})
    access_records = [r for r in caplog.records if r.name == "access"]
    assert access_records, "액세스 로그가 남아야 함"
    line = access_records[-1]
    assert "GET" in line.message and "/health" in line.message
