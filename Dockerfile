# 공용 이미지: api / mcp-realtime / mcp-knowledge / simulator는
# docker-compose에서 command 오버라이드로 분기 (INFRA01_DOCKER01)
FROM python:3.12-slim

# docling 런타임 의존(opencv 계열) 시스템 라이브러리, 매뉴얼 적재를 공용 이미지에서 수행하기 위함
# 이 블록이 없으면 ingest 전용 이미지를 따로 수동 빌드해야 해 CD 갱신 대상에서 누락 (INFRA01_DOCKER01)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 의존성 레이어 캐시 분리
COPY pyproject.toml uv.lock ./
COPY README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
# 매뉴얼 원본·매니페스트, ingest run-task가 이미지 내부 경로에서 읽음
COPY data/rags ./data/rags
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "agentory.main:app", "--host", "0.0.0.0", "--port", "8000"]
