# MCP 서버

본 시스템은 데이터 소스를 MCP(Model Context Protocol) 표준 도구로 추상화합니다(REQ-T-02).
서로 다른 도메인을 담당하는 세 서버로 구성되며, 각 서버는 에이전트 워커 1종과 1:1로 매핑됩니다.

| 서버 | 포트 | 담당 도구 | 매핑 워커 | 문서 |
| --- | --- | --- | --- | --- |
| realtime | 8101 | 센서 로그·알람 집계·설비 메타데이터 | data_analysis | [realtime.md](realtime.md) |
| knowledge | 8102 | 매뉴얼 유사도 검색 | knowledge | [knowledge.md](knowledge.md) |
| maintenance | 8103 | 설비 수리 이력·정비 요약 | maintenance | [maintenance.md](maintenance.md) |

에이전트(MCP 클라이언트)는 세 서버의 도구를 동일한 방식으로 발견·호출하여, 정형 데이터·비정형
지식·정비 이력을 종합한 답변을 생성합니다. 서버 추가는 워커 레지스트리에 `WorkerSpec` 등록만으로
그래프에 배선되며, 상세는 [docs/agent/architecture.md](../agent/architecture.md)에 있습니다.

maintenance 서버는 워커 도메인을 2종(realtime·knowledge)에서 3종으로 늘려 "현상 → 이력 → 조치"
진단 서사를 완성합니다(BE_MCP05_MAINT01). 수리 이력은 `equipment_repairs`를 재사용하므로 신규 DB
로직 없이 조회 창구만 엽니다.
