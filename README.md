# Agentory — 자율형 제조 데이터 분석 및 MCP 에이전트 시스템 (Backend)

자연어 질의 → Supervisor+ReAct 자율 추론 → MCP 도구 호출(실시간 데이터·RAG) → 종합 답변.

## 빠른 시작

```bash
# 1. 의존성 설치 (uv 필요: https://docs.astral.sh/uv/)
uv sync

# 2. 환경 변수
cp .env.example .env   # API 키 등 채우기

# 3. DB (PostgreSQL + pgvector)
docker compose up -d db
uv run alembic upgrade head

# 4. 실행 (각각 별도 터미널)
uv run agentory-api      # FastAPI  :8000  (Swagger: http://localhost:8000/docs)
uv run mcp-realtime      # MCP 서버 :8101
uv run mcp-knowledge     # MCP 서버 :8102
uv run simulator         # 센서 데이터 시뮬레이터

# 테스트 / 린트
uv run pytest
uv run ruff check . && uv run ruff format --check .

# pre-commit 훅 설치 (최초 1회)
uv run pre-commit install
```

## 구조

```
src/
├── agentory/            # 메인 FastAPI 앱 (Agent in-process)
│   ├── core/            # 설정(.env)·DB 세션·로깅
│   ├── common/          # SSE 이벤트 계약(events.py)·공용 예외
│   └── modules/         # 기능 모듈 = 담당자 경계
│       ├── chat/        # 질의 API·SSE 스트리밍
│       ├── auth/        # OIDC/JWT 미들웨어
│       ├── agent/       # Supervisor+ReAct·워커·프롬프트
│       ├── telemetry/   # 설비·센서 모델, 트윈용 REST
│       ├── watcher/     # 이상 징후 감지 백그라운드
│       └── rag/         # 인제스트·임베딩·벡터 스토어
├── mcp_realtime/        # MCP 서버: 센서·알람·설비 메타
├── mcp_knowledge/       # MCP 서버: 매뉴얼·유사사례 검색
└── simulator/           # 센서 데이터 시뮬레이터
tests/                   # unit / integration / contracts / e2e / golden
docs/                    # ADR·SSE 계약 문서
```

아키텍처 결정 배경: [docs/adr/0001-architecture.md](docs/adr/0001-architecture.md)

## 협업 규칙 (DEV_SETUP)

- **default 브랜치: `develop`** — 직접 푸시 금지, PR로만 머지 (리뷰 1인 이상)
- 작업 브랜치: `feature/<이슈번호>-<기능명>` (예: `feature/12-db-schema`)
- 배포 브랜치: `main` (develop → main 머지는 배포 시점에)
- 커밋 메시지: `<타입>: <내용> (<기능ID>)` — 타입: feat / fix / refactor / test / docs / chore
- SSE 이벤트 스키마(`common/events.py`) 변경은 프론트와 합의 후 계약 테스트와 함께 PR

## 문서

- 요구사항 정의서·기능명세서: 팀 Google Drive
- SSE 이벤트 계약: [docs/sse-events.md](docs/sse-events.md)
- 골든 질의 셋 포맷: [tests/golden/README.md](tests/golden/README.md)
