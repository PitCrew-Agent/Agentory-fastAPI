"""대화 히스토리 목록·상세 통합 테스트 (BE_CHAT03_HISTORY01)

실제 DB(마이그레이션 완료) 필요, 미연결 시 스킵, 조회는 읽기 전용이라 rollback으로 미오염
목록 제목·장비·집계, 상세 메시지, 소유자 스코프 검증
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.core.config import get_settings
from agentory.modules.chat import service
from agentory.modules.chat.models import ChatMessage, ChatSession

# 실데이터와 겹치지 않는 테스트 전용 소유자
OWNER = "zzz-chat-owner@example.com"
OTHER = "zzz-chat-other@example.com"


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as probe:
            await probe.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("DB 연결 불가, 통합 테스트 스킵")

    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def _seed_session(
    session, *, user_sub=OWNER, equipment_id="ZZZ-EQP", first_q="온도 이상 원인 분석"
):
    # 세션 + user/assistant 메시지 1쌍 생성 (flush만, 커밋 안 함)
    sid = uuid.uuid4()
    base = datetime(2020, 5, 5, 1, 0, tzinfo=UTC)
    session.add(ChatSession(session_id=sid, user_sub=user_sub, equipment_id=equipment_id))
    await session.flush()  # FK 위해 세션 먼저 삽입
    session.add(ChatMessage(session_id=sid, role="user", content=first_q, created_at=base))
    session.add(
        ChatMessage(
            session_id=sid,
            role="assistant",
            content="분석 결과입니다",
            created_at=base.replace(hour=2),
        )
    )
    await session.flush()
    return sid


async def test_list_returns_title_equipment_and_counts(session):
    sid = await _seed_session(session, equipment_id="ZZZ-EQP-A01", first_q="A01 온도 왜 이래")
    items = await service.list_sessions(session, OWNER)
    mine = next(i for i in items if i.session_id == str(sid))
    assert mine.equipment_id == "ZZZ-EQP-A01"
    assert mine.title == "A01 온도 왜 이래"  # 첫 사용자 질문
    assert mine.message_count == 2
    assert mine.last_message_at is not None


async def test_list_strips_equipment_prefix_from_title(session):
    # 제목 앞의 장비id는 배지와 중복이라 제거 (B안)
    sid = await _seed_session(
        session, equipment_id="EQP-A01", first_q="EQP-A01 온도 추세 24시간 보여줘"
    )
    items = await service.list_sessions(session, OWNER)
    mine = next(i for i in items if i.session_id == str(sid))
    assert mine.equipment_id == "EQP-A01"
    assert mine.title == "온도 추세 24시간 보여줘"  # 앞 장비id 제거됨


async def test_list_keeps_title_when_prefix_is_similar_id(session):
    # 유사 id(EQP-A011)는 세션 장비(EQP-A01)와 달라 제거 안 함
    sid = await _seed_session(session, equipment_id="EQP-A01", first_q="EQP-A011 압력 확인")
    items = await service.list_sessions(session, OWNER)
    mine = next(i for i in items if i.session_id == str(sid))
    assert mine.title == "EQP-A011 압력 확인"


async def test_list_truncates_long_title(session):
    long_q = "가" * 50
    sid = await _seed_session(session, first_q=long_q)
    items = await service.list_sessions(session, OWNER)
    mine = next(i for i in items if i.session_id == str(sid))
    assert mine.title == "가" * 30 + "…"  # TITLE_MAX_LEN 절삭


async def test_list_strips_response_format_scaffolding_from_title(session):
    # 프론트가 붙이는 응답 형식 지시 스캐폴딩은 제목에서 제외 (BE_CHAT03_HISTORY01)
    sid = await _seed_session(
        session,
        equipment_id=None,
        first_q="온도 30일 추세 보여줘\n\n---\n응답 형식 지시:\n답변은 표로 정리해줘",
    )
    items = await service.list_sessions(session, OWNER)
    mine = next(i for i in items if i.session_id == str(sid))
    assert mine.title == "온도 30일 추세 보여줘"


async def test_list_takes_first_line_for_multiline_question(session):
    # 구분선이 없어도 여러 줄이면 첫 비어있지 않은 줄만 제목으로
    sid = await _seed_session(session, equipment_id=None, first_q="압력 확인\n추가 설명 줄")
    items = await service.list_sessions(session, OWNER)
    mine = next(i for i in items if i.session_id == str(sid))
    assert mine.title == "압력 확인"


async def test_list_scoped_to_owner(session):
    sid_other = await _seed_session(session, user_sub=OTHER)
    items = await service.list_sessions(session, OWNER)
    assert all(i.session_id != str(sid_other) for i in items)  # 타인 세션 미노출


async def test_detail_returns_messages_in_order(session):
    sid = await _seed_session(session)
    detail = await service.get_session_detail(session, str(sid), OWNER)
    assert detail is not None
    assert detail.equipment_id == "ZZZ-EQP"
    assert [m.role for m in detail.messages] == ["user", "assistant"]
    assert detail.messages[0].content == "온도 이상 원인 분석"


async def test_detail_other_owner_returns_none(session):
    sid = await _seed_session(session, user_sub=OTHER)
    assert await service.get_session_detail(session, str(sid), OWNER) is None


async def test_detail_invalid_uuid_returns_none(session):
    assert await service.get_session_detail(session, "not-a-uuid", OWNER) is None
