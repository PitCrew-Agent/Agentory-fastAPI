# ADR-0001: 모듈러 모놀리스 + 멀티 엔트리포인트 모노레포

- 상태: 승인 (2026-07-02)
- 결정자: 주희정 (팀 공유 필요)

## 배경

4주 · 신입 4인 팀으로 REQ-T-01(ReAct), REQ-T-02(MCP 표준), FR 전 범위를 구현해야 한다.
MCP 서버는 표준상 별도 프로세스가 자연스러우므로 완전한 단일 프로세스는 불가능하고,
반대로 MSA는 4주 내 배포·디버깅 복잡도를 감당할 수 없다.

## 결정

1. **저장소는 하나, 실행 프로세스는 4개**: `agentory-api`(FastAPI+Agent), `mcp-realtime`,
   `mcp-knowledge`, `simulator`. uv 단일 패키지에 엔트리포인트 스크립트로 분리.
2. **Agent(Supervisor+ReAct)는 FastAPI 앱 in-process**: SSE로 Thought/Action/Observation을
   중계 계층 없이 바로 스트리밍하기 위함.
3. **모듈 경계 = 담당자 경계**: `modules/` 하위 패키지를 역할 분담표와 1:1로 맞춰
   머지 컨플릭트를 최소화.
4. **계층은 router → service → repository 3단**만 사용. 포트(인터페이스)는 비기능 요구사항
   "확장성"이 지목한 교체 지점 3곳에만 둔다:
   - LLM 프로바이더: `modules/agent/llm/base.py`
   - 임베딩: `modules/rag/embedding/base.py`
   - 벡터 스토어: `modules/rag/store/base.py`
5. **DB는 PostgreSQL 단일 인스턴스 + pgvector 확장**: 정형(§8.1, §8.2)과 벡터(§8.3)를 통합.
6. **MCP 트랜스포트는 streamable-http**: 컨테이너 분리·클라우드 배포 대응.

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

## 후속 결정 및 관련 ADR

본 ADR이 정한 골격 위에서 구현을 진행하며 내린 세부 결정은 별도 ADR로 분리했습니다.

| ADR | 주제 |
| --- | --- |
| [ADR-0002](0002-auth-session.md) | Azure AD SSO + opaque 세션 쿠키 인증 (Bearer→opaque 3회 전환) |
| [ADR-0003](0003-agent-runtime-budget.md) | 에이전트 실행 예산·컨텍스트 방어 가드레일 |
| [ADR-0004](0004-realtime-sse.md) | 실시간 SSE·알림 동기화·트윈 상태 판정 (래치 폐기) |
| [ADR-0005](0005-simulator-spc.md) | 시뮬레이터 SPC 이상 데이터 생성 모델 |
| [ADR-0006](0006-alarm-history-query.md) | 장비별 알람 이력 조회 설계 (데이터 소스·조회 형태·인덱스) |
| [ADR-0007](0007-equipment-repair.md) | 설비 수리 이력·시뮬레이터 힐 윈도우 동기화 |
| [ADR-0008](0008-cross-cutting-aop-i18n.md) | 횡단 관심사 표준화 (예외·i18n·ApiResponse·액세스 로그) |

### RAG 포트(§4)의 후속 보완

본 ADR §4에서 임베딩·벡터 스토어 포트를 3대 교체 지점으로 지정했고, 실제 어댑터는
pgvector·OpenAI 임베딩 단일 구현으로 확정했습니다. 구현 중 다음을 추가로 결정했습니다.

- 미구현 스텁 도구(`search_similar_cases`)는 `@mcp.tool()` 데코레이터를 제거해 MCP에
  노출하지 않습니다. LLM tool binding에 잡혀 불필요한 실패 Observation이 생기는 것을 막기
  위함이며, 회귀 테스트로 고정합니다 (BE_MCP04_RAG01).
- LLM 과대 입력 방어를 위해 `top_k`에 정적 상한(`MAX_TOP_K`=20) 검증을 둡니다. 운영 튜닝값
  (`RAG_SEARCH_TOP_K`·`MIN_SCORE`)과 구분되는 상수입니다.
- 임베딩 차원(1536)이 벡터 컬럼 스키마에 하드 커플링되어 있어, 임베딩 모델 교체 시 차원
  마이그레이션 + 전체 재적재가 필요합니다(유연성보다 단순성 선택).
- 검색 결과 빈 배열은 "매뉴얼 부재"가 아니라 "임계값 이상 근거 없음"을 의미하므로, 상위
  로직·프롬프트가 이를 오해하지 않도록 주의합니다.
