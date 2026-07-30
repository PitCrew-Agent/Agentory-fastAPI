# MCP 지식 검색 서버 (BE_MCP04_RAG01)

이 문서는 장애 조치 매뉴얼을 벡터 유사도로 검색하는 MCP 서버의 역할과 도구를 설명합니다.

## 이 서버의 역할

매뉴얼 문서를 청크·임베딩해 적재한 `knowledge_collection`(pgvector)에서 자연어 질의와 유사한
근거를 찾아 에이전트에 제공합니다. 별도 프로세스로 실행되며 streamable-http 트랜스포트(포트 8102)로
통신합니다.

```
[AI 에이전트 (MCP 클라이언트)]
        │  list_tools / call_tool (streamable-http)
        ▼
[mcp-knowledge 서버]  →  임베더(e5 로컬, 768차원) + PgVectorStore  →  pgvector(HNSW)
```

## 제공 도구

| 도구 | 기능 ID | 용도 |
| --- | --- | --- |
| `search_manuals` | BE_MCP04_RAG01 | 자연어 질의 임베딩 후 매뉴얼 벡터 컬렉션 유사도 Top-K 검색 |

### search_manuals

- 입력: `query`(필수), `top_k`(기본 `rag_search_top_k`=3, 최대치 상한), `equipment_type`(선택)
- 출력: `[{doc_id, content, score}]`, `score`는 코사인 유사도(1 - cosine_distance)
- 유사도 임계값 `rag_search_min_score`(기본 0.2) 미달 결과는 제외합니다. 빈 배열은 매뉴얼 부재가
  아니라 현재 질의 기준 임계값 이상 근거가 없음을 뜻합니다.

## equipment_type 필터 폴백 (feature/128 #129)

`equipment_type`은 적재 메타(현재 전 청크 `Etching`)로 검색을 좁히는 선택 필터입니다. 그런데
knowledge 워커가 라이브에서 적재 태그와 다른 값(예 `EQP-ETC`)을 추측해 넘기면, exact-match 필터가
0건을 반환해 **매뉴얼을 못 읽어오는** 문제가 있었습니다. 벡터 검색·임베딩·임계값 자체는 정상입니다.

`PgVectorStore.search`가 필터 결과 0건이면 **미필터로 재검색(폴백)** 하도록 고쳐, 잘못된 태그로
근거를 놓치지 않게 했습니다. 매칭이 있을 때는 필터를 유지해 유형 특화 검색을 보존합니다.

| `equipment_type` | 기존 | 현재 |
| --- | --- | --- |
| `Etching` (실제 태그) | 3건 | 3건 |
| `EQP-ETC` (워커 추측) | 0건 | 3건 (폴백) |
| 미지정(None) | 3건 | 3건 |

## 조회 로직 공용화

임베딩·벡터 검색 로직은 `agentory.modules.rag`(embedding·store)에 구현되어 있으며, MCP 서버는
파라미터 검증·임계값 필터만 담당합니다. 장애 대응 계획(incident)도 이 서버의 도구를 재사용합니다.

## 실행 방법

```bash
uv run mcp-knowledge          # streamable-http, 포트 8102
docker compose up -d mcp-knowledge
```

매뉴얼 적재(ingest)와 임베딩 API 키가 선행되어야 합니다.

## 관련 문서

- 에이전트 아키텍처: [../agent/architecture.md](../agent/architecture.md)
- 데이터베이스 설계(knowledge_collection): [../database.md](../database.md)
