# 매뉴얼 인제스트 입력 (AI_RAG01)

이 디렉터리는 RAG 지식베이스에 적재할 매뉴얼 원본과 매니페스트를 두는 곳입니다. 실제 매뉴얼 파일과 `manifest.json`은 저장소에 커밋하지 않으며, 각자 팀 Google Drive에서 내려받아 이곳에 배치합니다. 저장소에는 이 설명 문서와 예시 매니페스트만 유지합니다.

## 사용 방법

먼저 매뉴얼 파일(`.pdf`·`.docx`·`.txt`·`.md`)을 이 디렉터리에 둡니다. 그다음 `manifest.example.json`을 복사해 `manifest.json`을 만들고 문서별 메타데이터를 채웁니다. 마지막으로 적재 스크립트를 실행합니다.

```bash
uv run python scripts/ingest_manuals.py
```

기본 경로는 실행 위치와 무관하게 저장소의 `data/manuals`를 가리킵니다. 다른 위치의 원본을 적재하려면 옵션으로 지정합니다.

```bash
uv run python scripts/ingest_manuals.py --manuals-dir <원본 디렉터리> [--manifest <매니페스트 경로>]
```

`--manifest`를 생략하면 `<manuals-dir>/manifest.json`을 사용하며, 매니페스트의 `file`은 항상 `--manuals-dir` 기준 상대 경로로 해석합니다.

적재는 `doc_id` 단위로 기존 청크를 지우고 다시 넣으므로 여러 번 실행해도 안전합니다. 실행에는 `OPENAI_API_KEY`와 기동 중인 PostgreSQL(pgvector)이 필요합니다.

## manifest.json 형식

각 항목은 다음 필드를 가집니다.

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `file` | 예 | data/manuals 기준 상대 파일명 |
| `doc_id` | 예 | 문서 고유 ID, `MAN-영문-숫자` 형식(예: MAN-ETC-042) |
| `equipment_type` | 아니오 | 메타 필터용 공정 유형(예: Etching) |
| `alarm_code` | 아니오 | 메타 필터용 알람 코드(예: ERR-402) |

`doc_id`는 답변 인용(NEW_TRUST01) 추출이 `MAN-영문-숫자` 정규식으로 동작하므로 이 형식을 반드시 지켜야 합니다. 형식이 어긋나면 검색 결과에 나와도 인용으로 연결되지 않습니다.
