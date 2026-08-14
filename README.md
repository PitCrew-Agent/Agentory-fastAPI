# Agentory: 자율형 제조 데이터 분석 및 MCP 에이전트 시스템 (Backend)
<img width="812" height="482" alt="스크린샷 2026-08-06 오후 6 32 39" src="https://github.com/user-attachments/assets/dbe22805-a395-4ec9-957b-f1f9b1061862" />
<br>
제조 설비의 복합 이상을 다변량 센서에서 잡아내고, 현장의 자연어 질문에 실시간 센서·매뉴얼·정비 이력을 스스로 종합해 근거와 함께 답합니다.

## 프로젝트 소개

### 배경

제조 설비 모니터링은 오랫동안 설비마다 고정한 임계값과 알람 규칙에 의존해 왔습니다. 이 방식은 두 가지를 놓칩니다. 설비마다 정상 운전점이 달라 공통 임계로는 개체 차이를 반영하지 못하고, 각 센서가 모두 임계 안쪽에 있으면서 센서 사이의 관계만 깨지는 복합 이상은 원리적으로 감지되지 않습니다. 한편 현장 담당자는 설비 상태를 자연어로 묻고 답을 얻으려면 실시간 센서·정비 이력·매뉴얼을 각각 다른 화면에서 직접 조합해야 했습니다.

Agentory는 이 두 간극을 메우기 위해, 다변량 센서에서 정상 운전의 모양을 학습하는 이상 감지 파이프라인과, 자연어 질의를 받아 필요한 데이터를 스스로 모아 근거와 함께 답하는 에이전트를 하나의 백엔드로 묶었습니다.

### 해결하려는 문제

| 영역 | 문제 |
| --- | --- |
| 이상 감지 | 설비 개체 차이를 무시하는 고정 임계, 센서 관계만 붕괴하는 복합 이상 미검출 |
| 데이터 접근 | 실시간 센서·정비 이력·매뉴얼이 분리돼 현장 담당자가 수작업으로 종합 |
| 에이전트 효율 | 라우팅 왕복이 직렬로 쌓여 응답 지연·토큰 비용 증가 |
| 매뉴얼 검색 | Dense 임베딩이 알람코드·장비명 같은 어휘 일치 신호를 놓침 |

### 접근

- **Fast Router**: 인사·잡담·범위 밖 질의를 규칙으로 분류해 라우팅 LLM 호출 없이 즉시 응답합니다
- **Planner 하이브리드 오케스트레이터**: 단일 ReAct 에이전트가 필요한 MCP 도구를 스스로 골라 병렬 호출하고 관찰 결과로 재판단합니다
- **RAG 하이브리드 검색**: Dense 임베딩과 BM25 어휘 후보를 convex 융합해 어휘 신호까지 회수하고 CrossEncoder로 재정렬합니다
- **레이어드 이상 감지**: 규칙 임계 위에 PCA MSPC(T²·SPE) + EWMA 통계 레이어를 얹고 설비별 임계를 캘리브레이션하며, LLM은 판정이 아니라 채널 기여도 설명을 담당합니다

### 성과

에이전트 구조 개편(레거시 → 하이브리드 오케스트레이터) 전후 비교입니다.

| 지표 | 개선 |
| --- | --- |
| 평균 응답 지연 | -58.1% (품질 벤치 n=12) |
| 질의당 비용 | -58.2% |
| 라우팅 정확도 | 0.878 → 0.944 |
| 인용률 | 0.545 → 0.955 |
| 환각율(미존재 대상) | 0.50 → 0.00 |

이상 감지(최종 스택 PCA-both) 성능입니다.

| 지표 | 값 |
| --- | --- |
| 이벤트 recall | 1.000 |
| 운영 정합 오탐(FAR) | 0.59건/설비·일 (규칙 18.07 대비 약 1/30) |
| 상관 붕괴(corr_break) recall | 규칙 0.0 → 통계 1.0 |

이상 감지 수치는 합성 시뮬레이터 데이터 기준의 상한이며, 실제 알람 발령 전 섀도우 모드로 실측 관찰 중입니다.

## 사전 준비

