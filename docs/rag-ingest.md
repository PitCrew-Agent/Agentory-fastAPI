# RAG 인제스트 환경 구축·재적재 가이드

매뉴얼을 벡터 DB에 적재하고 검색하는 파이프라인의 구성과 실행 절차를 설명합니다.

## 파이프라인 구성

| 단계 | 구현 | 설정 |
|---|---|---|
| 변환 | Docling `DocumentConverter` | 지원 형식: pdf, docx, md, txt |
| 청킹 | Docling `HybridChunker` | `CHUNK_MAX_TOKENS=1024` (tiktoken cl100k_base 기준) |
| 정규화 | `rag/ingest.py`의 `normalize` | NFC·제로폭 문자·행말 공백 정리 |
| 임베딩 | KURE-v1 (sentence-transformers) | `EMBEDDING_PROVIDER=kure-v1`, 1024차원 |
| 적재 | pgvector `knowledge_collection` | HNSW 인덱스, 코사인 거리 |

청킹은 문자 고정폭이 아니라 문서 구조 기반입니다. `max_tokens`는 고정 크기가 아니라 초과 시
재분할하는 상한이며 overlap 개념이 없습니다. 소속 섹션의 heading이 청크 앞에 맥락으로 붙습니다.

## 최초 설치

의존성은 기본 그룹에 포함되어 있어 별도 옵션이 필요하지 않습니다. Docling과
sentence-transformers가 함께 설치됩니다.

```bash
uv sync
cp .env.example .env          # EMBEDDING_PROVIDER=kure-v1 기본값 그대로 사용
docker compose up -d db
uv run alembic upgrade head   # 벡터 컬럼 1024차원으로 정렬
```

KURE-v1 모델(약 2.2GB)은 최초 임베딩 시 Hugging Face 캐시로 내려받습니다. 다운로드는 한 번만
일어나고 이후에는 캐시를 재사용합니다.

## 매뉴얼 적재

`data/manuals/manifest.json`에 문서 목록을 정의한 뒤 실행합니다. 형식은
`data/manuals/manifest.example.json`을 참고하시기 바랍니다.

**적재 전에 API·MCP 서버를 모두 내려야 합니다.** Docling은 PDF 파싱에 레이아웃 인식과 OCR
신경망을 사용하는데, 메모리와 CPU가 부족하면 뒤쪽 페이지의 추론이 실패합니다. 문제는 이때
예외를 던지지 않고 해당 페이지를 빈 페이지로 처리한다는 점입니다. 즉 **에러 없이 조용히 문서 일부가
누락된 채 적재가 끝납니다.** 특히 MCP knowledge 서버는 KURE-v1 모델(약 2.2GB)을 상주시키므로
적재와 함께 띄워두면 이 현상이 재현됩니다.

```bash
docker compose up -d db                      # DB만 실행
uv run python scripts/ingest_manuals.py      # API·MCP 서버는 내린 상태
```

적재가 끝나면 출력된 청크 수를 확인하시기 바랍니다. 같은 문서인데 이전보다 청크 수가 크게 줄었다면
파싱 누락을 의심해야 합니다. 참고로 `Etching_Equipment_SOP_v1_6.pdf`(20페이지)의 정상 결과는
32청크입니다.

`doc_id` 단위로 기존 청크를 삭제한 뒤 삽입하므로 재실행해도 중복이 쌓이지 않습니다.

## 전량 재적재가 필요한 경우

다음 중 하나라도 바뀌면 기존 벡터를 그대로 쓸 수 없어 전량 재적재가 필요합니다.

- 임베딩 모델 교체: 벡터 공간 자체가 달라집니다. 차원까지 달라지면 컬럼 마이그레이션도 선행해야 합니다.
- 청킹 설정 변경(`CHUNK_MAX_TOKENS` 등): 청크 경계가 달라져 기존 청크와 대응되지 않습니다.
- 정규화 규칙 변경: 임베딩 입력 텍스트가 달라집니다.

마이그레이션 0022는 벡터 컬럼을 1024차원으로 바꾸면서 `knowledge_collection`을 비우고 HNSW 인덱스를
재생성합니다. 따라서 `alembic upgrade head` 이후에는 반드시 재적재를 수행해야 검색이 동작합니다.

## OpenAI 임베딩으로 되돌리기

로컬 추론이 곤란한 환경에서는 provider를 바꿀 수 있습니다. 다만 차원이 1536으로 달라지므로 컬럼
마이그레이션을 되돌리고 재적재해야 합니다.

```bash
uv run alembic downgrade 0021_worklog_plan_completion
```

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_DIM=1536
OPENAI_API_KEY=...
```

이후 `scripts/ingest_manuals.py`를 다시 실행합니다. 검색 품질은 KURE-v1 대비 낮습니다
(golden v4 core hit@1 0.396 대 0.451).

## 검색 서버

MCP knowledge 서버는 기동 시 임베딩 모델을 선로드(warmup)합니다. 첫 검색이 모델 로드 지연을
떠안지 않도록 하기 위한 것으로, 기동에 수십 초가 걸릴 수 있습니다.

```bash
uv run mcp-knowledge
```
