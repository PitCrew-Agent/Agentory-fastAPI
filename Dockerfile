# 공용 이미지: api / mcp-realtime / mcp-knowledge / simulator는
# docker-compose에서 command 오버라이드로 분기 (INFRA01_DOCKER01)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 의존성 레이어 캐시 분리
COPY pyproject.toml uv.lock ./
COPY README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "agentory.main:app", "--host", "0.0.0.0", "--port", "8000"]
