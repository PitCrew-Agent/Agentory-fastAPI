"""HF 가중치 프리페치, 설정에 맞는 로컬 모델만 다운로드 (서버 기동·수동 공용)

provider가 openai/none이면 로컬 가중치가 없으므로 대상에서 제외
HF_HOME 캐시에 저장되므로 볼륨(EFS 등) 마운트 시 최초 1회만 받고 이후 기동은 캐시 사용
"""

import os

from huggingface_hub import snapshot_download

from agentory.core.config import get_settings
from agentory.modules.rag.embedding.e5 import _DEFAULT_MODEL as E5_DEFAULT
from agentory.modules.rag.rerank import _DEFAULT_MODELS as RERANK_MODELS

# PyTorch 로더가 안 쓰는 다른 프레임워크 가중치 형식은 제외해 다운로드·용량 절감
_IGNORE = [
    "onnx/*",
    "openvino/*",
    "*.onnx",
    "*.h5",
    "*.msgpack",
    "*.tflite",
]
_SAFETENSORS_MODELS = {E5_DEFAULT, *RERANK_MODELS.values()}


def target_models() -> list[str]:
    """현재 설정(embedding_provider·reranker_provider)이 실제로 쓰는 로컬 모델 ID 목록"""
    settings = get_settings()
    models: list[str] = []
    if settings.embedding_provider == "e5":
        models.append(settings.embedding_model or E5_DEFAULT)
    if settings.reranker_provider in RERANK_MODELS:
        models.append(settings.reranker_model or RERANK_MODELS[settings.reranker_provider])
    return models


def prefetch_models() -> list[str]:
    """대상 모델 가중치를 HF_HOME 캐시로 다운로드, 반환: 실제 다운로드한 모델 목록

    로컬 경로(디렉터리)로 지정된 모델은 다운로드 없이 그 경로에서 로드하므로 건너뜀(폐쇄망 대응)
    """
    fetched: list[str] = []
    for name in target_models():
        if os.path.isdir(name):
            continue
        ignore_patterns = [*_IGNORE]
        if name in _SAFETENSORS_MODELS:
            ignore_patterns.append("pytorch_model.bin")
        snapshot_download(repo_id=name, ignore_patterns=ignore_patterns)
        fetched.append(name)
    return fetched
