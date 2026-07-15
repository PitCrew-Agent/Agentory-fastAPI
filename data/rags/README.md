# 문서 인제스트 입력 (AI_RAG01)

이 디렉터리는 RAG 지식베이스에 적재할 문서 원본과 매니페스트를 두는 곳입니다. 지원 문서(`.pdf`·`.docx`·`.txt`·`.md`)와 `manifest.json`은 저장소에 함께 커밋해 팀원이 별도 내려받기 없이 공유합니다. 새 문서를 추가하고 커밋한 뒤 적재 스크립트를 실행하면 새로 청킹해 pgvector에 재적재합니다.

## 사용 방법

먼저 문서 파일(`.pdf`·`.docx`·`.txt`·`.md`)을 이 디렉터리에 둡니다. 그다음 `manifest.example.json`을 복사해 `manifest.json`을 만들고 문서별 메타데이터를 채웁니다. 마지막으로 적재 스크립트를 실행합니다.

```bash
uv run python scripts/ingest_manuals.py
```

기본 경로는 실행 위치와 무관하게 저장소의 `data/documents`를 가리킵니다. 다른 위치의 원본을 적재하려면 옵션으로 지정합니다.

```bash
uv run python scripts/ingest_manuals.py --documents-dir <원본 디렉터리> [--manifest <매니페스트 경로>]
```

`--manifest`를 생략하면 `<documents-dir>/manifest.json`을 사용하며, 매니페스트의 `file`은 항상 `--documents-dir` 기준 상대 경로로 해석합니다.

적재는 `doc_id` 단위로 기존 청크를 지우고 다시 넣으므로 여러 번 실행해도 안전합니다. 실행에는 `OPENAI_API_KEY`와 기동 중인 PostgreSQL(pgvector)이 필요합니다.

## manifest.json 형식

각 항목은 다음 필드를 가집니다.

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `file` | 예 | data/documents 기준 상대 파일명 |
| `doc_id` | 예 | 문서 고유 ID, `MAN-영문-숫자` 형식(예: MAN-ETC-042) |
| `equipment_type` | 아니오 | 메타 필터용 공정 유형(예: Etching) |
| `alarm_code` | 아니오 | 메타 필터용 알람 코드(예: ERR-402) |

`doc_id`는 답변 인용(NEW_TRUST01) 추출이 `MAN-영문-숫자` 정규식으로 동작하므로 이 형식을 반드시 지켜야 합니다. 형식이 어긋나면 검색 결과에 나와도 인용으로 연결되지 않습니다.

`alarm_code`는 문서 단위 값이 모든 청크에 복사되므로, 하나의 문서가 여러 알람을 다루는 경우(예: SOP 전체) 특정 코드를 넣으면 다른 알람 섹션의 청크에 오표기가 발생합니다. 이런 문서는 `null`로 비워 둡니다. 섹션 단위 메타 부여는 추후 청킹 개선 과제입니다.
