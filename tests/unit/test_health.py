"""앱 기동·헬스체크 스모크 테스트"""


async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    # ApiResponse 구조: 실제 상태는 result 안
    assert body["success"] is True
    assert body["result"]["status"] == "ok"


async def test_openapi_docs(client):
    """Swagger 자동 문서 노출 확인 (DEV_API_DOC)"""
    res = await client.get("/openapi.json")
    assert res.status_code == 200
