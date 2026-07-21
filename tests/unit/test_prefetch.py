"""RAG 모델 가중치 프리페치 단위 테스트 (AI_RAG01_CHUNK01)"""

from agentory.modules.rag import prefetch
from agentory.modules.rag.embedding.e5 import _DEFAULT_MODEL as E5_DEFAULT


def test_prefetch_excludes_alternative_runtime_weights(monkeypatch):
    calls = []
    monkeypatch.setattr(prefetch, "target_models", lambda: [E5_DEFAULT])
    monkeypatch.setattr(prefetch.os.path, "isdir", lambda path: False)
    monkeypatch.setattr(
        prefetch,
        "snapshot_download",
        lambda **kwargs: calls.append(kwargs),
    )

    assert prefetch.prefetch_models() == [E5_DEFAULT]
    assert calls[0]["repo_id"] == E5_DEFAULT
    assert "onnx/*" in calls[0]["ignore_patterns"]
    assert "openvino/*" in calls[0]["ignore_patterns"]
    assert "pytorch_model.bin" in calls[0]["ignore_patterns"]
