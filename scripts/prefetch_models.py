"""HF 가중치 수동 다운로드 (README 안내용), 설정 기반 대상만 받음

서버는 기동 시 자동 프리페치하지만, 이미 가동 중인 인스턴스나 로컬에서 미리 받을 때 사용
실행: uv run python scripts/prefetch_models.py
"""

from agentory.modules.rag.prefetch import prefetch_models

if __name__ == "__main__":
    fetched = prefetch_models()
    print(f"prefetch 완료: {fetched or '대상 없음(로컬 가중치 불필요 설정)'}")