| 도구 | 버전 | 용도 |
| --- | --- | --- |
| Python | 3.12 이상 | 런타임 |
| uv | 최신 | 의존성·실행 관리 (https://docs.astral.sh/uv/) |
| Docker | Compose v2 | PostgreSQL(pgvector)·Redis 로컬 스택 |

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

# 4. 인프라 (PostgreSQL + pgvector, Redis) — 인증·세션·감사 로그에 Redis 필요
docker compose up -d db redis
uv run alembic upgrade head

# 5. 데모 데이터 시드 (식각 3라인 설비·텔레메트리·수리 이력)
# 시뮬레이터가 아직 안 떠 있을 때 실행, 재실행 시 데모 데이터를 비우고 재적재 (멱등)
uv run python scripts/seed_data.py

# 6. RAG 매뉴얼 적재
uv run python scripts/ingest_manuals.py
# 파싱·청킹·임베딩 후 knowledge_collection에 벡터를 적재

# 7. 실행 (각각 별도 터미널)
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

파서·청커·임베더 또는 임베딩 차원이 바뀐 브랜치를 받으면 3단계와 6단계를 다시 실행합니다 재적재는 doc_id 기준 멱등이라 반복 실행해도 안전합니다

3단계는 현재 config(`EMBEDDING_*`·`RERANKER_*`)의 로컬 모델 가중치를 HF 캐시에 등록합니다 모델 설정을 바꾸면 3단계를 다시 실행합니다 폐쇄망에서는 `EMBEDDING_MODEL`·`RERANKER_MODEL`에 로컬 경로를 지정하면 다운로드 없이 해당 경로에서 로드합니다

## 필수 환경 변수

`.env.example`를 복사하면 로컬 기본값(DB·Redis·MCP URL 등)은 그대로 동작합니다. 직접 채워야 하는 값은 다음과 같으며, 나머지 항목(에이전트·RAG·이상 감지 튜닝)은 `.env.example`의 주석 설명을 참고해 필요 시 조정합니다.

| 변수 | 필수 여부 | 설명 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 필수 | LLM 라우팅·계획·답변 합성 호출용, 없으면 에이전트가 응답하지 못합니다 |
| `AZURE_AD_TENANT_ID`·`AZURE_AD_CLIENT_ID`·`AZURE_AD_CLIENT_SECRET` | 로그인 사용 시 | Azure AD OIDC 인증용, 이 셋만 채우면 `OIDC_*`는 자동 유도되며 다른 IdP는 `OIDC_*`를 직접 지정합니다 |
| `DATABASE_URL`·`DB_PASSWORD` | 선택 | 로컬은 기본값을 그대로 사용하고, dev/prod는 RDS 관리형 시크릿의 비밀번호를 `DB_PASSWORD`로 주입합니다 |

## 전체 스택 실행 (Docker Compose)

빠른 시작이 각 서비스를 로컬에서 개별 실행하는 개발용 경로라면, 아래는 api·MCP 3종·simulator를 컨테이너로 한 번에 띄우는 통합 확인용 경로입니다. DB 준비(마이그레이션·시드·매뉴얼 적재)는 compose가 자동 실행하지 않으므로, 노출된 DB로 호스트에서 먼저 수행합니다.

```bash
# 1. 인프라 기동 + DB 준비 (호스트에서 1회)
cp .env.example .env                     # OPENAI_API_KEY 등 채우기
docker compose up -d db redis
uv run alembic upgrade head
uv run python scripts/seed_data.py
uv run python scripts/prefetch_models.py
uv run python scripts/ingest_manuals.py

# 2. 앱 스택 기동 (이미지 빌드 포함)
docker compose up --build
# api :8000 · mcp-realtime :8101 · mcp-knowledge :8102 · mcp-maintenance :8103 · simulator
```

이미지에는 RAG 모델 가중치가 포함되지 않아 `mcp-knowledge` 컨테이너가 첫 검색 시 모델을 내려받습니다. HF 캐시를 볼륨으로 마운트하지 않으면 컨테이너 재생성마다 다시 받으므로, 반복 기동이 잦거나 폐쇄망이면 캐시 볼륨 마운트를 권장합니다.

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
