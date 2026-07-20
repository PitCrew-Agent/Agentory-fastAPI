"""비동기 DB 세션 관리 (DEV_DATABASE)

SQLAlchemy 2 async + asyncpg 기반
모델은 각 모듈 models.py에서 Base 상속으로 정의, 마이그레이션은 alembic으로 관리
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from agentory.core.config import get_settings


class Base(DeclarativeBase):
    pass


# 커넥션 풀 튜닝, 고갈 시 빠른 실패·유휴 커넥션 사전 검증 (NEW_PROACT01_DETECT01)
# 상한 30(20+10) 산정 근거: 요청 1건이 감사 로그 적재·본조회로 커넥션 2개 사용,
# 대시보드 폴링 초당 6.5건 기준 상한 20으로는 고갈, RDS db.t4g.micro max_connections
# 약 112개 대비 타 서비스(simulator·knowledge·maintenance·realtime) 몫 남기고 상향 (DEV_DATABASE)
engine = create_async_engine(
    get_settings().database_url,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_timeout=10,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends용 세션 팩토리"""
    async with SessionLocal() as session:
        yield session
