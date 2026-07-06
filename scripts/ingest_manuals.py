"""매뉴얼 인제스트 실행 (AI_RAG01_CHUNK01)

실행: uv run python scripts/ingest_manuals.py [--manuals-dir 경로] [--manifest 경로]
매니페스트의 각 문서를 파싱→청킹→임베딩→knowledge_collection 적재
doc_id별 재적재 멱등, OPENAI_API_KEY·DB 필요
"""

import argparse
import asyncio
import json
from pathlib import Path

from agentory.modules.rag.embedding.openai import get_embedder
from agentory.modules.rag.ingest import ingest_document
from agentory.modules.rag.store.pgvector import PgVectorStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANUALS_DIR = ROOT / "data" / "manuals"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="매뉴얼 인제스트 실행")
    parser.add_argument(
        "--manuals-dir",
        type=Path,
        default=DEFAULT_MANUALS_DIR,
        help="매뉴얼 원본 디렉터리 (기본: 저장소의 data/manuals)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="매니페스트 경로 (기본: <manuals-dir>/manifest.json)",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    manuals_dir: Path = args.manuals_dir
    manifest: Path = args.manifest or manuals_dir / "manifest.json"
    if not manifest.exists():
        print(f"매니페스트 없음: {manifest} (manifest.example.json 참고)")
        return
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    embedder = get_embedder()
    store = PgVectorStore()
    total = 0
    for entry in entries:
        count = await ingest_document(
            manuals_dir / entry["file"],
            doc_id=entry["doc_id"],
            equipment_type=entry.get("equipment_type"),
            alarm_code=entry.get("alarm_code"),
            embedder=embedder,
            store=store,
        )
        total += count
        print(f"{entry['doc_id']}: {count} chunks")
    print(f"총 {total} chunks 적재")


if __name__ == "__main__":
    asyncio.run(main())
