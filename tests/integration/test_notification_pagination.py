"""알림 페이지 번호 페이지네이션 통합 테스트 (NEW_PROACT01_ALERT02)

실제 DB 필요, 미연결 시 스킵, flush만 하고 teardown rollback으로 미오염
전역 데이터와 겹치지 않도록 2000년 창의 전용 라인 알림을 삽입해 격리
offset 기반이라 단정이 흔들리지 않게 전용 라인으로 스코핑해 조회
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentory.common.exceptions import ValidationError
from agentory.core.config import get_settings
from agentory.modules.auth.models import User
from agentory.modules.notification import repository, service
from agentory.modules.notification.models import Notification

EQP = "ZZZ-PAGE-01"
LINE = "ZZZ-PAGE-LINE"  # 전용 라인으로 스코핑해 전역 데이터와 격리
Y2000 = datetime(2000, 1, 1, 0, 0, tzinfo=UTC)  # 전역 데이터(2020+)와 격리된 창


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


async def _seed(session, count: int):
    # 시각 오름차순으로 count건 생성, 마지막 항목이 가장 최신
    rows = []
    for index in range(count):
        occurred_at = Y2000 + timedelta(minutes=index)
        n = Notification(
            occurred_at=occurred_at,
            equipment_id=EQP,
            line_name=LINE,
            alarm_code="ERR-000",
            message="테스트 알림",
            bucket_start=occurred_at,
        )
        session.add(n)
        rows.append(n)
    await session.flush()
    return rows  # rows[0] 가장 과거 .. rows[-1] 최신


async def _user(session, email: str):
    user = User(email=email, name="페이지", role="field_engineer", status="active")
    session.add(user)
    await session.flush()
    return user


async def test_page_returns_requested_slice(session):
    rows = await _seed(session, 25)
    newest_first = [r.id for r in reversed(rows)]

    first = await service.list_notifications(session, page=1, limit=10, line_names=[LINE])
    assert [i.id for i in first.items] == newest_first[:10]
    assert first.page == 1
    assert first.total_items == 25
    assert first.total_pages == 3
    assert first.has_more is True


async def test_jump_to_arbitrary_page(session):
    rows = await _seed(session, 25)
    newest_first = [r.id for r in reversed(rows)]

    # 페이지 번호로 임의 이동, 커서 없이 3페이지 직접 조회
    third = await service.list_notifications(session, page=3, limit=10, line_names=[LINE])
    assert [i.id for i in third.items] == newest_first[20:25]
    assert third.page == 3
    assert third.has_more is False


async def test_pages_cover_all_items_without_overlap(session):
    rows = await _seed(session, 25)
    collected: list[int] = []
    for page_no in range(1, 4):
        page = await service.list_notifications(session, page=page_no, limit=10, line_names=[LINE])
        collected += [i.id for i in page.items]

    assert collected == [r.id for r in reversed(rows)]
    assert len(collected) == len(set(collected))


async def test_page_beyond_last_clamps_to_last_page(session):
    await _seed(session, 25)
    page = await service.list_notifications(session, page=99, limit=10, line_names=[LINE])
    # 빈 목록 대신 마지막 페이지 반환
    assert page.page == 3
    assert len(page.items) == 5


async def test_empty_scope_returns_first_page(session):
    page = await service.list_notifications(session, page=1, limit=10, line_names=[])
    assert page.items == []
    assert page.page == 1
    assert page.total_items == 0
    assert page.total_pages == 0
    assert page.has_more is False


async def test_limit_caps_page_size(session):
    await _seed(session, 25)
    page = await service.list_notifications(session, page=1, limit=2, line_names=[LINE])
    assert len(page.items) == 2
    assert page.limit == 2
    assert page.total_pages == 13  # 25건을 2건씩


async def test_unread_only_filters(session):
    rows = await _seed(session, 3)
    # 읽음은 사용자별 상태이므로 조회도 동일 user_id 기준 (BE_NOTI01_SCOPE01)
    user = await _user(session, "noti-page@test.local")
    await repository.mark_read(session, rows[2].id, user.id)

    page = await service.list_notifications(
        session, page=1, limit=10, line_names=[LINE], unread_only=True, user_id=user.id
    )
    assert {i.id for i in page.items} == {rows[0].id, rows[1].id}  # 읽은 항목 제외
    assert page.total_items == 2


async def test_range_filters_to_half_open_window(session):
    # 발생 시각 [start, end) 반열림 구간만 조회, rows[index]는 Y2000+index분 (BE_NOTI01_RANGE01)
    rows = await _seed(session, 25)
    page = await service.list_notifications(
        session,
        page=1,
        limit=10,
        line_names=[LINE],
        start=Y2000 + timedelta(minutes=5),
        end=Y2000 + timedelta(minutes=10),
    )
    # 5·6·7·8·9분(=rows[5..9])만 포함, 발생 역순
    assert [i.id for i in page.items] == [r.id for r in rows[9:4:-1]]
    assert page.total_items == 5  # count도 동일 필터로 정합


async def test_range_end_is_exclusive(session):
    # end와 정확히 같은 시각의 알림은 미포함(반열림 상한)
    rows = await _seed(session, 25)
    page = await service.list_notifications(
        session,
        page=1,
        limit=10,
        line_names=[LINE],
        start=Y2000 + timedelta(minutes=5),
        end=Y2000 + timedelta(minutes=6),  # rows[6] 시각과 동일
    )
    assert [i.id for i in page.items] == [rows[5].id]  # rows[6]은 상한 미포함


async def test_open_ended_range_filters_one_side(session):
    # start만 지정 시 하한만 적용, end 미지정이면 상한 없음
    rows = await _seed(session, 25)
    page = await service.list_notifications(
        session, page=1, limit=50, line_names=[LINE], start=Y2000 + timedelta(minutes=20)
    )
    assert {i.id for i in page.items} == {r.id for r in rows[20:]}  # 20분 이후 전체
    assert page.total_items == 5


async def test_inverted_range_raises_validation(session):
    # start >= end 경계 역전 요청은 400
    with pytest.raises(ValidationError):
        await service.list_notifications(
            session,
            page=1,
            limit=10,
            line_names=[LINE],
            start=Y2000 + timedelta(minutes=10),
            end=Y2000 + timedelta(minutes=5),
        )


async def test_available_dates_returns_distinct_kst_days(session):
    # 알림이 있는 KST 날짜만 distinct 오름차순 반환 (BE_NOTI01_RANGE01)
    # Y2000(=2000-01-01 00:00 UTC)은 KST로 2000-01-01 09:00이라 같은 날, 하루 뒤는 다음 날
    base = Y2000
    for offset in (base, base, base + timedelta(days=1), base + timedelta(days=3)):
        session.add(
            Notification(
                occurred_at=offset,
                equipment_id=EQP,
                line_name=LINE,
                alarm_code="ERR-000",
                message="테스트 알림",
                bucket_start=offset,
            )
        )
    await session.flush()

    result = await service.list_available_dates(session, line_names=[LINE])
    assert [d.isoformat() for d in result.dates] == ["2000-01-01", "2000-01-02", "2000-01-04"]


async def test_available_dates_respects_line_scope(session):
    # 담당 라인 밖 알림은 가용 날짜에서 제외
    await _seed(session, 1)  # LINE 소속 1건
    other = Notification(
        occurred_at=Y2000 + timedelta(days=10),
        equipment_id=EQP,
        line_name="ZZZ-OTHER-LINE",
        alarm_code="ERR-000",
        message="타 라인",
        bucket_start=Y2000 + timedelta(days=10),
    )
    session.add(other)
    await session.flush()

    result = await service.list_available_dates(session, line_names=[LINE])
    assert all(d.isoformat() != "2000-01-11" for d in result.dates)  # 타 라인 날짜 미포함


async def test_available_dates_bounds_by_range(session):
    # start·end 반열림 구간 밖의 날짜는 제외
    await _seed(session, 5)  # 0~4분, 모두 2000-01-01
    later = Notification(
        occurred_at=Y2000 + timedelta(days=5),
        equipment_id=EQP,
        line_name=LINE,
        alarm_code="ERR-000",
        message="범위 밖",
        bucket_start=Y2000 + timedelta(days=5),
    )
    session.add(later)
    await session.flush()

    result = await service.list_available_dates(
        session, line_names=[LINE], start=Y2000, end=Y2000 + timedelta(days=1)
    )
    assert [d.isoformat() for d in result.dates] == ["2000-01-01"]  # 1/6은 상한 밖
