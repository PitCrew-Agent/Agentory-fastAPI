"""문서 인제스트 실행 (AI_RAG01_CHUNK01)

실행: uv run python scripts/ingest_manuals.py [--documents-dir 경로] [--manifest 경로]
매니페스트의 각 문서를 파싱→청킹→임베딩→knowledge_collection 적재
doc_id별 재적재 멱등, OPENAI_API_KEY·DB 필요
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from agentory.modules.rag.embedding import get_embedder
from agentory.modules.rag.ingest import ingest_document
from agentory.modules.rag.prefetch import prefetch_models
from agentory.modules.rag.store.pgvector import PgVectorStore, verify_embedding_dim

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCUMENTS_DIR = ROOT / "data" / "rags"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="문서 인제스트 실행")
    parser.add_argument(
        "--documents-dir",
        type=Path,
        default=DEFAULT_DOCUMENTS_DIR,
        help="문서 원본 디렉터리 (기본: 저장소의 data/rags)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="매니페스트 경로 (기본: <documents-dir>/manifest.json)",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    documents_dir: Path = args.documents_dir
    manifest: Path = args.manifest or documents_dir / "manifest.json"
    if not manifest.exists():
        print(f"매니페스트 없음: {manifest} (manifest.example.json 참고)")
        return
    # 운영자 ingest 시 임베더·리랭커 가중치 미리 확보, 서비스 기동·질의에서 다운로드 지연 방지
    fetched = prefetch_models()
    print(f"가중치 준비: {fetched or '없음(로컬 가중치 불필요 설정)'}")
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    embedder = get_embedder()
    store = PgVectorStore()
    total = 0
    for index, entry in enumerate(entries, start=1):
        # 문서 단위로만 로그가 찍히면 장시간 무음 구간이 생겨 진행 중인지 멈춘 건지 구분 불가
        print(f"[{index}/{len(entries)}] {entry['doc_id']} 시작: {entry['file']}", flush=True)
        started = time.monotonic()
        count = await ingest_document(
            documents_dir / entry["file"],
            doc_id=entry["doc_id"],
            equipment_type=entry.get("equipment_type"),
            alarm_code=entry.get("alarm_code"),
            embedder=embedder,
            store=store,
        )
        total += count
        print(
            f"[{index}/{len(entries)}] {entry['doc_id']}: {count} chunks "
            f"({time.monotonic() - started:.1f}s)",
            flush=True,
        )
    # 매니페스트에서 빠진 구 문서(교체 전 doc_id 등) 잔존 청크 정리
    removed = await store.purge_except({entry["doc_id"] for entry in entries})
    if removed:
        print(f"매니페스트 밖 구 문서 {removed} chunks 제거")
    print(f"총 {total} chunks 적재")
    # 적재 직후 DB 벡터 컬럼 차원과 설정 정합성 확인 (B)
    await verify_embedding_dim()
    print("임베딩 차원 정합성 확인 완료")


if __name__ == "__main__":
    asyncio.run(main())
