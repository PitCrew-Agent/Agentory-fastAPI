# e2e: 골든 시나리오 E2E (TEST_HARNESS)

실 LLM + 실 DB + 실 도구로 전체 파이프라인을 검증하는 최상위 하네스.

## 흐름 (AAA)

- Arrange: 픽스처가 설비 마스터 보강 + 대상 설비(EQP-003)에 최근 이상 텔레메트리 주입
- Act: golden 케이스의 query로 에이전트 그래프 실행 (실 LLM)
- Assert: golden `expect`와 속성 대조 (도구 호출 / 답변 키워드 / 인용)

도구는 repository를 감싼 in-process LangChain 도구로 붙여 MCP 서버 기동 없이 실행한다.
MCP HTTP 전송 계층은 contracts·integration 레이어가 별도 검증한다.

## 실행

```bash
# 실 LLM 호출이라 기본 pytest에서는 제외됨, 명시적으로 실행
uv run pytest -m llm

# 선행 조건: OPENAI_API_KEY 설정 + DB 연결 가능 (미충족 시 자동 skip)
docker compose up -d db && uv run alembic upgrade head
```

E2E는 유한 모드 결정론 시드를 전제하므로 **연속 시뮬레이터가 돌지 않는 상태**에서 실행합니다.
시뮬레이터가 켜져 있으면 시드 대상·경쟁 설비에 텔레메트리가 계속 쌓여 최근 창 라인 질의의 격리가 깨집니다.
픽스처가 시드 직전 라이브 텔레메트리 라이터를 감지하면 해당 케이스를 명시적으로 skip하니,
실행 전 `simulator` 프로세스를 정지합니다 (TODO(김건) 전용 DB 격리로 공유 자체를 제거하는 후속 과제).

## 케이스 추가

`tests/golden/*.yaml`에 케이스를 추가하면 자동으로 파라미터화되어 실행된다
(포맷은 tests/golden/README.md 참고).

## 지식(RAG) 의존 검증

`citations_include`·매뉴얼 인용 검증은 knowledge 도구가 연동될 때만 활성화된다.
현재는 knowledge 워커 미연동이라 해당 검증은 skip되며, 김건 RAG 연동 후 자동 활성화된다.
