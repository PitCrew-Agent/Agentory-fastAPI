# AGENTS.md — 코딩 에이전트 지침

자율형 제조 데이터 분석 및 MCP 에이전트 시스템 백엔드. Codex·Claude Code 등
코딩 에이전트는 작업 전 이 문서를 따른다.

## 명령어

```bash
uv sync                          # 의존성 설치
uv run pytest                    # 테스트 (필수 통과)
uv run ruff check . --fix        # 린트
uv run ruff format .             # 포맷
uv run agentory-api              # FastAPI :8000
uv run mcp-realtime              # MCP 서버 :8101
uv run mcp-knowledge             # MCP 서버 :8102
uv run alembic upgrade head      # DB 마이그레이션 (docker compose up -d db 선행)
```

## 아키텍처 규칙

- 모듈러 모놀리스, 상세 배경은 docs/adr/0001-architecture.md 참고
- `src/agentory/modules/` 하위 모듈 경계 = 팀 담당자 경계, 자기 담당 외 모듈 수정 시 담당자 합의 필수
- 모듈 내부 계층은 router → service → repository 3단만 사용, 과도한 추상화 금지
- 포트(인터페이스)는 3곳만 유지: `agent/llm/base.py`, `rag/embedding/base.py`, `rag/store/base.py`
- SSE 이벤트 스키마는 `src/agentory/common/events.py`가 단일 소스, 변경 시
  docs/sse-events.md와 tests/contracts/ 동시 갱신 및 프론트 합의 필수
- 새 환경 변수는 `core/config.py`와 `.env.example`에 동시 추가, 비밀값 하드코딩 금지
- 새 SQLAlchemy 모델은 `alembic/env.py`의 import 블록에 등록 후 마이그레이션 생성

## 주석 규칙 (필수)

- 간단하게, 명사형 어미로 작성
- 온점(.) 금지, `—`(em dash) 금지
- 담당자 표기는 `TODO(담당자명)` 형태로만 작성, `담당: 이름` 같은 표기 금지
- 기능 ID(예: BE_MCP02_TELEMETRY01)를 괄호로 병기해 기능명세서와 추적 연결

## Git 규칙

- default 브랜치 `develop`, 직접 푸시 금지, 작업은 `feature/<기능ID>` 브랜치에서 PR로 머지
- 커밋 메시지: `<타입>: <내용> (<기능ID>)`, 타입은 feat / fix / refactor / test / docs / chore
- 커밋·푸시·이슈 생성·PR 생성은 반드시 사용자에게 계획(파일 단위 분할 + 메시지)을 먼저 보여주고 승인 후 실행
- AI를 협업자로 추가 금지: Co-Authored-By, Generated-by 등 AI 서명·크레딧 라인 절대 삽입 금지

## 이슈·PR 작성 규칙

- 템플릿 사용: `.github/ISSUE_TEMPLATE/작업-이슈.md`, `.github/PULL_REQUEST_TEMPLATE.md`
- "작업 개요"는 격식체로 사람이 쓴 것처럼 자연스러운 어투로 서술
- 기술 선택은 대안과의 정량 비교 표 + 선택 근거 필수
- PR에는 개선 효과 정량 평가 표 추가, 맨 마지막에 `Closes #이슈번호`
- 그 외 항목은 명사형 어미로 간단 작성

## 테스트 규약

- 공통 픽스처는 tests/conftest.py, 골든 질의 셋 포맷은 tests/golden/README.md 준수
- 레이어: unit(모듈 단위) / integration(DB·MCP 연동) / contracts(SSE 계약) / e2e(골든 시나리오)
- 기능 구현 시 기능명세서의 "예외 처리" 열(빈 결과·파라미터 오류 등) 케이스 포함
