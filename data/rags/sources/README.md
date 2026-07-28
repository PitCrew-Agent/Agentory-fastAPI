# RAG 매뉴얼 편집 원본

`data/rags`의 PDF 지식 문서를 생성한 마크다운 원본입니다. PDF는 바이너리라 직접 수정이 어렵기 때문에, 문서 내용을 바꿀 때는 이 마크다운을 고친 뒤 PDF로 다시 렌더링합니다. 이 디렉터리는 `manifest.json`에 포함되지 않으므로 RAG 적재 대상이 아닙니다.

## 원본과 생성 PDF 대응

| 소스 마크다운 | 생성 PDF | doc_id |
| --- | --- | --- |
| Equipment_Condition_Guideline_v1_0_KO.md | ../Equipment_Condition_Guideline_v1_0_KO.pdf | MAN-GDL-001 |
| Etching_Equipment_SOP_v2_0_KO.md | ../Etching_Equipment_SOP_v2_0_KO.pdf | MAN-SOP-001 |
| MPS8600_Maintenance_Manual_Rev2_1_KO.md | ../MPS8600_Maintenance_Manual_Rev2_1_KO.pdf | MAN-TM-8600 |

## PDF 재생성

마크다운 상단 `<!-- PDF 렌더링 지시 -->` 주석의 머리말·꼬리말 규격을 따르며, 마크다운→HTML→PDF 변환기(예: `xhtml2pdf`)에 한글 폰트를 지정해 렌더링합니다. 재생성 후 `data/rags`의 해당 PDF를 교체하고, 내용이 바뀌었으면 `scripts/ingest_manuals.py`로 재적재합니다.
