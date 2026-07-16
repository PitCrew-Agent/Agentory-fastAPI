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
engine = create_async_engine(
    get_settings().database_url,
    echo=False,
    pool_size=10,
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
