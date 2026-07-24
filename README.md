# Agentory: 자율형 제조 데이터 분석 및 MCP 에이전트 시스템 (Backend)

자연어 질의 → Fast Router 분류 → Planner 자율 계획 + 병렬 MCP 도구 호출(실시간 데이터·RAG·정비 이력) → 종합 답변.

## 빠른 시작

```bash
# 1. 의존성 설치 (uv 필요: https://docs.astral.sh/uv/)
uv sync

# 2. 환경 변수
# 새 환경에서만 실행
cp .env.example .env   # API 키 등 채우기
# 기존 환경에서는 .env의 EMBEDDING_*·RERANKER_* 값을 .env.example에 맞춰 갱신

# 3. RAG 모델 가중치 캐시 등록
uv run python scripts/prefetch_models.py
# 현재 EMBEDDING_*·RERANKER_* 설정의 로컬 모델 가중치를 다운로드하고 HF 캐시에 등록

# 4. DB (PostgreSQL + pgvector)
docker compose up -d db
uv run alembic upgrade head

# 5. RAG 매뉴얼 적재
uv run python scripts/ingest_manuals.py
# 파싱·청킹·임베딩 후 knowledge_collection에 벡터를 적재

# 6. 실행 (각각 별도 터미널)
uv run agentory-api      # FastAPI  :8000  (Swagger: http://localhost:8000/docs)
uv run mcp-realtime      # MCP 서버 :8101  센서·알람·설비 메타
uv run mcp-knowledge     # MCP 서버 :8102  매뉴얼·유사사례 검색
uv run mcp-maintenance   # MCP 서버 :8103  설비 정비·수리 이력
uv run simulator         # 센서 데이터 시뮬레이터

# 테스트 / 린트
uv run pytest
uv run ruff check . && uv run ruff format --check .

# pre-commit 훅 설치 (최초 1회)
uv run pre-commit install
```

파서·청커·임베더 또는 임베딩 차원이 바뀐 브랜치를 받으면 3단계부터 5단계까지 다시 실행합니다 재적재는 doc_id 기준 멱등이라 반복 실행해도 안전합니다

3단계는 현재 config(`EMBEDDING_*`·`RERANKER_*`)의 로컬 모델 가중치를 HF 캐시에 등록합니다 모델 설정을 바꾸면 3단계를 다시 실행합니다 폐쇄망에서는 `EMBEDDING_MODEL`·`RERANKER_MODEL`에 로컬 경로를 지정하면 다운로드 없이 해당 경로에서 로드합니다

## 이상 감지 CLI

이상 감지 파이프라인은 서버 기동과 별개로 운영하는 CLI 두 개를 제공합니다. 정상 telemetry가 쌓인
뒤 실행하며, 모델이 없으면 워처가 즉시 반환하므로 미실행 상태로 두어도 무해합니다.

```bash
uv run anomaly-fit                      # 공정 유형별 PCAX 모델 적합·저장 (정상 telemetry 필요)
uv run anomaly-shadow-report --days 7   # 섀도우 감지 vs 규칙 알람 비교 리포트 (기본 7일)
```

| 명령 | 역할 |
| --- | --- |
| `anomaly-fit` | 정상 구간 telemetry로 공정 유형별 모델 적합, `equipment_anomaly_models`에 저장 |
| `anomaly-shadow-report` | 섀도우 기록과 규칙 알람을 detector-only·overlapping·rule-only로 분류 |

배포 절차와 파라미터는 [docs/anomaly-serving.md](docs/anomaly-serving.md)를 참고합니다.

## 구조

```
src/
├── agentory/            # 메인 FastAPI 앱 (Agent in-process)
│   ├── core/            # 설정(.env)·DB 세션·로깅
│   ├── common/          # SSE 이벤트 계약(events.py)·공용 예외
│   └── modules/         # 기능 모듈 = 담당자 경계
│       ├── chat/         # 질의 API·SSE 스트리밍
│       ├── auth/         # OIDC/JWT 미들웨어
│       ├── agent/        # Fast Router·Planner 오케스트레이터·MCP 클라이언트·프롬프트
│       ├── telemetry/    # 설비·센서 모델, 트윈용 REST
│       ├── watcher/      # 이상 징후 감지 백그라운드·모델 적합 CLI
│       ├── rag/          # 인제스트·임베딩·벡터 스토어
│       ├── admin/        # 라인·유저 관리, 설비 책임자·수리 이력
│       ├── incident/     # 장애 대응 계획 생성
│       ├── notification/ # 알림 REST·실시간 SSE
│       └── worklog/      # 작업 로그 REST·완료 시 도메인 이력 적재
├── mcp_realtime/        # MCP 서버: 센서·알람·설비 메타
├── mcp_knowledge/       # MCP 서버: 매뉴얼·유사사례 검색
├── mcp_maintenance/     # MCP 서버: 설비 정비·수리 이력
├── anomaly/             # 이상 감지 알고리즘 (PCAX·VAR·윈도잉·평가, 순수 로직)
└── simulator/           # 센서 데이터 시뮬레이터
tests/                   # unit / integration / contracts / e2e / golden
docs/                    # ADR·SSE 계약·에이전트·이상 감지·실험 문서
```

에이전트 기본 경로는 Fast Router로 잡담을 분류한 뒤, Planner가 필요한 MCP 도구를 스스로 골라
병렬 실행하고 관찰 결과로 재판단하는 하이브리드 오케스트레이터입니다. 레거시 Supervisor + 워커
ReAct 경로는 `agent_orchestrator_enabled=false`로 되돌릴 수 있게 남겨 두었습니다.

아키텍처 결정 배경: [docs/adr/0001-architecture.md](docs/adr/0001-architecture.md),
[docs/adr/0009-agent-hybrid-orchestration.md](docs/adr/0009-agent-hybrid-orchestration.md)

## 협업 규칙 (DEV_SETUP)

- **default 브랜치: `develop`**, 직접 푸시 금지, PR로만 머지 (리뷰 1인 이상)
- 작업 브랜치: `feature/<이슈번호>-<기능명>` (예: `feature/12-db-schema`)
- 배포 브랜치: `main` (develop → main 머지는 배포 시점에)
- 커밋 메시지: `<타입>: <내용> (<기능ID>)`, 타입: feat / fix / refactor / test / docs / chore
- SSE 이벤트 스키마(`common/events.py`) 변경은 프론트와 합의 후 계약 테스트와 함께 PR

## 문서

- 요구사항 정의서·기능명세서: 팀 Google Drive
- 에이전트 아키텍처: [docs/agent/architecture.md](docs/agent/architecture.md)
- 데이터베이스 설계: [docs/database.md](docs/database.md)
- 센서 데이터 시뮬레이터: [docs/simulator.md](docs/simulator.md)
- MCP 서버: [docs/mcp/](docs/mcp/)
- SSE 이벤트 계약: [docs/sse-events.md](docs/sse-events.md)
- API 응답 규약: [docs/api-response.md](docs/api-response.md)
- 이상 감지 서빙: [docs/anomaly-serving.md](docs/anomaly-serving.md)
- 작업 로그: [docs/worklog.md](docs/worklog.md)
- 실험 기록: [docs/experiments/](docs/experiments/)
- 골든 질의 셋 포맷: [tests/golden/README.md](tests/golden/README.md)
