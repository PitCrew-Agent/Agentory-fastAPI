# ADR-0001: 모듈러 모놀리스 + 멀티 엔트리포인트 모노레포

- 상태: 승인 (2026-07-02)
- 결정자: 주희정 (팀 공유 필요)

## 배경

4주 · 신입 4인 팀으로 REQ-T-01(ReAct), REQ-T-02(MCP 표준), FR 전 범위를 구현해야 한다.
MCP 서버는 표준상 별도 프로세스가 자연스러우므로 완전한 단일 프로세스는 불가능하고,
반대로 MSA는 4주 내 배포·디버깅 복잡도를 감당할 수 없다.

## 결정

1. **저장소는 하나, 실행 프로세스는 4개** — `agentory-api`(FastAPI+Agent), `mcp-realtime`,
   `mcp-knowledge`, `simulator`. uv 단일 패키지에 엔트리포인트 스크립트로 분리.
2. **Agent(Supervisor+ReAct)는 FastAPI 앱 in-process** — SSE로 Thought/Action/Observation을
   중계 계층 없이 바로 스트리밍하기 위함.
3. **모듈 경계 = 담당자 경계** — `modules/` 하위 패키지를 역할 분담표와 1:1로 맞춰
   머지 컨플릭트를 최소화.
4. **계층은 router → service → repository 3단**만 사용. 포트(인터페이스)는 비기능 요구사항
   "확장성"이 지목한 교체 지점 3곳에만 둔다:
   - LLM 프로바이더: `modules/agent/llm/base.py`
   - 임베딩: `modules/rag/embedding/base.py`
   - 벡터 스토어: `modules/rag/store/base.py`
5. **DB는 PostgreSQL 단일 인스턴스 + pgvector 확장** — 정형(§8.1, §8.2)과 벡터(§8.3)를 통합.
6. **MCP 트랜스포트는 streamable-http** — 컨테이너 분리·클라우드 배포 대응.

## 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| 풀 헥사고날/DDD | 신입 팀 학습 비용 > 이득. 계층 보일러플레이트가 4주 일정 잠식 |
| MSA | 서비스 간 통신·배포·관측 복잡도. 팀 규모 대비 과설계 |
| 벡터 DB 별도 (Chroma 등) | 운영 인스턴스 증가. pgvector로 충분하며 포트로 교체 여지 확보 |
| Agent 별도 서비스 | SSE 이벤트 중계 계층 추가 필요 → 3주차 통합 리스크 |

## 결과

- SSE 이벤트 계약은 `src/agentory/common/events.py`가 단일 소스 (docs/sse-events.md 참조).
- 브랜치 전략: default `develop`, 작업은 `feature/<기능ID>` → PR → develop.
